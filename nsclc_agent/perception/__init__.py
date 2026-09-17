"""Perception layer: films and clinical documents → validated, PROPOSED
facts and descriptors (never a stage, never ground truth)."""

from .imaging import (
    ImagingError,
    ImagingFindings,
    ImagingReader,
    fold_into_case,
    load_image_ref,
    validate_descriptor,
)
from .reports import ReportFindings, ReportReader, fold_report_facts

__all__ = [
    "ImagingError", "ImagingFindings", "ImagingReader",
    "fold_into_case", "load_image_ref", "validate_descriptor",
    "ReportFindings", "ReportReader", "fold_report_facts",
]
