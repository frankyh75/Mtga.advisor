# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Common Commands

### Run Collection Export
```bash
# Auto-discover logs (macOS/Windows)
python -m cli.main collection --output out

# Specify explicit log paths
python -m cli.main collection --log /path/to/Player.log --log /path/to/Player-prev.log --output out

# Override platform detection
python -m cli.main collection --platform windows --output out
```

### Run Local Server
```bash
python -m cli.main serve --output out --port 8000
# Then open http://127.0.0.1:8000/
```

### Test Commands
```bash
# Run all tests
python -m pytest

# Run specific test module
python -m pytest parser/tests/test_pipeline.py

# Run with verbose output
python -m pytest -v
```

## Project Architecture

This is a **Phase 1** project focused on deterministic, local-only MTG Arena collection export from log files. The architecture follows a clear pipeline pattern with three main modules.

### Core Modules

**parser/** - Log parsing pipeline
- `pipeline.py`: Core parsing pipeline implementing the ingest → chunk → extract_json → dispatch flow
  - `ingest()`: Reads log files into `LogLine` objects
  - `chunk()`: Groups log lines into `LogChunk` events by detecting timestamps and JSON payloads
  - `extract_json()`: Parses JSON payloads from chunks, handling malformed data gracefully
  - `dispatch()`: Processes events to build `CollectionReport` with snapshot+delta semantics
  - `parse_collection()`: Main entry point combining the full pipeline
- `export.py`: Outputs artifacts (`collection.json`, `run-report.json`, raw samples)
  - Handles staleness detection (7-day threshold for snapshots)
  - Manages file I/O with consistent formatting
- `log_paths.py`: Cross-platform log discovery
  - Supports Windows (LocalLow, Steam userdata) and macOS (Logs, Steam userdata)
  - `PathStrategy` system for extensible path detection
  - Steam userdata paths are expanded dynamically to find user-specific logs

**cli/** - Command-line interface
- `main.py`: argparse-based CLI with two subcommands
  - `collection`: Exports collection from logs
  - `serve`: Starts local HTTP server for viewing artifacts
  - Rich override options for all platform paths

**server/** - Local viewing only
- `app.py`: Minimal HTTP server serving static HTML and JSON APIs
  - No external dependencies, uses stdlib `http.server`
  - Endpoints: `/`, `/api/collection`, `/api/run-report`
  - Output directory is configurable via CLI or environment

### Data Flow

1. **Log Discovery**: `log_paths.py` finds MTGA logs based on platform
2. **Parsing**: `pipeline.py` processes logs through a 4-stage pipeline
3. **Export**: `export.py` writes JSON artifacts to output directory
4. **Viewing**: `server.py` serves artifacts locally via HTTP

### Key Design Principles

**Snapshot-first semantics**: The parser requires a snapshot event (`PlayerInventory.GetPlayerCardsV3`) before processing delta events (`Inventory.Updated`). Without a snapshot, completeness is marked "unknown".

**Conservative completeness**: Three-level system (complete/partial/unknown) for cards, wildcards, and source. Unknown is never assumed to be 0.

**Defensive parsing**: JSON extraction handles multiline payloads, malformed data, and MTGA's non-standard numeric format (`+123` instead of `123`).

**No external dependencies for core functionality**: All parsing is deterministic and local. LLM usage is explicitly out of scope for Phase 1.

## Output Artifacts

- `out/collection.json`: Card counts (Arena IDs), wildcards, and diagnostics
- `out/run-report.json`: Run metadata, log paths, summary stats
- `out/raw-samples/`: Individual JSON files per parsed event for debugging

## Platform-Specific Notes

- **macOS**: Default log path is `~/Library/Logs/Wizards of the Coast/MTGA/`
- **Windows**: Default path is `%USERPROFILE%/AppData/LocalLow/Wizards Of The Coast/MTGA/`
- Steam variants have different paths (see `log_paths.py` for full matrix)
- All paths are overridable via CLI flags

## Important Constraints

1. **Phase 1 Scope Lock**: No advisor logic, no online services, no meta signals, no UI beyond debug viewing
2. **Determinism > Everything**: Core features must work without any LLM or network access
3. **Completeness Claims**: Never claim "complete" collection without a snapshot event and wildcard baseline
4. **Error Handling**: Parsing failures produce warnings and partial outputs, not crashes

## Normative Documentation Priority

When in doubt, consult documents in this order:
1. `docs/ai-context.md` - Project goals, phase scope, mandatory constraints
2. `docs/phase-1-plan.md` - Phase 1 definition of done and implementation plan
3. `docs/adr/*.md` - Architecture Decision Records (tech stack, CLI UX, outputs, parser pipeline)
4. `docs/data-schemas.md` - JSON structure examples
