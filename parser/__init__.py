"""Parser package for MTGA log discovery and parsing."""

from .pipeline import CollectionReport, parse_collection

__all__ = ["CollectionReport", "parse_collection"]
