# Repository Guidelines

## Project Structure and Module Organization

- `cli/` holds the command line entry points (invoked via `python -m cli.main`).
- `parser/` contains the log ingestion and collection export pipeline.
- `server/` serves local, read-only views of export artifacts.
- `docs/` stores plans, data schemas, and architectural notes.
- `out/` is the default output folder for generated artifacts (local only).

## Build, Test, and Development Commands

- `python -m cli.main collection --output out` runs the local collection export.
- `python -m cli.main collection --log <path> --log <path> --output out` targets explicit log files.
- `python -m cli.main serve --output out --port 8000` starts a local HTTP server for viewing results.
- `python -m pytest` runs the test suite.
- `python -m pytest parser/tests/test_pipeline.py` runs a focused test module.

## Coding Style and Naming Conventions

- No formatter is enforced; keep style consistent with nearby files.
- Prefer Python 4-space indentation, `snake_case` for functions, and `PascalCase` for classes.
- Name JSON artifacts exactly as documented (e.g., `collection.json`, `run-report.json`).

## Testing Guidelines

- Tests use `pytest` and live under `parser/tests/`.
- Add tests for new parsing behaviors, especially log edge cases and staleness checks.
- Include repro log samples only when necessary, and keep them small.

## Commit and Pull Request Guidelines

- Commit messages in history are short, imperative phrases; there is no strict convention.
- Keep PRs focused, describe intent, and list test commands run.
- Link related issues or docs (e.g., `docs/phase-1-collection-export.md`) when relevant.

## Security and Configuration Tips

- This project is offline-first; do not add required network calls to core workflows.
- Log paths are platform-specific; keep path discovery configurable and documented.
- Never claim a complete collection without a snapshot event and wildcard baseline.
- Advisor features should stay usable with no paid subscriptions; LLM usage must be optional with a prompt-based fallback.
