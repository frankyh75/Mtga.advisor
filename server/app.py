from __future__ import annotations

import json
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
import html
import os


DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8000
DEFAULT_OUTPUT_DIR = Path(__file__).resolve().parents[1] / "out"


def _read_json(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None


def _json_response(handler: BaseHTTPRequestHandler, payload: dict[str, Any], *, status: int) -> None:
    body = json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Content-Length", str(len(body.encode("utf-8"))))
    handler.send_header("Cache-Control", "no-store")
    handler.end_headers()
    handler.wfile.write(body.encode("utf-8"))


def _html_response(handler: BaseHTTPRequestHandler, body: str, *, status: int) -> None:
    handler.send_response(status)
    handler.send_header("Content-Type", "text/html; charset=utf-8")
    handler.send_header("Content-Length", str(len(body.encode("utf-8"))))
    handler.send_header("Cache-Control", "no-store")
    handler.send_header(
        "Content-Security-Policy",
        "default-src 'none'; style-src 'unsafe-inline'; img-src 'none'; script-src 'none'; connect-src 'self';",
    )
    handler.send_header("X-Content-Type-Options", "nosniff")
    handler.end_headers()
    handler.wfile.write(body.encode("utf-8"))


def _format_json(payload: dict[str, Any] | None) -> str:
    if payload is None:
        return "(keine Daten gefunden)"
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2)


def _extract_diagnostics(payload: dict[str, Any] | None) -> dict[str, Any]:
    if not payload:
        return {"completeness": None, "warnings": ["Artefakt fehlt."], "evidence": []}
    diagnostics = payload.get("diagnostics", {})
    return {
        "completeness": diagnostics.get("completeness"),
        "warnings": list(diagnostics.get("warnings", [])),
        "evidence": list(diagnostics.get("evidence", [])),
    }


def _render_list(items: list[str]) -> str:
    if not items:
        return "<p>Keine Warnungen.</p>"
    rows = "".join(f"<li>{html.escape(item)}</li>" for item in items)
    return f"<ul>{rows}</ul>"


def _summary_card(label: str, value: Any, detail: str = "") -> str:
    return (
        '<div class="card">'
        f"<span>{html.escape(label)}</span>"
        f"<strong>{html.escape(str(value))}</strong>"
        f"<small>{html.escape(detail)}</small>"
        "</div>"
    )


def _collection_summary(collection: dict[str, Any] | None) -> str:
    if not collection:
        return _summary_card("Collection", "fehlt", "collection.json nicht gefunden")
    cards = collection.get("cards", {})
    total = sum(int(value) for value in cards.values()) if isinstance(cards, dict) else 0
    return "".join(
        [
            _summary_card("Unique IDs", len(cards), "collection.json"),
            _summary_card("Total Cards", total, str(collection.get("source", "unknown"))),
        ]
    )


def _advisor_summary(advisor_result: dict[str, Any] | None) -> str:
    if not advisor_result:
        return _summary_card("Advisor", "fehlt", "advisor-result.json nicht gefunden")
    summary = advisor_result.get("summary", {})
    return "".join(
        [
            _summary_card("Completion", f"{summary.get('completionScore', '?')}%", "Deck-Fortschritt"),
            _summary_card("Missing", summary.get("missingCards", "?"), "fehlende Karten"),
            _summary_card(
                "Craft Advice",
                "allowed" if summary.get("hardCraftAdviceAllowed") else "what-if",
                "Wildcard-Gate",
            ),
        ]
    )


def _render_missing_table(advisor_result: dict[str, Any] | None) -> str:
    if not advisor_result:
        return "<p>Noch kein Advisor-Result vorhanden.</p>"
    recommendations = advisor_result.get("recommendations", [])
    if not recommendations:
        return "<p>Keine fehlenden Karten.</p>"
    rows = []
    for rec in recommendations[:50]:
        rows.append(
            "<tr>"
            f"<td>{html.escape(str(rec.get('name', 'Unknown')))}</td>"
            f"<td>{html.escape(str(rec.get('needed', '?')))}</td>"
            f"<td>{html.escape(str(rec.get('owned', '?')))}</td>"
            f"<td>{html.escape(str(rec.get('rarity', 'unknown')))}</td>"
            f"<td>{html.escape(', '.join(rec.get('reasons', [])))}</td>"
            "</tr>"
        )
    suffix = ""
    if len(recommendations) > 50:
        suffix = f"<p class=\"meta\">Weitere {len(recommendations) - 50} Empfehlungen im JSON.</p>"
    return (
        "<table><thead><tr><th>Karte</th><th>Need</th><th>Owned</th><th>Rarity</th><th>Gründe</th></tr></thead>"
        f"<tbody>{''.join(rows)}</tbody></table>{suffix}"
    )


