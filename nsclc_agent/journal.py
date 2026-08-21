"""Content-addressed call journal for reproducible replay.

A checkpoint records what the state *was*; an audit asks "given exactly this
evidence, why was that recommendation released" — which requires re-running
against the same tool results and model outputs, not whatever the endpoints
return today. Every host call (tool execution, LLM completion) is appended as
``(seq, kind, req_hash, result)``; replaying feeds the recorded results back in
order.

Three properties, inherited from the YaoBi/Grok-Build journal design:

* **A journal is never authorisation.** Authorisation is re-derived live by the
  broker before the recorded result is touched, so a journal recorded under one
  role cannot hand a different role a result its policy denies.
* **Divergence is latched.** Agents wrap model calls in broad ``except`` so
  they can fall back to deterministic paths — which would silently convert a
  failed replay into a "fell back to rules" note. Divergences are therefore
  recorded on the journal itself and the runner fails closed at the end.
* **Exhaustion ≠ divergence.** A different call at a recorded position is a
  hard failure; running past the end of the journal is only a warning that the
  tail was live.
"""

from __future__ import annotations

import hashlib
import json
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any

MAX_JOURNAL_BYTES = 64 * 1024 * 1024
MAX_JOURNAL_ENTRIES = 10_000

KIND_TOOL = "tool"
KIND_LLM = "llm"


class JournalError(RuntimeError):
    """Base class for journal faults. Never swallowed silently."""


class JournalDivergence(JournalError):
    """The replayed run issued a different call than the one recorded."""

    def __init__(self, seq: int, kind: str, expected: str, actual: str, detail: str = "") -> None:
        super().__init__(
            f"replay divergence at seq {seq} ({kind}): recorded request "
            f"{expected[:12]} but the run issued {actual[:12]}. "
            f"The journal, the code or the model changed. {detail}".strip()
        )
        self.seq = seq
        self.kind = kind
        self.expected = expected
        self.actual = actual


@dataclass
class JournalEntry:
    seq: int
    kind: str
    req_hash: str
    result: Any
    #: Human-readable label — tool or model name. Not part of the hash.
    label: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {"seq": self.seq, "kind": self.kind, "req_hash": self.req_hash,
                "result": self.result, "label": self.label}

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "JournalEntry":
        try:
            return cls(
                seq=int(payload["seq"]), kind=str(payload["kind"]),
                req_hash=str(payload["req_hash"]), result=payload.get("result"),
                label=str(payload.get("label", "")),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise JournalError(f"malformed journal entry: {exc}") from exc


def canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False, default=str)


def request_hash(kind: str, name: str, payload: Any) -> str:
    return hashlib.sha256(
        canonical_json({"kind": kind, "name": name, "payload": payload}).encode("utf-8")
    ).hexdigest()


class Journal:
    """Append-only call journal, usable for recording or replaying."""

    def __init__(
        self,
        path: str | Path | None = None,
        *,
        mode: str = "record",
        entries: list[JournalEntry] | None = None,
        meta: dict[str, Any] | None = None,
    ) -> None:
        if mode not in ("record", "replay", "off"):
            raise JournalError(f"unknown journal mode {mode!r}; expected record|replay|off")
        self.path = Path(path) if path else None
        self.mode = mode
        # A fresh recording must not append to an old file: appended entries
        # restart the sequence at 1 and permanently corrupt the journal.
        if self.mode == "record" and self.path is not None \
                and not entries and self.path.exists():
            self.path.unlink()
        self.entries: list[JournalEntry] = list(entries or [])
        self.meta: dict[str, Any] = dict(meta or {})
        self._cursor = 0
        self._lock = threading.RLock()
        self.replayed = 0
        self.live_after_exhaustion = 0
        self.divergences: list[dict[str, Any]] = []

    # ------------------------------------------------------------------ record
    def write_meta(self, meta: dict[str, Any]) -> None:
        with self._lock:
            self.meta.update(meta)
            if self.path is not None and self.mode == "record":
                self.path.parent.mkdir(parents=True, exist_ok=True)
                with self.path.open("a", encoding="utf-8") as handle:
                    handle.write(json.dumps({"_meta": self.meta}, ensure_ascii=False) + "\n")

    def record(self, kind: str, name: str, payload: Any, result: Any) -> int:
        with self._lock:
            if self.mode != "record":
                return -1
            if len(self.entries) >= MAX_JOURNAL_ENTRIES:
                raise JournalError(
                    f"journal full at {MAX_JOURNAL_ENTRIES} entries"
                )
            entry = JournalEntry(
                seq=len(self.entries) + 1, kind=kind,
                req_hash=request_hash(kind, name, payload), result=result, label=name,
            )
            self.entries.append(entry)
            if self.path is not None:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                with self.path.open("a", encoding="utf-8") as handle:
                    handle.write(json.dumps(entry.to_dict(), ensure_ascii=False) + "\n")
            return entry.seq

    # ------------------------------------------------------------------ replay
    def next_result(self, kind: str, name: str, payload: Any) -> tuple[bool, Any]:
        """Return ``(hit, result)`` for the next recorded call.

        ``hit`` is False when not replaying or when the journal is exhausted —
        the caller then executes the call live. A *mismatch* is not a miss: it
        raises :class:`JournalDivergence` and latches the divergence.
        """
        with self._lock:
            if self.mode != "replay":
                return False, None
            if self._cursor >= len(self.entries):
                self.live_after_exhaustion += 1
                return False, None
            entry = self.entries[self._cursor]
            actual = request_hash(kind, name, payload)
            if entry.kind != kind or entry.req_hash != actual:
                same_call = entry.kind == kind and entry.label == name
                detail = (
                    f"same call {kind}:{name} but different arguments "
                    f"(the case, facts or prompt changed)"
                    if same_call
                    else f"recorded {entry.kind}:{entry.label}, issued {kind}:{name}"
                )
                self.divergences.append({
                    "seq": entry.seq,
                    "recorded": f"{entry.kind}:{entry.label}",
                    "issued": f"{kind}:{name}",
                    "differs_by": "arguments" if same_call else "call",
                    "recorded_hash": entry.req_hash[:16],
                    "issued_hash": actual[:16],
                })
                raise JournalDivergence(entry.seq, entry.kind, entry.req_hash, actual, detail=detail)
            self._cursor += 1
            self.replayed += 1
            return True, entry.result

    @property
    def diverged(self) -> bool:
        with self._lock:
            return bool(self.divergences)

    def summary(self) -> dict[str, Any]:
        with self._lock:
            return {
                "mode": self.mode,
                "entries": len(self.entries),
                "replayed": self.replayed,
                "live_after_exhaustion": self.live_after_exhaustion,
                "diverged": self.diverged,
                "meta": dict(self.meta),
            }

    # -------------------------------------------------------------------- load
    @classmethod
    def load(cls, path: str | Path, *, mode: str = "replay") -> "Journal":
        file_path = Path(path)
        if not file_path.is_file():
            raise JournalError(f"journal not found: {file_path}")
        size = file_path.stat().st_size
        if size > MAX_JOURNAL_BYTES:
            raise JournalError(f"journal is {size} bytes, over the cap; refusing to load")

        entries: list[JournalEntry] = []
        meta: dict[str, Any] = {}
        for line_no, line in enumerate(
            file_path.read_text(encoding="utf-8").splitlines(), start=1
        ):
            line = line.strip()
            if not line:
                continue
            try:
                payload = json.loads(line)
            except json.JSONDecodeError as exc:
                # A torn final line (crash mid-write) is truncated, not fatal;
                # a torn *interior* line is corruption.
                if line_no >= _count_lines(file_path):
                    break
                raise JournalError(f"corrupt journal line {line_no}: {exc}") from exc
            if isinstance(payload, dict) and "_meta" in payload:
                meta.update(payload["_meta"] or {})
                continue
            entry = JournalEntry.from_dict(payload)
            if entry.seq != len(entries) + 1:
                raise JournalError(
                    f"journal sequence gap: expected {len(entries) + 1}, got {entry.seq}"
                )
            entries.append(entry)
        return cls(path=file_path, mode=mode, entries=entries, meta=meta)


