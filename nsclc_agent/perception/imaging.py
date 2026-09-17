"""Perception layer — read radiology films into candidate TNM descriptors.

The inviolable rule, unchanged from v0.1:

    The vision model PROPOSES descriptors. It does NOT assign the stage group.

What changed, closing the review findings:

* **Proposals are vocabulary-validated.** A proposed ``"N2"`` (which the
  engine would refuse) is rejected at ingestion with a
  ``IMAGING_DESCRIPTOR_REJECTED`` flag instead of being seeded into the case
  and detonating the run downstream.
* **Cross-checks normalize both sides first.** ``t2a`` vs ``T2a`` is
  agreement, not discordance.
* **No silent text-model fallback.** A reader must declare vision capability;
  without one the read is skipped with ``NO_VISION_PROVIDER`` — base64 images
  are never sent to a text model to hallucinate over.
* Findings are graded ``model_reasoning`` in the ledger — never releasable on
  their own — and ``requires_confirmation`` is schema-enforced true.
"""

from __future__ import annotations

import base64
import json
import mimetypes
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from ..llm.base import LLMError, extract_json
from ..staging import StagingError
from ..staging.tnm import _normalize_m, _normalize_n, _normalize_t  # noqa: PLC2701

_IMAGE_EXTS = {
    ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
    ".webp": "image/webp", ".gif": "image/gif", ".bmp": "image/bmp",
    ".tif": "image/tiff", ".tiff": "image/tiff",
}
_MAX_INLINE_BYTES = 20 * 1024 * 1024
_MAX_IMAGES = 12


class ImagingError(RuntimeError):
    pass


def load_image_ref(ref: str, *, base_dir: Path | None = None) -> str:
    """Resolve one image reference to a URL usable in a vision message.

    Local paths resolve relative to ``base_dir`` (the case file's directory)
    first, then the CWD — the v0.1 behavior of CWD-only made example cases
    break the moment you ran them from anywhere else.
    """
    if ref.startswith(("data:", "http://", "https://")):
        return ref
    path = Path(ref).expanduser()
    if not path.is_absolute() and base_dir is not None:
        candidate = (base_dir / path).resolve()
        if candidate.is_file():
            path = candidate
    if not path.is_file():
        raise ImagingError(f"Image not found: {ref}")
    suffix = path.suffix.lower()
    if suffix and suffix not in _IMAGE_EXTS:
        # A PDF (or DICOM, docx…) silently base64'd into an image_url part is
        # a request the vision endpoint cannot honor — refuse with the fix.
        raise ImagingError(
            f"{path.name}: {suffix} is not a supported image format — export "
            f"the page(s) to PNG/JPEG first (supported: "
            f"{', '.join(sorted(_IMAGE_EXTS))})")
    size = path.stat().st_size
    if size > _MAX_INLINE_BYTES:
        raise ImagingError(
            f"Image {path.name} is {size / 1e6:.1f} MB, over the "
            f"{_MAX_INLINE_BYTES / 1e6:.0f} MB inline limit."
        )
    mime = _IMAGE_EXTS.get(path.suffix.lower()) or \
        mimetypes.guess_type(path.name)[0] or "image/png"
    b64 = base64.b64encode(path.read_bytes()).decode("ascii")
    return f"data:{mime};base64,{b64}"


def load_image_refs(refs: list[str], *, base_dir: Path | None = None) -> list[str]:
    if len(refs) > _MAX_IMAGES:
        raise ImagingError(f"{len(refs)} images attached; the limit is {_MAX_IMAGES}")
    return [load_image_ref(r, base_dir=base_dir) for r in refs]


@dataclass
class ImagingFindings:
    """Model-PROPOSED, UNVERIFIED radiographic descriptors."""

    modality: Optional[str] = None
    candidate_t: Optional[str] = None
    candidate_n: Optional[str] = None
    candidate_m: Optional[str] = None
    nodal_stations: list[str] = field(default_factory=list)
    measurable_lesions: list[dict[str, Any]] = field(default_factory=list)
    metastatic_sites: list[str] = field(default_factory=list)
    confidence: Optional[str] = None
    uncertainties: list[str] = field(default_factory=list)
    #: Proposals the vocabulary validator rejected, kept for the audit trail.
    rejected_descriptors: list[str] = field(default_factory=list)
    provider: Optional[str] = None
    model: Optional[str] = None
    n_images: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": "MODEL_PROPOSED_UNVERIFIED",
            "requires_confirmation": True,
            "modality": self.modality,
            "candidate_t": self.candidate_t,
            "candidate_n": self.candidate_n,
            "candidate_m": self.candidate_m,
            "nodal_stations": self.nodal_stations,
            "measurable_lesions": self.measurable_lesions,
            "metastatic_sites": self.metastatic_sites,
            "confidence": self.confidence,
            "uncertainties": self.uncertainties,
            "rejected_descriptors": self.rejected_descriptors,
            "read_by": {"provider": self.provider, "model": self.model,
                        "images": self.n_images},
        }


