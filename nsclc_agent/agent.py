"""The NSCLC agent orchestrator.

Pipeline for one case:

    case ──▶ deterministic TNM-9 staging ──▶ module routing ──▶ prompt assembly
         ──▶ provider completion ──▶ structured AgentResult

The stage group is computed symbolically and *injected* into the system prompt
so the model reasons within a verified stage rather than re-deriving (and
possibly hallucinating) it. Every result carries full provenance for auditing.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field, replace
from typing import Any, Optional

from .case import Case
from .config import Config, load_config
from .consult import (
    STATUS_COMPLETE,
    STATUS_EXHAUSTED,
    STATUS_GATHERING,
    STATUS_READY,
    ConsultSession,
    ConsultTurn,
    extract,
    is_sufficient,
)
from .evidence import (
    RetrievalResult,
    Retriever,
    build_retriever,
    evidence_block,
    query_plan,
)
from .imaging import ImagingFindings, ImagingReader
from .prompts import PromptModule, load_module
from .providers.base import GenerationParams, LLMProvider, LLMResponse, Message
from .providers.registry import build_provider
from .staging import (
    RouteResult,
    StageResult,
    StagingError,
    normalize_stage_group,
    route,
    stage_from_strings,
    stage_groups_compatible,
)
from .staging.tnm import M_CATEGORIES, N_CATEGORIES, T_CATEGORIES, TNM

_EDU_DISCLAIMER = (
    "EDUCATIONAL / RESEARCH USE ONLY. This system generates decision-support "
    "and training material for teaching and evaluation. It is not a medical "
    "device and its output must not be used for real patient care without "
    "review by a qualified multidisciplinary oncology team."
)


def _in_vocabulary(value: str, vocab: tuple[str, ...]) -> bool:
    """Case/whitespace-insensitive membership in a descriptor vocabulary."""
    key = "".join(str(value).split()).upper()
    return any(key == v.upper() for v in vocab)


def _same_descriptor(a: str, b: str) -> bool:
    """Compare two TNM descriptors ignoring case and whitespace."""
    return "".join(str(a).split()).upper() == "".join(str(b).split()).upper()


@dataclass
class AgentResult:
    case_id: Optional[str]
    staging: Optional[dict]
    routing: dict
    module_key: Optional[str]
    provider: Optional[str]
    response: Optional[LLMResponse]
    flags: list[str] = field(default_factory=list)
    error: Optional[str] = None
    #: model-proposed radiographic descriptors, if films were read (perception
    #: layer). Always recorded as UNVERIFIED provenance, never as ground truth.
    imaging: Optional[dict] = None
    #: literature actually retrieved for this run (or the recorded fact that
    #: none was), so a reader can tell a cited identifier from a recalled one.
    evidence: Optional[dict] = None
    #: consultation provenance when the case came from an interview: what was
    #: asked, what was answered, and what is still unknown.
    consult: Optional[dict] = None

    def to_dict(self) -> dict:
        return {
            "case_id": self.case_id,
            "staging": self.staging,
            "routing": self.routing,
            "module_key": self.module_key,
            "provider": self.provider,
            "imaging": self.imaging,
            "evidence": self.evidence,
            "consult": self.consult,
            "response": self.response.to_dict() if self.response else None,
            "flags": self.flags,
            "error": self.error,
            "disclaimer": _EDU_DISCLAIMER,
        }


class NSCLCAgent:
    def __init__(
        self,
        config: Optional[Config] = None,
        *,
        allow_fallback_module: bool = True,
        vision_provider: Optional[str] = None,
        retriever: Optional[Retriever] = None,
        lang: str = "en",
    ):
        self.config = config or load_config()
        self.allow_fallback_module = allow_fallback_module
        self.vision_provider = vision_provider or self.config.vision_provider
        self.lang = lang
        self.retriever = retriever if retriever is not None \
            else build_retriever(self.config.evidence)
        self._provider_cache: dict[str, LLMProvider] = {}

    # -- provider handling --------------------------------------------------

    def get_provider(self, name: Optional[str] = None) -> LLMProvider:
        name = name or self.config.default_provider
        if name not in self._provider_cache:
            cfg = self.config.provider_config(name)
            self._provider_cache[name] = build_provider(
                name, cfg, defaults=self.config.generation
            )
        return self._provider_cache[name]

    def resolve_vision_provider_name(
        self, override: Optional[str] = None
    ) -> str:
        """Pick the provider that reads films.

        Preference: explicit override → configured ``vision_provider`` → any
        provider flagged vision-capable → the default provider.
        """
        if override:
            return override
        if self.vision_provider:
            return self.vision_provider
        for name in self.config.provider_names():
            cfg = self.config.provider_config(name)
            if isinstance(cfg, dict) and cfg.get("vision"):
                return name
        return self.config.default_provider

    # -- perception / imaging (vision) --------------------------------------

    def read_imaging(
        self,
        case: Case,
        *,
        provider: Optional[str] = None,
        params: Optional[GenerationParams] = None,
    ) -> ImagingFindings:
        """Read the case's films into model-PROPOSED radiographic descriptors.

        The returned descriptors are UNVERIFIED observations; they are fed back
        into the deterministic staging engine and cross-checked — they never
        assign the stage group themselves.
        """
        vname = self.resolve_vision_provider_name(provider)
        prov = self.get_provider(vname)
        reader = ImagingReader(prov)
        return reader.read(case.images, context=case.presentation, params=params)

    def _ingest_imaging(
        self, case: Case, findings: ImagingFindings
    ) -> tuple[Case, list[str]]:
        """Fold model-proposed descriptors into the case, safely.

        Human/pathologic descriptors stay authoritative and are only
        *cross-checked* (discordance is flagged, never overridden). Descriptors
        the case is missing are *seeded* from the proposal and flagged as
        radiographic/unverified so the deterministic engine can still stage.
        """
        flags: list[str] = []
        proposed: dict[str, Optional[str]] = {}
        for desc, vocab in (("t", T_CATEGORIES), ("n", N_CATEGORIES),
                            ("m", M_CATEGORIES)):
            value = getattr(findings, f"candidate_{desc}")
            if value and not _in_vocabulary(value, vocab):
                # The reader answered outside the 9th-edition vocabulary (e.g.
                # the ambiguous "N2" it was told not to use). Drop it rather
                # than feeding an unusable descriptor to the engine, and say so.
                flags.append(
                    f"IMAGING_DESCRIPTOR_INVALID[{desc.upper()}]: the film "
                    f"reader proposed {value!r}, which is not a 9th-edition "
                    f"{desc.upper()} category — discarded. Valid: "
                    f"{', '.join(vocab)}."
                )
                value = None
            proposed[desc] = value
        seeded: dict[str, str] = {}
        for desc in ("t", "n", "m"):
            human = getattr(case, desc)
            prop = proposed[desc]
            if human and prop:
                if not _same_descriptor(human, prop):
                    flags.append(
                        f"IMAGING_DISCORDANCE[{desc.upper()}]: case says "
                        f"{human} but the film reader proposed {prop} — the "
                        f"case value is used for staging; reconcile before use."
                    )
            elif not human and prop:
                seeded[desc] = prop
        if seeded:
            new_case = replace(case, **seeded)
            flags.append(
                "RADIOGRAPHIC_TNM_PROPOSED: staged from model-proposed "
                "descriptors ("
                + ", ".join(f"c{d.upper()}={v}" for d, v in seeded.items())
                + ") — provisional cTNM, pending radiologist/pathology "
                "confirmation."
            )
        else:
            new_case = case
        hint = self._next_step_hint(new_case, findings)
        if hint:
            flags.append(hint)
        return new_case, flags

    def _next_step_hint(
        self, case: Case, findings: ImagingFindings
    ) -> Optional[str]:
        """A minimal value-of-information seed: name the test that resolves the
        descriptor left unresolved after film reading."""
        missing = []
        if not case.t:
            missing.append("T")
        if not case.n:
            missing.append("N")
        if not case.m:
            missing.append("M")
        suggestions = {
            "T": "dedicated contrast CT / bronchoscopy to fix the T descriptor",
            "N": "EBUS-TBNA of suspicious stations to resolve N (single- vs "
                 "multi-station changes IIIA↔IIIB)",
            "M": "PET-CT + brain MRI to confirm/exclude distant metastasis (M)",
        }
        if not missing:
            return None
        steps = "; ".join(suggestions[d] for d in missing)
        return (
            f"NEXT_STEP_SUGGESTED: descriptor(s) {', '.join(missing)} not "
            f"established from the films — {steps}."
        )

    # -- staging + routing (no LLM) -----------------------------------------

    def resolve_stage(self, case: Case) -> tuple[Optional[StageResult], list[str]]:
        flags: list[str] = []
        if case.has_tnm():
            m_assumed = not case.m
            try:
                result = stage_from_strings(case.t, case.n, case.m or "M0")
            except StagingError as exc:
                flags.append(f"STAGING_ERROR: {exc}")
                return None, flags
            if m_assumed:
                # Never let "no M supplied" quietly become "no metastases":
                # that assumption alone separates a curative from a palliative
                # pathway, so it is recorded in provenance every time.
                flags.append(
                    "M_ASSUMED_M0: no M category was supplied — staged as M0 "
                    "by assumption. Confirm the metastatic workup (PET-CT + "
                    "brain MRI) before acting on a curative-intent stage."
                )
                result.descriptor_notes.append(
                    "M0 was assumed, not supplied — the stage group is "
                    "provisional pending metastatic staging."
                )
            if case.stage_group and not stage_groups_compatible(
                case.stage_group, result.stage_group
            ):
                flags.append(
                    f"STAGE_MISMATCH: provided {case.stage_group} but TNM "
                    f"{result.tnm} computes to {result.stage_group} (using "
                    f"computed value)"
                )
            return result, flags
        if case.stage_group:
            norm = normalize_stage_group(case.stage_group)
            if norm is None:
                flags.append(
                    f"STAGE_LABEL_UNRECOGNIZED: {case.stage_group!r} is not a "
                    f"recognizable stage group; supply a TNM triple or a "
                    f"label such as 'IIIB'"
                )
                return None, flags
            note = (f"STAGE_FROM_LABEL: no TNM provided; using the given "
                    f"stage_group without deterministic verification")
            if norm != str(case.stage_group).strip():
                note += f" (normalized {case.stage_group!r} → {norm!r})"
            flags.append(note)
            return (
                StageResult(
                    tnm=TNM(t=case.t or "TX", n=case.n or "NX",
                            m=case.m or "MX"),
                    stage_group=norm,
                ),
                flags,
            )
        flags.append("STAGE_UNRESOLVED: no TNM and no stage_group provided")
        return None, flags

    def route_case(self, case: Case) -> tuple[
        Optional[StageResult], Optional[RouteResult], list[str]
    ]:
        stage_result, flags = self.resolve_stage(case)
        if stage_result is None:
            return None, None, flags
        route_result = route(stage_result.stage_group)
        if not route_result.available:
            flags.append(
                f"MODULE_UNAVAILABLE: no dedicated module for stage "
                f"{stage_result.stage_group}"
                + (f" — {route_result.note}" if route_result.note else "")
            )
        return stage_result, route_result, flags

    # -- prompt assembly ----------------------------------------------------

    def _staging_preamble(self, stage_result: StageResult) -> str:
        payload = stage_result.to_dict()
        lines = [
            "=== DETERMINISTIC STAGING (computed by a verified symbolic engine, "
            "not by you) ===",
            f"TNM: {stage_result.tnm}",
            f"Stage group: {stage_result.stage_group} ({stage_result.edition})",
        ]
        if stage_result.migration_notes:
            lines.append("8th→9th migration: "
                         + " ".join(stage_result.migration_notes))
        if stage_result.descriptor_notes:
            lines.append("Notes: " + " ".join(stage_result.descriptor_notes))
        lines.append(
            "Treat this stage assignment as authoritative. Do NOT re-derive or "
            "override the stage group; reason about management within it."
        )
        return "\n".join(lines)

    def _imaging_block(self, findings: ImagingFindings) -> str:
        return (
            "=== RADIOGRAPHIC FINDINGS (proposed by the imaging reader, "
            "UNVERIFIED) ===\n"
            "These candidate descriptors were read from the films by a vision "
            "model. They are NOT the radiologist's report and NOT pathology; do "
            "not treat them as confirmed. The authoritative stage above already "
            "reflects the verified descriptors — use these only as supporting "
            "context.\n"
            + json.dumps(findings.to_dict(), ensure_ascii=False, indent=2)
        )

    def _language_block(self, lang: str) -> str:
        """Override the modules' hard-coded 'answer in English' requirement.

        The protocol modules mandate English so that generated training data is
        uniform. A consultation with a Chinese-speaking patient needs the
        patient-facing text in Chinese while the clinical vocabulary stays
        canonical, so the override is explicit and scoped rather than a silent
        contradiction of the module.
        """
        if lang != "zh":
            return ""
        return (
            "=== OUTPUT LANGUAGE OVERRIDE (takes precedence over the module's "
            "ENGLISH requirement) ===\n"
            "Write all free-text prose — plan summaries, explanations, "
            "uncertainty statements, questions to the patient — in SIMPLIFIED "
            "CHINESE. Keep these in their canonical form and do not translate "
            "them: TNM descriptors, stage groups, gene and biomarker names, "
            "drug INNs, trial names, and every JSON key and enum value. The "
            "JSON structure itself is unchanged."
        )

    def _consult_block(self, session: "ConsultSession") -> str:
        """Tell the model what was asked, and what is still unknown."""
        gaps = [g for g in session.gaps() if g.get("above_threshold")]
        lines = [
            "=== CONSULTATION PROVENANCE ===",
            f"This case was assembled through {session.round_index} round(s) "
            f"of interview, not supplied complete. Facts carry the round they "
            f"arrived in and whether a deterministic pattern or a model read "
            f"them out of the reply.",
            json.dumps(
                {"known": session.known, "provenance": session.provenance},
                ensure_ascii=False, indent=2,
            ),
        ]
        if gaps:
            lines += [
                "",
                "STILL UNKNOWN after the consultation ended — these are real "
                "information gaps and must appear as 'information_gap' steps "
                "and uncertainty statements, not be filled in by assumption:",
            ]
            lines += [f"  • {g['key']}: {g['why']}" for g in gaps]
        else:
            lines.append(
                "\nNo decision-relevant fact remained unknown when the "
                "consultation stopped."
            )
        return "\n".join(lines)

    def build_messages(
        self,
        case: Case,
        module: PromptModule,
        stage_result: StageResult,
        *,
        imaging_findings: Optional[ImagingFindings] = None,
        retrieval: Optional[RetrievalResult] = None,
        session: Optional["ConsultSession"] = None,
        lang: Optional[str] = None,
    ) -> list[Message]:
        lang = lang or self.lang
        blocks = [module.system_prompt, self._staging_preamble(stage_result)]
        if retrieval is not None:
            blocks.append(evidence_block(retrieval, lang=lang))
        language_block = self._language_block(lang)
        if language_block:
            blocks.append(language_block)
        system = "\n\n".join(blocks)

        user = case.render_user_message()
        if session is not None:
            user += "\n\n" + self._consult_block(session)
        if imaging_findings is not None:
            user += "\n\n" + self._imaging_block(imaging_findings)
        return [
            Message("system", system),
            Message("user", user),
        ]

    # -- full run -----------------------------------------------------------

    def run(
        self,
        case: Case,
        *,
        provider: Optional[str] = None,
        params: Optional[GenerationParams] = None,
        dry_run: bool = False,
        read_films: bool = True,
        retrieve_evidence: bool = True,
        session: Optional[ConsultSession] = None,
        lang: Optional[str] = None,
    ) -> AgentResult:
        case_id = case.case_id
        imaging_flags: list[str] = []
        imaging_findings: Optional[ImagingFindings] = None

        # -- perception layer: read films → proposed descriptors ------------
        if case.has_images() and read_films and not dry_run:
            try:
                imaging_findings = self.read_imaging(case, params=params)
            except Exception as exc:  # graceful: fall back to any given TNM
                imaging_flags.append(f"IMAGING_READ_FAILED: {exc}")
            else:
                case, ingest_flags = self._ingest_imaging(case, imaging_findings)
                imaging_flags.extend(ingest_flags)
        elif case.has_images() and dry_run:
            imaging_flags.append(
                "IMAGING_SKIPPED_DRY_RUN: films attached but not read "
                "(dry-run makes no model calls)"
            )

        imaging_dict = imaging_findings.to_dict() if imaging_findings else None

        stage_result, route_result, flags = self.route_case(case)
        flags = imaging_flags + flags
        staging_dict = stage_result.to_dict() if stage_result else None
        routing_dict = route_result.to_dict() if route_result else {}

        consult_dict = session.to_dict() if session is not None else None

        if stage_result is None or route_result is None:
            return AgentResult(
                case_id, staging_dict, routing_dict, None, None, None,
                flags=flags, error="Could not resolve stage/routing for case",
                imaging=imaging_dict, consult=consult_dict,
            )

        module_key = route_result.module_key
        if module_key is None and self.allow_fallback_module \
                and route_result.fallback_module_key:
            module_key = route_result.fallback_module_key
            flags.append(
                f"USING_FALLBACK_MODULE: {module_key} (no exact module for "
                f"stage {stage_result.stage_group})"
            )

        if module_key is None:
            return AgentResult(
                case_id, staging_dict, routing_dict, None, None, None,
                flags=flags,
                error=(f"No protocol module available for stage "
                       f"{stage_result.stage_group}"),
                imaging=imaging_dict, consult=consult_dict,
            )

        # -- evidence layer: retrieve BEFORE reasoning ----------------------
        retrieval = self.retrieve_evidence(
            stage_result.stage_group,
            facts=(session.known if session is not None else case.fields),
            enabled=retrieve_evidence and not dry_run,
        )
        flags.extend(_evidence_flags(retrieval))

        module = load_module(module_key)
        messages = self.build_messages(
            case, module, stage_result, imaging_findings=imaging_findings,
            retrieval=retrieval, session=session, lang=lang,
        )

        if dry_run:
            return AgentResult(
                case_id, staging_dict, routing_dict, module_key, None,
                LLMResponse(
                    content="[dry-run: prompt assembled, model not called]",
                    provider="(none)", model="(none)",
                ),
                flags=flags + ["DRY_RUN"],
                imaging=imaging_dict, evidence=retrieval.to_dict(),
                consult=consult_dict,
            )

        try:
            prov = self.get_provider(provider)
            response = prov.complete(messages, params=params)
        except Exception as exc:  # provider config/transport errors → result.error
            prov_name = provider or self.config.default_provider
            return AgentResult(
                case_id, staging_dict, routing_dict, module_key,
                prov_name, None, flags=flags, error=str(exc),
                imaging=imaging_dict, evidence=retrieval.to_dict(),
                consult=consult_dict,
            )
        return AgentResult(
            case_id, staging_dict, routing_dict, module_key, prov.name,
            response, flags=flags, imaging=imaging_dict,
            evidence=retrieval.to_dict(), consult=consult_dict,
        )

    # -- evidence -----------------------------------------------------------

    def retrieve_evidence(
        self,
        stage_group: Optional[str],
        *,
        facts: Optional[dict] = None,
        enabled: bool = True,
        limit: int = 3,
    ) -> RetrievalResult:
        """Run the deterministic query plan for a case through the retriever.

        Queries are derived from the *computed* stage and the *known* facts,
        never from model output, so a hallucinated claim cannot steer what gets
        retrieved and then appear to be supported by it.
        """
        if not enabled or not self.retriever.enabled:
            return RetrievalResult(backend=self.retriever.name, enabled=False)
        queries = query_plan(stage_group, facts, limit=limit)
        return self.retriever.search_many(queries, limit=5)

    # -- autonomous consultation (自主问诊) ---------------------------------

    def start_consult(
        self,
        *,
        presentation: str = "",
        question: str = "",
        case: Optional[Case] = None,
        lang: Optional[str] = None,
        max_rounds: Optional[int] = None,
        questions_per_round: Optional[int] = None,
        provider: Optional[str] = None,
        params: Optional[GenerationParams] = None,
    ) -> ConsultSession:
        """Open a consultation, reading whatever the opening text already says.

        The opening statement is treated exactly like any later reply: it goes
        through the same extractor, so "68F T2bN2bM0 adenocarcinoma" fills four
        slots immediately and the first question is about something genuinely
        unknown rather than something already stated.
        """
        cfg = self.config.consult or {}
        session = ConsultSession(
            lang=lang or cfg.get("lang") or self.lang,
            max_rounds=int(max_rounds if max_rounds is not None
                           else cfg.get("max_rounds", 6)),
            questions_per_round=int(
                questions_per_round if questions_per_round is not None
                else cfg.get("questions_per_round", 3)),
            presentation=presentation or "",
            question=question or "",
        )
        if case is not None:
            session.with_case(case)
        opening = session.presentation
        if opening.strip():
            values, notes = self._extract(session, opening, provider=provider,
                                          params=params)
            session.record(values, source="opening", round_index=-1)
            session.notes.extend(notes)
        self._refresh(session)
        return session

    def consult_step(
        self,
        session: ConsultSession,
        reply: str,
        *,
        provider: Optional[str] = None,
        params: Optional[GenerationParams] = None,
    ) -> ConsultSession:
        """Fold one reply into the session and re-plan.

        Records the turn even when the reply yields nothing usable — a round
        that produced no facts is information about the consultation, and
        silently retrying the same question would be worse.
        """
        if session.is_finished():
            return session
        asked = session.ask()
        values, notes = self._extract(
            session, reply, provider=provider, params=params,
            pending=[a["key"] for a in asked],
        )
        accepted = session.record(values, source="reply")
        turn = ConsultTurn(round_index=session.round_index, asked=asked,
                           reply=reply, extracted=accepted, notes=notes)
        session.turns.append(turn)
        if not accepted:
            turn.notes.append(
                "NO_NEW_FACTS: the reply did not resolve any of the questions "
                "asked in this round."
            )
        self._refresh(session)
        return session

    def _extract(
        self,
        session: ConsultSession,
        text: str,
        *,
        provider: Optional[str] = None,
        params: Optional[GenerationParams] = None,
        pending: Optional[list[str]] = None,
    ) -> tuple[dict, list[str]]:
        """Deterministic extraction, with the model as an optional second pass."""
        keys = pending if pending is not None else session.pending()
        prov: Optional[LLMProvider] = None
        try:
            prov = self.get_provider(provider)
        except Exception as exc:  # no usable backend → patterns only
            session.notes.append(f"EXTRACTION_PROVIDER_UNAVAILABLE: {exc}")
        return extract(text, keys, provider=prov, params=params,
                       lang=session.lang)

    def _refresh(self, session: ConsultSession) -> ConsultSession:
        """Re-stage on what is known and recompute the stopping condition.

        Idempotent: it runs on every start/step/finish and on resume, so it
        must not accumulate duplicate notes, and it must not drag a session
        that has already produced a recommendation back into gathering.
        """
        if session.status == STATUS_COMPLETE:
            return session
        session.stage_group = None
        # A TNM triple stages deterministically; failing that, a stage label the
        # case arrived with still tells the planner which band to weight by.
        if session.staging_ready() or session.stage_group_label:
            stage_result, _ = self.resolve_stage(session.to_case())
            if stage_result is not None:
                session.stage_group = stage_result.stage_group
        if is_sufficient(session.known, session.stage_group):
            session.status = STATUS_READY
        elif session.rounds_remaining <= 0:
            session.status = STATUS_EXHAUSTED
            note = (f"CONSULT_ROUNDS_EXHAUSTED: stopped after "
                    f"{session.max_rounds} round(s) with decision-relevant "
                    f"facts still unknown; they are carried into the result "
                    f"as information gaps.")
            if note not in session.notes:
                session.notes.append(note)
        else:
            session.status = STATUS_GATHERING
        return session

    def finish_consult(
        self,
        session: ConsultSession,
        *,
        provider: Optional[str] = None,
        params: Optional[GenerationParams] = None,
        dry_run: bool = False,
        retrieve_evidence: bool = True,
    ) -> AgentResult:
        """Run the assembled case through the normal pipeline."""
        self._refresh(session)
        case = session.to_case()
        result = self.run(
            case, provider=provider, params=params, dry_run=dry_run,
            retrieve_evidence=retrieve_evidence, session=session,
            lang=session.lang,
        )
        # Any status other than READY means the interview stopped early — out
        # of rounds, out of scripted answers, or the user walked away. All of
        # them leave real gaps, so all of them are flagged.
        if session.status != STATUS_READY:
            missing = [g["key"] for g in session.gaps()
                       if g.get("above_threshold")]
            result.flags.append(
                f"CONSULT_INCOMPLETE: the consultation ended at status "
                f"{session.status!r} with decision-relevant facts still "
                f"unknown ({', '.join(missing) or 'none listed'}) — see "
                f"consult.outstanding."
            )
        if not dry_run and result.error is None:
            session.status = STATUS_COMPLETE
            result.consult = session.to_dict()
        return result


def _evidence_flags(retrieval: RetrievalResult) -> list[str]:
    if not retrieval.enabled:
        return [
            "EVIDENCE_NOT_RETRIEVED: no literature retrieval backend ran for "
            "this case; any identifier in the response is model recall and is "
            "labelled MODEL_RECALL_UNVERIFIED."
        ]
    flags = [
        f"EVIDENCE_RETRIEVED[{len(retrieval.records)}]: via "
        f"{retrieval.backend} for {len(retrieval.queries)} query/queries."
    ]
    if retrieval.errors:
        flags.append("EVIDENCE_RETRIEVAL_ERRORS: "
                     + "; ".join(retrieval.errors))
    if not retrieval.records:
        flags.append(
            "EVIDENCE_RETRIEVAL_EMPTY: retrieval ran but returned no records; "
            "citations fall back to model recall."
        )
    return flags
