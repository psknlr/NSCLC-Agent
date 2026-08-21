"""Perception layer: films → validated, PROPOSED descriptors (never a stage)."""

from .imaging import (
    ImagingError,
    ImagingFindings,
    ImagingReader,
    fold_into_case,
    load_image_ref,
    validate_descriptor,
)

__all__ = [
    "ImagingError", "ImagingFindings", "ImagingReader",
    "fold_into_case", "load_image_ref", "validate_descriptor",
]
