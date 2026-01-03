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


def _render_index(collection: dict[str, Any] | None, run_report: dict[str, Any] | None) -> str:
    collection_diag = _extract_diagnostics(collection)
    report_diag = _extract_diagnostics(run_report)

    return """<!doctype html>
<html lang="de">
<head>
  <meta charset="utf-8">
  <title>MTGA Advisor – Export-Status</title>
  <style>
    body { font-family: sans-serif; margin: 2rem; line-height: 1.4; }
    h1, h2 { margin-bottom: 0.25rem; }
    .section { margin-bottom: 2rem; }
    .meta { color: #555; font-size: 0.95rem; }
    pre { background: #f5f5f5; padding: 1rem; overflow: auto; }
    .warning { color: #b00020; }
  </style>
</head>
<body>
  <h1>MTGA Advisor – Export-Status</h1>
  <p class="meta">Textbasierte Ansicht der lokalen Export-Artefakte.</p>

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
</body>
</html>
""".format(
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
        if self.path == "/":
            collection = _read_json(output_dir / "collection.json")
            run_report = _read_json(output_dir / "run-report.json")
            body = _render_index(collection, run_report)
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