_NORMALIZERS = {"t": _normalize_t, "n": _normalize_n, "m": _normalize_m}


def validate_descriptor(kind: str, value: Any) -> tuple[Optional[str], Optional[str]]:
    """Normalize a proposed descriptor against the engine vocabulary.

    Returns ``(canonical, None)`` or ``(None, rejection_reason)``. A proposal
    the engine would refuse (bare N2, T2…) is rejected here, with the engine's
    own actionable message, instead of being seeded and failing the run later.
    """
    if value is None:
        return None, None
    text = str(value).strip()
    if not text or text.lower() in ("null", "none", "n/a", "na", "unknown", "?"):
        return None, None
    try:
        return _NORMALIZERS[kind](text), None
    except StagingError as exc:
        return None, f"IMAGING_DESCRIPTOR_REJECTED[{kind.upper()}]: {text!r} — {exc}"


IMAGING_EXTRACTION_PROMPT = """\
=== IMAGING DESCRIPTOR EXTRACTION (NSCLC, AJCC/UICC 9th edition) ===

You are a thoracic-imaging description assistant in an educational NSCLC
pipeline. You are shown radiology images (CT / PET-CT / MRI) for one patient.

YOUR ROLE — and its hard limits:
  • PROPOSE candidate radiographic TNM *descriptors* and measurements.
  • You DO NOT assign a stage group. A deterministic engine does that.
  • Do NOT invent findings. If a descriptor cannot be determined, return null
    and add an "uncertainties" line naming what is missing and the test that
    would resolve it.
  • Everything you output is MODEL-PROPOSED and UNVERIFIED, pending the
    reporting radiologist and pathology.

Use the exact 9th-edition vocabulary:
  cT ∈ {Tis, T1a, T1b, T1c, T2a, T2b, T3, T4} or null
  cN ∈ {N0, N1, N2a, N2b, N3} or null
      N2a = single ipsilateral mediastinal station; N2b = multiple stations.
      If you cannot tell single vs multi-station, use null — do NOT write "N2".
  cM ∈ {M0, M1a, M1b, M1c1, M1c2} or null
      M1a intrathoracic; M1b single extrathoracic; M1c1 multiple mets one
      organ system; M1c2 multiple organ systems.

Report nodal stations by IASLC number (e.g. "4R", "7").

Respond with ONLY a JSON object:
{"modality": "CT|PET-CT|MRI|other", "candidate_t": …, "candidate_n": …,
 "candidate_m": …, "nodal_stations": [...],
 "measurable_lesions": [{"site": …, "long_axis_mm": …}],
 "metastatic_sites": [...], "confidence": "high|moderate|low|none",
 "uncertainties": ["…"]}
"""