def _render_deck(deck: dict[str, Any] | None) -> str:
    if not deck:
        return "<p>Noch kein importiertes Deck vorhanden.</p>"
    main_count = sum(int(entry.get("count", 0)) for entry in deck.get("mainboard", []))
    side_count = sum(int(entry.get("count", 0)) for entry in deck.get("sideboard", []))
    diagnostics = deck.get("diagnostics", {})
    return (
        f"<p><strong>{html.escape(str(deck.get('name', 'Imported Deck')))}</strong> "
        f"({html.escape(str(deck.get('format', 'unknown')))})</p>"
        f"<p>Mainboard: {main_count} Karten · Sideboard: {side_count} Karten</p>"
        f"<div class=\"warning\">{_render_list(list(diagnostics.get('warnings', [])))}</div>"
    )


def _render_index(
    collection: dict[str, Any] | None,
    run_report: dict[str, Any] | None,
    deck: dict[str, Any] | None,
    advisor_result: dict[str, Any] | None,
) -> str:
    collection_diag = _extract_diagnostics(collection)
    report_diag = _extract_diagnostics(run_report)
    advisor_warnings = list(advisor_result.get("warnings", [])) if advisor_result else []

    return """<!doctype html>
<html lang="de">
<head>
  <meta charset="utf-8">
  <title>MTGA Advisor – Export-Status</title>
  <style>
    :root {{
      --ink: #1f2933;
      --muted: #667085;
      --line: #d8cfc0;
      --paper: #f7f1e7;
      --panel: #fffaf0;
      --accent: #b45309;
      --danger: #b00020;
    }}
    body {{
      background:
        radial-gradient(circle at 15% 10%, rgba(180,83,9,.18), transparent 24rem),
        linear-gradient(135deg, #f7f1e7 0%, #eadfcb 100%);
      color: var(--ink);
      font-family: ui-serif, Georgia, "Times New Roman", serif;
      margin: 0;
      line-height: 1.45;
    }}
    main {{ max-width: 1160px; margin: 0 auto; padding: 2rem; }}
    h1 {{ font-size: clamp(2rem, 5vw, 4.2rem); line-height: .95; margin: 1rem 0 .5rem; }}
    h2 {{ margin-bottom: .4rem; }}
    .hero {{ border-bottom: 1px solid var(--line); margin-bottom: 1.5rem; padding-bottom: 1rem; }}
    .section {{ background: rgba(255,250,240,.82); border: 1px solid var(--line); border-radius: 18px; margin-bottom: 1rem; padding: 1rem 1.2rem; box-shadow: 0 14px 40px rgba(60,40,20,.08); }}
    .grid {{ display: grid; gap: .8rem; grid-template-columns: repeat(auto-fit, minmax(150px, 1fr)); margin: 1rem 0; }}
    .card {{ background: var(--panel); border: 1px solid var(--line); border-radius: 14px; padding: .9rem; }}
    .card span, .card small, .meta {{ color: var(--muted); font-size: .92rem; }}
    .card strong {{ display: block; font-size: 1.65rem; color: var(--accent); }}
    pre {{ background: #231f1a; color: #f8ead1; padding: 1rem; overflow: auto; border-radius: 12px; max-height: 26rem; }}
    .warning {{ color: var(--danger); }}
    table {{ width: 100%; border-collapse: collapse; margin-top: .8rem; }}
    th, td {{ border-bottom: 1px solid var(--line); padding: .55rem; text-align: left; }}
    th {{ color: var(--muted); font-size: .85rem; text-transform: uppercase; letter-spacing: .04em; }}
    nav a {{ color: var(--accent); margin-right: 1rem; }}
  </style>
</head>
<body>
<main>
  <div class="hero">
    <p class="meta">Lokaler Advisor · offline-first · regelbasiert</p>
    <h1>MTGA Advisor</h1>
    <nav>
      <a href="/api/collection">collection.json</a>
      <a href="/api/run-report">run-report.json</a>
      <a href="/api/deck">arena_deck.json</a>
      <a href="/api/advisor-result">advisor-result.json</a>
    </nav>
  </div>

  <div class="grid">
    {collection_summary}
    {advisor_summary}
  </div>

  <div class="section">
    <h2>Deck</h2>
    {deck_html}
  </div>

  <div class="section">
    <h2>Advisor</h2>
    <div class="warning">
      <h3>Warnings</h3>
      {advisor_warnings}
    </div>
    {missing_table}
  </div>

  <div class="section">
    <h2>Collection</h2>
    <p>Completeness: <strong>{collection_completeness}</strong></p>
    <div class="warning">
      <h3>Warnings</h3>
      {collection_warnings}
    </div>
    <h3>JSON</h3>
    <pre>{collection_json}</pre>
  </div>

  <div class="section">
    <h2>Run-Report</h2>
    <p>Completeness: <strong>{report_completeness}</strong></p>
    <div class="warning">
      <h3>Warnings</h3>
      {report_warnings}
    </div>
    <h3>JSON</h3>
    <pre>{report_json}</pre>
  </div>
</main>
</body>
</html>
""".format(
        collection_summary=_collection_summary(collection),
        advisor_summary=_advisor_summary(advisor_result),
        deck_html=_render_deck(deck),
        advisor_warnings=_render_list(advisor_warnings),
        missing_table=_render_missing_table(advisor_result),
        collection_completeness=html.escape(str(collection_diag["completeness"])),
        collection_warnings=_render_list(collection_diag["warnings"]),
        collection_json=html.escape(_format_json(collection)),
        report_completeness=html.escape(str(report_diag["completeness"])),
        report_warnings=_render_list(report_diag["warnings"]),
        report_json=html.escape(_format_json(run_report)),
    )