def _count_lines(path: Path) -> int:
    return len(path.read_text(encoding="utf-8").splitlines())


class JournaledLLM:
    """Wraps an LLM client so completions are recorded / replayed.

    ``offline=True`` (replay with no live model) makes journal exhaustion an
    error rather than a silent degradation to the deterministic path.
    ``meta_prefix`` separates the main reasoning model from the vision model —
    both ride the same journal, but each mirrors its own recorded identity and
    availability on replay.
    """

    def __init__(
        self,
        inner: Any,
        journal: Journal,
        *,
        offline: bool = False,
        meta_prefix: str = "llm",
    ) -> None:
        self.inner = inner
        self.journal = journal
        self.offline = offline
        self.meta_prefix = meta_prefix

    def _meta(self, key: str, default: Any) -> Any:
        return self.journal.meta.get(f"{self.meta_prefix}_{key}", default)

    @property
    def name(self) -> str:
        if self.journal.mode == "replay":
            return str(self._meta("provider", getattr(self.inner, "name", "journal")))
        return getattr(self.inner, "name", "unknown")

    @property
    def model(self) -> str:
        if self.journal.mode == "replay":
            return str(self._meta("model", getattr(self.inner, "model", "journal")))
        return getattr(self.inner, "model", "unknown")

    @property
    def supports_vision(self) -> bool:
        if self.journal.mode == "replay":
            return bool(self._meta("supports_vision",
                                   getattr(self.inner, "supports_vision", False)))
        return bool(getattr(self.inner, "supports_vision", False))

    @property
    def available(self) -> bool:
        if self.journal.mode == "replay":
            # Mirror what the recording ran with: a run recorded without a
            # model must replay without one, or the replay issues LLM calls
            # the journal never saw and diverges on the very first entry.
            key = f"{self.meta_prefix}_available"
            if key in self.journal.meta:
                return bool(self.journal.meta[key]) or bool(
                    getattr(self.inner, "available", False))
            return bool(self.journal.entries) or bool(
                getattr(self.inner, "available", False))
        return bool(getattr(self.inner, "available", False))

    def chat(self, messages: list[dict[str, Any]], **kwargs: Any):
        from .llm.base import LLMError, LLMResponse

        tools = kwargs.get("tools") or []
        payload = {
            "messages": messages,
            "tools": [t.name for t in tools],
            "temperature": kwargs.get("temperature", 0.0),
            "max_tokens": kwargs.get("max_tokens", 1024),
            "model": self.model,
        }
        hit, recorded = self.journal.next_result(KIND_LLM, self.model, payload)
        if hit:
            return LLMResponse.from_journal(recorded)
        if self.journal.mode == "replay" and self.offline:
            raise LLMError(
                "replay journal exhausted and no live model configured — "
                "this run cannot be served offline"
            )
        response = self.inner.chat(messages, **kwargs)
        self.journal.record(KIND_LLM, self.model, payload, response.to_journal())
        return response