class ImagingReader:
    """Reads films through a vision-capable client into proposed descriptors."""

    def __init__(self, llm: Any):
        if llm is None or not getattr(llm, "available", False):
            raise ImagingError("no model configured for film reading")
        if not getattr(llm, "supports_vision", False):
            # The v0.1 failure mode: base64 slabs sent to a text model.
            raise ImagingError(
                f"provider {getattr(llm, 'name', '?')!r} is not vision-capable; "
                f"refusing to send images to a text model"
            )
        self.llm = llm

    def read(
        self,
        images: list[str],
        *,
        context: str = "",
        base_dir: Path | None = None,
        budget: Any | None = None,
    ) -> ImagingFindings:
        if not images:
            raise ImagingError("no images provided to the imaging reader")
        urls = load_image_refs(images, base_dir=base_dir)
        if budget is not None and not budget.reserve_llm():
            raise ImagingError("LLM budget exhausted before film reading")
        user_parts: list[dict[str, Any]] = [{
            "type": "text",
            "text": "Read the attached film(s) and return the JSON described above."
                    + (f"\n\nClinical context (may be incomplete; do not "
                       f"over-read):\n{context.strip()}" if context else ""),
        }]
        user_parts += [{"type": "image_url", "image_url": {"url": u}} for u in urls]
        try:
            response = self.llm.chat(
                [
                    {"role": "system", "content": IMAGING_EXTRACTION_PROMPT},
                    {"role": "user", "content": user_parts},
                ],
                temperature=0.0, max_tokens=1500,
            )
        except LLMError as exc:
            if budget is not None:
                budget.refund_llm()
            raise ImagingError(f"film reading failed: {exc}") from exc
        if budget is not None:
            budget.charge_llm_tokens(response.total_tokens)
        payload = extract_json(response.text, None)
        if not isinstance(payload, dict):
            raise ImagingError(
                f"could not parse imaging findings from model reply: "
                f"{response.text[:200]!r}"
            )
        return self._to_findings(payload, response.provider, response.model, len(urls))

    @staticmethod
    def _to_findings(
        data: dict[str, Any], provider: str, model: str, n_images: int
    ) -> ImagingFindings:
        rejected: list[str] = []
        canon: dict[str, Optional[str]] = {}
        for kind in ("t", "n", "m"):
            value, rejection = validate_descriptor(kind, data.get(f"candidate_{kind}"))
            canon[kind] = value
            if rejection:
                rejected.append(rejection)

        def _norm_str(value: Any) -> Optional[str]:
            text = str(value).strip() if value is not None else ""
            return text or None

        return ImagingFindings(
            modality=_norm_str(data.get("modality")),
            candidate_t=canon["t"],
            candidate_n=canon["n"],
            candidate_m=canon["m"],
            nodal_stations=[str(s) for s in data.get("nodal_stations") or []],
            measurable_lesions=[
                d for d in (data.get("measurable_lesions") or [])
                if isinstance(d, dict)
            ],
            metastatic_sites=[str(s) for s in data.get("metastatic_sites") or []],
            confidence=_norm_str(data.get("confidence")),
            uncertainties=[str(u) for u in data.get("uncertainties") or []],
            rejected_descriptors=rejected,
            provider=provider, model=model, n_images=n_images,
        )


def fold_into_case(
    t: Optional[str], n: Optional[str], m: Optional[str],
    findings: ImagingFindings,
) -> tuple[dict[str, str], list[str]]:
    """Fold proposed descriptors into the case's descriptors, safely.

    Human/pathologic descriptors stay authoritative and are only cross-checked
    (normalized on BOTH sides before comparison); missing descriptors are
    seeded from validated proposals. Returns ``(seeded, flags)``.
    """
    flags: list[str] = []
    flags.extend(findings.rejected_descriptors)
    seeded: dict[str, str] = {}
    human = {"t": t, "n": n, "m": m}
    proposed = {"t": findings.candidate_t, "n": findings.candidate_n,
                "m": findings.candidate_m}
    for kind in ("t", "n", "m"):
        have, prop = human[kind], proposed[kind]
        if have and prop:
            canon_have, rejection = validate_descriptor(kind, have)
            if rejection or canon_have is None:
                continue  # the case's own descriptor is the engine's problem
            if canon_have != prop:
                flags.append(
                    f"IMAGING_DISCORDANCE[{kind.upper()}]: case says "
                    f"{canon_have} but the film reader proposed {prop} — the "
                    f"case value is used for staging; reconcile before use."
                )
        elif not have and prop:
            seeded[kind] = prop
    if seeded:
        flags.append(
            "RADIOGRAPHIC_TNM_PROPOSED: descriptors seeded from validated "
            "model proposals ("
            + ", ".join(f"c{k.upper()}={v}" for k, v in seeded.items())
            + ") — provisional cTNM pending radiologist/pathology confirmation."
        )
    return seeded, flags


def mock_findings_payload() -> str:
    """The honest empty read the offline mock returns for imaging prompts."""
    return json.dumps({
        "modality": "unknown",
        "candidate_t": None, "candidate_n": None, "candidate_m": None,
        "nodal_stations": [], "measurable_lesions": [], "metastatic_sites": [],
        "confidence": "none",
        "uncertainties": [
            "Offline mock cannot read images; configure a vision-capable "
            "backend for real film reading."
        ],
    }, ensure_ascii=False)