class MtgaAdvisorHandler(BaseHTTPRequestHandler):
    server_version = "mtga-advisor/0.1"

    def do_GET(self) -> None:
        output_dir = self.server.output_dir
        if self.path == "/api/collection":
            payload = _read_json(output_dir / "collection.json")
            if payload is None:
                _json_response(
                    self,
                    {"error": "not_found", "message": "collection.json fehlt."},
                    status=HTTPStatus.NOT_FOUND,
                )
                return
            _json_response(self, payload, status=HTTPStatus.OK)
            return
        if self.path == "/api/run-report":
            payload = _read_json(output_dir / "run-report.json")
            if payload is None:
                _json_response(
                    self,
                    {"error": "not_found", "message": "run-report.json fehlt."},
                    status=HTTPStatus.NOT_FOUND,
                )
                return
            _json_response(self, payload, status=HTTPStatus.OK)
            return
        if self.path == "/api/deck":
            payload = _read_json(output_dir / "arena_deck.json")
            if payload is None:
                _json_response(
                    self,
                    {"error": "not_found", "message": "arena_deck.json fehlt."},
                    status=HTTPStatus.NOT_FOUND,
                )
                return
            _json_response(self, payload, status=HTTPStatus.OK)
            return
        if self.path == "/api/advisor-result":
            payload = _read_json(output_dir / "advisor-result.json")
            if payload is None:
                _json_response(
                    self,
                    {"error": "not_found", "message": "advisor-result.json fehlt."},
                    status=HTTPStatus.NOT_FOUND,
                )
                return
            _json_response(self, payload, status=HTTPStatus.OK)
            return
        if self.path == "/":
            collection = _read_json(output_dir / "collection.json")
            run_report = _read_json(output_dir / "run-report.json")
            deck = _read_json(output_dir / "arena_deck.json")
            advisor_result = _read_json(output_dir / "advisor-result.json")
            body = _render_index(collection, run_report, deck, advisor_result)
            _html_response(self, body, status=HTTPStatus.OK)
            return

        _json_response(
            self,
            {"error": "not_found", "message": "Pfad nicht gefunden."},
            status=HTTPStatus.NOT_FOUND,
        )

    def log_message(self, format: str, *args: Any) -> None:
        return


class MtgaAdvisorServer(ThreadingHTTPServer):
    def __init__(self, server_address: tuple[str, int], handler: type[BaseHTTPRequestHandler], output_dir: Path):
        super().__init__(server_address, handler)
        self.output_dir = output_dir


def run_server(host: str = DEFAULT_HOST, port: int = DEFAULT_PORT, output_dir: Path = DEFAULT_OUTPUT_DIR) -> None:
    server = MtgaAdvisorServer((host, port), MtgaAdvisorHandler, output_dir)
    print(f"Serving MTGA Advisor on http://{host}:{port} (output={output_dir})")
    server.serve_forever()


if __name__ == "__main__":
    output_env = os.environ.get("MTGA_ADVISOR_OUTPUT_DIR")
    resolved_output = Path(output_env).expanduser().resolve() if output_env else DEFAULT_OUTPUT_DIR
    run_server(output_dir=resolved_output)
