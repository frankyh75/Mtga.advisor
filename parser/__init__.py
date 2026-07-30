"""Parser package for MTGA log discovery and parsing."""

from parser.decks import export_decks
from parser.export import ExportPaths, export_collection
from parser.pipeline import CollectionReport, parse_collection
from parser.start_hook import DeckSummary, StartHookData, parse_start_hook

__all__ = ["CollectionReport", "ExportPaths", "export_collection", "parse_collection"]
