"""MTGA Advisor Dashboard — lokaler Webserver.

Zeigt Collection, Decks, LLM-Advisor-Ergebnisse und Run-Reports an.
Unterstützt interaktive Deck-Auswahl und LLM-Chat.
"""

from __future__ import annotations

import json
import urllib.request
import urllib.error
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
import html
import os
from string import Template

from advisor.llm_config import LLMConfig, load_config, write_default_config


DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8000
DEFAULT_OUTPUT_DIR = Path(__file__).resolve().parents[1] / "out"
DASHBOARD_JS_PATH = Path(__file__).with_name("dashboard.js")


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
        "default-src 'none'; style-src 'unsafe-inline'; img-src 'none'; script-src 'self'; connect-src 'self';",
    )
    handler.send_header("X-Content-Type-Options", "nosniff")
    handler.end_headers()
    handler.wfile.write(body.encode("utf-8"))


def _js_response(handler: BaseHTTPRequestHandler, body: str, *, status: int) -> None:
    handler.send_response(status)
    handler.send_header("Content-Type", "application/javascript; charset=utf-8")
    handler.send_header("Content-Length", str(len(body.encode("utf-8"))))
    handler.send_header("Cache-Control", "no-store")
    handler.send_header("X-Content-Type-Options", "nosniff")
    handler.end_headers()
    handler.wfile.write(body.encode("utf-8"))


def _dashboard_js() -> str:
    return DASHBOARD_JS_PATH.read_text(encoding="utf-8")


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
        return "<p>Keine.</p>"
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


def _decks_summary(decks: dict[str, Any] | None) -> str:
    if not decks:
        return _summary_card("Decks", "fehlt", "decks.json nicht gefunden")
    deck_list = decks.get("decks", [])
    return _summary_card("Decks", len(deck_list), "decks.json")


def _llm_advisor_summary(advisor: dict[str, Any] | None) -> str:
    if not advisor:
        return _summary_card("LLM Advisor", "fehlt", "advisor-result.json nicht gefunden")
    summary = advisor.get("summary", {})
    priorities = advisor.get("craftingPriorities", [])
    optimizations = advisor.get("deckOptimizations", [])
    return "".join(
        [
            _summary_card("Top Priority", summary.get("topPriority", "?"), "LLM-Empfehlung"),
            _summary_card("Crafting Priorities", len(priorities), "Gruppen"),
            _summary_card("Deck Optimizations", len(optimizations), "Decks"),
        ]
    )


def _render_decks_table(decks: dict[str, Any] | None) -> str:
    if not decks:
        return "<p>Noch kein Deck-Export vorhanden. Führe <code>python3 -m cli.main run</code> aus.</p>"
    deck_list = decks.get("decks", [])
    if not deck_list:
        return "<p>Keine Decks gefunden.</p>"
    rows = []
    for i, deck in enumerate(deck_list[:50]):
        name = deck.get("name", "Unnamed")
        fmt = deck.get("attributes", {}).get("Format", "?")
        deck_id = deck.get("deckId", i)
        legalities = deck.get("formatLegalities", {})
        legal = ", ".join(f for f, v in legalities.items() if v)[:40] or "?"
        row = (
            "<tr class='deck-row'>"
            + f"<td>{html.escape(name)}</td>"
            + f"<td>{html.escape(fmt)}</td>"
            + f"<td>{html.escape(legal)}</td>"
            + (
                "<td><button class='btn-select-deck' "
                f"data-deck-id='{html.escape(str(deck_id))}' "
                f"data-deck-name='{html.escape(name)}'>Select</button></td>"
            )
            + "</tr>"
        )
        rows.append(row)
    suffix = ""
    if len(deck_list) > 50:
        suffix = f'<p class="meta">Weitere {len(deck_list) - 50} Decks im JSON.</p>'
    return (
        "<table><thead><tr><th>Name</th><th>Format</th><th>Legal in</th><th></th></tr></thead>"
        f"<tbody>{''.join(rows)}</tbody></table>{suffix}"
    )


def _render_crafting_priorities(advisor: dict[str, Any] | None) -> str:
    if not advisor:
        return "<p>Noch kein LLM-Advisor-Ergebnis vorhanden.</p>"
    priorities = advisor.get("craftingPriorities", [])
    if not priorities:
        return "<p>Keine Crafting-Prioritäten.</p>"
    sections = []
    for p in priorities[:10]:
        reason = p.get("reason", "?")
        cards = p.get("cards", [])
        card_rows = "".join(
            f"<li>{html.escape(c.get('name', '?'))} "
            f"({c.get('count', 1)}x, {c.get('rarity', '?')}) "
            f"für {', '.join(c.get('forDecks', ['?']))}</li>"
            for c in cards
        )
        sections.append(
            "<div class='priority-group'>"
            f"<h3>{html.escape(reason)}</h3>"
            f"<ul>{card_rows}</ul>"
            "</div>"
        )
    return "".join(sections)


def _render_deck_optimizations(advisor: dict[str, Any] | None) -> str:
    if not advisor:
        return ""
    optimizations = advisor.get("deckOptimizations", [])
    if not optimizations:
        return ""
    sections = []
    for d in optimizations[:5]:
        name = d.get("deckName", "?")
        fmt = d.get("format", "?")
        issues = d.get("issues", [])
        suggestions = d.get("suggestions", [])
        crafting = d.get("craftingNeeded", "")
        sections.append(
            "<div class='optimization-group'>"
            f"<h3>{html.escape(name)} ({html.escape(fmt)})</h3>"
            f"<h4>Issues</h4><ul>{''.join(f'<li>{html.escape(i)}</li>' for i in issues)}</ul>"
            f"<h4>Suggestions</h4><ul>{''.join(f'<li>{html.escape(s)}</li>' for s in suggestions)}</ul>"
            f"<p><strong>Crafting:</strong> {html.escape(crafting)}</p>"
            "</div>"
        )
    return "".join(sections)


def _render_meta_notes(advisor: dict[str, Any] | None) -> str:
    if not advisor:
        return ""
    notes = advisor.get("metaNotes", [])
    if not notes:
        return ""
    return f"<ul>{''.join(f'<li>{html.escape(n)}</li>' for n in notes)}</ul>"


def _build_chat_prompt(deck: dict[str, Any], collection: dict[str, Any] | None, question: str) -> str:
    """Baue einen Chat-Prompt für eine spezifische Deck-Frage."""
    lines = [
        "Du bist ein MTG Arena Deck-Building Experte.",
        "Beantworte die Frage des Nutzers basierend auf dem Deck und der Collection.",
        "",
        "## Deck",
    ]

    name = deck.get("name", "Unnamed")
    fmt = deck.get("attributes", {}).get("Format", "unknown")
    lines.append(f"Name: {name}")
    lines.append(f"Format: {fmt}")

    # Deck cards if available
    deck_cards = deck.get("cards", deck.get("mainDeck", []))
    if deck_cards:
        lines.append("\nKarten:")
        if isinstance(deck_cards, list):
            for card in deck_cards[:60]:
                if isinstance(card, dict):
                    lines.append(f"  {card.get('name', '?')} x{card.get('count', card.get('quantity', 1))}")
                elif isinstance(card, str):
                    lines.append(f"  {card}")
        elif isinstance(deck_cards, dict):
            for card_id, count in list(deck_cards.items())[:60]:
                lines.append(f"  {card_id} x{count}")

    # Collection context
    if collection:
        cards = collection.get("cards", {})
        wildcards = collection.get("wildcards", {})
        total = sum(int(v) for v in cards.values()) if isinstance(cards, dict) else 0
        lines.append(f"\n## Collection ({len(cards)} unique, {total} total)")
        if wildcards:
            lines.append("Wildcards:")
            for key in ("wcCommon", "wcUncommon", "wcRare", "wcMythic"):
                val = wildcards.get(key, 0)
                lines.append(f"  {key.replace('wc', '')}: {val}")

    lines.append(f"\n## Frage\n{question}")
    lines.append("\nAntworte auf Deutsch, präzise und mit konkreten Kartennamen.")

    return "\n".join(lines)


def _call_llm_chat(config: LLMConfig, prompt: str) -> dict[str, Any]:
    """Rufe LLM für Chat auf. Returns dict with response or error."""
    payload = {
        "messages": [
            {"role": "system", "content": config.system_prompt},
            {"role": "user", "content": prompt},
        ],
        "temperature": config.temperature,
        "max_tokens": config.max_tokens,
        "stream": False,
    }

    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        config.endpoint,
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )

    try:
        with urllib.request.urlopen(req, timeout=config.timeout) as resp:
            body = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        error_body = exc.read().decode("utf-8", errors="replace")
        return {"error": f"HTTP {exc.code}: {error_body[:500]}"}
    except urllib.error.URLError as exc:
        return {"error": f"LLM nicht erreichbar: {exc.reason}"}
    except Exception as exc:
        return {"error": str(exc)}

    choices = body.get("choices", [])
    if not choices:
        return {"error": "LLM gab keine Choices zurück"}

    content = choices[0].get("message", {}).get("content", "")
    if not content:
        return {"error": "LLM gab leere Antwort"}

    return {"response": content, "model": config.model_name}


def _render_index(
    collection: dict[str, Any] | None,
    run_report: dict[str, Any] | None,
    deck: dict[str, Any] | None,
    advisor_result: dict[str, Any] | None,
    decks: dict[str, Any] | None,
    llm_config: LLMConfig,
) -> str:
    collection_diag = _extract_diagnostics(collection)
    report_diag = _extract_diagnostics(run_report)
    advisor_warnings = list(advisor_result.get("warnings", [])) if advisor_result else []

    return Template("""<!doctype html>
<html lang="de">
<head>
  <meta charset="utf-8">
  <title>MTGA Advisor – Dashboard</title>
  <style>
    :root {
      --ink: #1f2933;
      --muted: #667085;
      --line: #d8cfc0;
      --paper: #f7f1e7;
      --panel: #fffaf0;
      --accent: #b45309;
      --danger: #b00020;
      --green: #2d6a4f;
    }
    * { box-sizing: border-box; }
    body {
      background:
        radial-gradient(circle at 15% 10%, rgba(180,83,9,.18), transparent 24rem),
        linear-gradient(135deg, #f7f1e7 0%, #eadfcb 100%);
      color: var(--ink);
      font-family: ui-serif, Georgia, "Times New Roman", serif;
      margin: 0;
      line-height: 1.45;
    }
    main { max-width: 1160px; margin: 0 auto; padding: 2rem; }
    h1 { font-size: clamp(2rem, 5vw, 4.2rem); line-height: .95; margin: 1rem 0 .5rem; }
    h2 { margin-bottom: .4rem; }
    h3 { margin: .6rem 0 .2rem; font-size: 1.1rem; }
    h4 { margin: .4rem 0 .1rem; font-size: .95rem; color: var(--muted); }
    .hero { border-bottom: 1px solid var(--line); margin-bottom: 1.5rem; padding-bottom: 1rem; }
    .section { background: rgba(255,250,240,.82); border: 1px solid var(--line); border-radius: 18px; margin-bottom: 1rem; padding: 1rem 1.2rem; box-shadow: 0 14px 40px rgba(60,40,20,.08); }
    .grid { display: grid; gap: .8rem; grid-template-columns: repeat(auto-fit, minmax(150px, 1fr)); margin: 1rem 0; }
    .card { background: var(--panel); border: 1px solid var(--line); border-radius: 14px; padding: .9rem; }
    .card span, .card small, .meta { color: var(--muted); font-size: .92rem; }
    .card strong { display: block; font-size: 1.65rem; color: var(--accent); }
    pre { background: #231f1a; color: #f8ead1; padding: 1rem; overflow: auto; border-radius: 12px; max-height: 26rem; font-size: .85rem; }
    .warning { color: var(--danger); }
    .priority-group, .optimization-group { background: var(--panel); border: 1px solid var(--line); border-radius: 12px; padding: .8rem 1rem; margin-bottom: .6rem; }
    .priority-group ul, .optimization-group ul { margin: .2rem 0; padding-left: 1.2rem; }
    .priority-group li, .optimization-group li { margin: .15rem 0; }
    table { width: 100%; border-collapse: collapse; margin-top: .8rem; }
    th, td { border-bottom: 1px solid var(--line); padding: .55rem; text-align: left; }
    th { color: var(--muted); font-size: .85rem; text-transform: uppercase; letter-spacing: .04em; }
    nav a { color: var(--accent); margin-right: 1rem; }
    .badge { display: inline-block; background: var(--accent); color: #fff; border-radius: 10px; padding: .1rem .5rem; font-size: .8rem; }
    .badge-green { background: var(--green); }
    .btn-select-deck { background: var(--accent); color: #fff; border: none; border-radius: 8px; padding: .3rem .8rem; cursor: pointer; font-size: .85rem; }
    .btn-select-deck:hover { opacity: .85; }
    .deck-row:hover { background: rgba(180,83,9,.06); cursor: pointer; }

    /* Chat Panel */
    #chat-panel { display: none; position: fixed; bottom: 0; right: 0; width: 480px; max-width: 100vw; height: 600px; max-height: 80vh; background: var(--panel); border: 2px solid var(--accent); border-radius: 16px 0 0 0; box-shadow: -8px -8px 32px rgba(0,0,0,.15); flex-direction: column; z-index: 100; }
    #chat-panel.active { display: flex; }
    #chat-header { padding: .8rem 1rem; border-bottom: 1px solid var(--line); display: flex; justify-content: space-between; align-items: center; }
    #chat-header h3 { margin: 0; }
    #chat-close { background: none; border: none; font-size: 1.5rem; cursor: pointer; color: var(--muted); }
    #chat-messages { flex: 1; overflow-y: auto; padding: 1rem; }
    .chat-msg { margin-bottom: .8rem; padding: .6rem .8rem; border-radius: 12px; max-width: 90%; }
    .chat-msg.user { background: var(--accent); color: #fff; margin-left: auto; }
    .chat-msg.assistant { background: #f0e8d8; color: var(--ink); }
    .chat-msg.error { background: #fee; color: var(--danger); border: 1px solid var(--danger); }
    #chat-input-area { padding: .8rem; border-top: 1px solid var(--line); display: flex; gap: .5rem; }
    #chat-input { flex: 1; border: 1px solid var(--line); border-radius: 8px; padding: .5rem; font-family: inherit; }
    #chat-send { background: var(--accent); color: #fff; border: none; border-radius: 8px; padding: .5rem 1rem; cursor: pointer; }
    #chat-send:disabled { opacity: .5; cursor: default; }
    #chat-loading { font-size: .85rem; color: var(--muted); padding: .4rem; display: none; }

    /* Config Panel */
    #config-panel { margin-top: .5rem; padding: .5rem; background: #f0e8d8; border-radius: 8px; }
    #config-panel label { display: block; font-size: .85rem; color: var(--muted); margin-top: .3rem; }
    #config-panel input, #config-panel select { width: 100%; border: 1px solid var(--line); border-radius: 6px; padding: .3rem; font-size: .85rem; }
    #config-toggle { font-size: .85rem; color: var(--accent); cursor: pointer; }
    #config-fields { display: none; margin-top: .5rem; }
    #config-fields.open { display: block; }
  </style>
</head>
<body>
<main>
  <div class="hero">
    <p class="meta">Lokaler Advisor · LLM-gestützt · offline-first</p>
    <h1>MTGA Advisor</h1>
    <nav>
      <a href="/api/collection">collection.json</a>
      <a href="/api/decks">decks.json</a>
      <a href="/api/run-report">run-report.json</a>
      <a href="/api/advisor-result">advisor-result.json</a>
      <span id="config-toggle">⚙ LLM-Config</span>
    </nav>
  </div>

  <div id="config-panel">
    <div id="config-fields">
      <label>Endpoint</label>
      <input id="cfg-endpoint" value="$cfg_endpoint">
      <label>Modell</label>
      <input id="cfg-model" value="$cfg_model">
      <label>Temperature</label>
      <input id="cfg-temperature" type="number" step="0.1" min="0" max="2" value="$cfg_temp">
      <label>Max Tokens</label>
      <input id="cfg-max-tokens" type="number" step="64" min="64" value="$cfg_max_tokens">
      <button id="cfg-save" style="margin-top:.5rem;background:var(--green);color:#fff;border:none;border-radius:8px;padding:.4rem 1rem;cursor:pointer;">Speichern</button>
      <span id="cfg-status" style="margin-left:.5rem;font-size:.85rem;"></span>
    </div>
  </div>

  <div class="grid">
    $collection_summary
    $decks_summary
    $llm_advisor_summary
  </div>

  <div class="section">
    <h2>Decks <span class="badge">Phase 1.2</span></h2>
    <p class="meta">Klick auf ein Deck, um mit dem LLM zu chatten.</p>
    $decks_table
  </div>

  <div class="section">
    <h2>LLM Advisor <span class="badge badge-green">Phase 2</span></h2>
    <div class="warning">
      <h3>Warnings</h3>
      $advisor_warnings
    </div>
    <h3>Crafting Priorities</h3>
    $crafting_priorities
    <h3>Deck Optimizations</h3>
    $deck_optimizations
    <h3>Meta Notes</h3>
    $meta_notes
  </div>

  <div class="section">
    <h2>Collection</h2>
    <p>Completeness: <strong>$collection_completeness</strong></p>
    <div class="warning">
      <h3>Warnings</h3>
      $collection_warnings
    </div>
    <h3>JSON</h3>
    <pre>$collection_json</pre>
  </div>

  <div class="section">
    <h2>Run-Report</h2>
    <p>Completeness: <strong>$report_completeness</strong></p>
    <div class="warning">
      <h3>Warnings</h3>
      $report_warnings
    </div>
    <h3>JSON</h3>
    <pre>$report_json</pre>
  </div>
</main>

<!-- Chat Panel -->
  <div id="chat-panel">
  <div id="chat-header">
    <h3 id="chat-deck-name">Deck</h3>
    <button id="chat-close">×</button>
  </div>
  <div id="chat-messages"></div>
  <div id="chat-loading">LLM denkt...</div>
  <div id="chat-input-area">
    <input id="chat-input" placeholder="Frage zum Deck..." autocomplete="off">
    <button id="chat-send">Senden</button>
  </div>
</div>
<script src="/dashboard.js" defer></script>
</body>
</html>
""").substitute(
        collection_summary=_collection_summary(collection),
        decks_summary=_decks_summary(decks),
        llm_advisor_summary=_llm_advisor_summary(advisor_result),
        decks_table=_render_decks_table(decks),
        advisor_warnings=_render_list(advisor_warnings),
        crafting_priorities=_render_crafting_priorities(advisor_result),
        deck_optimizations=_render_deck_optimizations(advisor_result),
        meta_notes=_render_meta_notes(advisor_result),
        collection_completeness=html.escape(str(collection_diag["completeness"])),
        collection_warnings=_render_list(collection_diag["warnings"]),
        collection_json=html.escape(_format_json(collection)),
        report_completeness=html.escape(str(report_diag["completeness"])),
        report_warnings=_render_list(report_diag["warnings"]),
        report_json=html.escape(_format_json(run_report)),
        cfg_endpoint=html.escape(llm_config.endpoint),
        cfg_model=html.escape(llm_config.model_name),
        cfg_temp=llm_config.temperature,
        cfg_max_tokens=llm_config.max_tokens,
    )


class MtgaAdvisorHandler(BaseHTTPRequestHandler):
    server_version = "mtga-advisor/0.3"

    def do_GET(self) -> None:
        output_dir = self.server.output_dir

        if self.path == "/dashboard.js":
            try:
                _js_response(self, _dashboard_js(), status=HTTPStatus.OK)
            except OSError:
                _json_response(
                    self,
                    {"error": "not_found", "message": "dashboard.js fehlt."},
                    status=HTTPStatus.NOT_FOUND,
                )
            return

        if self.path == "/api/collection":
            payload = _read_json(output_dir / "collection.json")
            if payload is None:
                _json_response(self, {"error": "not_found", "message": "collection.json fehlt."}, status=HTTPStatus.NOT_FOUND)
                return
            _json_response(self, payload, status=HTTPStatus.OK)
            return

        if self.path == "/api/decks":
            payload = _read_json(output_dir / "decks.json")
            if payload is None:
                _json_response(self, {"error": "not_found", "message": "decks.json fehlt."}, status=HTTPStatus.NOT_FOUND)
                return
            _json_response(self, payload, status=HTTPStatus.OK)
            return

        if self.path == "/api/run-report":
            payload = _read_json(output_dir / "run-report.json")
            if payload is None:
                _json_response(self, {"error": "not_found", "message": "run-report.json fehlt."}, status=HTTPStatus.NOT_FOUND)
                return
            _json_response(self, payload, status=HTTPStatus.OK)
            return

        if self.path == "/api/advisor-result":
            payload = _read_json(output_dir / "advisor-result.json")
            if payload is None:
                _json_response(self, {"error": "not_found", "message": "advisor-result.json fehlt."}, status=HTTPStatus.NOT_FOUND)
                return
            _json_response(self, payload, status=HTTPStatus.OK)
            return

        if self.path == "/api/config":
            config = load_config()
            _json_response(self, config.to_dict(), status=HTTPStatus.OK)
            return

        if self.path == "/":
            collection = _read_json(output_dir / "collection.json")
            run_report = _read_json(output_dir / "run-report.json")
            deck = _read_json(output_dir / "arena_deck.json")
            advisor_result = _read_json(output_dir / "advisor-result.json")
            decks = _read_json(output_dir / "decks.json")
            llm_config = load_config()
            body = _render_index(collection, run_report, deck, advisor_result, decks, llm_config)
            _html_response(self, body, status=HTTPStatus.OK)
            return

        _json_response(self, {"error": "not_found", "message": "Pfad nicht gefunden."}, status=HTTPStatus.NOT_FOUND)

    def do_POST(self) -> None:
        output_dir = self.server.output_dir

        if self.path == "/api/chat":
            content_len = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(content_len)
            try:
                data = json.loads(body.decode("utf-8"))
            except json.JSONDecodeError:
                _json_response(self, {"error": "invalid JSON"}, status=HTTPStatus.BAD_REQUEST)
                return

            deck_id = data.get("deck_id")
            question = data.get("question", "").strip()
            if not question:
                _json_response(self, {"error": "Keine Frage"}, status=HTTPStatus.BAD_REQUEST)
                return

            # Deck finden
            decks = _read_json(output_dir / "decks.json")
            if not decks:
                _json_response(self, {"error": "decks.json fehlt"}, status=HTTPStatus.NOT_FOUND)
                return

            deck = None
            for d in decks.get("decks", []):
                if str(d.get("deckId")) == str(deck_id):
                    deck = d
                    break

            if not deck:
                _json_response(self, {"error": f"Deck {deck_id} nicht gefunden"}, status=HTTPStatus.NOT_FOUND)
                return

            # Collection laden
            collection = _read_json(output_dir / "collection.json")

            # Prompt bauen
            prompt = _build_chat_prompt(deck, collection, question)

            # Config laden (mit optionalen CLI-Overrides aus dem Request)
            cli_overrides = {}
            if data.get("endpoint"):
                cli_overrides["endpoint"] = data["endpoint"]
            if data.get("model"):
                cli_overrides["model_name"] = data["model"]
            if data.get("temperature") is not None:
                cli_overrides["temperature"] = float(data["temperature"])
            if data.get("max_tokens") is not None:
                cli_overrides["max_tokens"] = int(data["max_tokens"])

            config = load_config(cli_overrides=cli_overrides)

            # LLM aufrufen
            result = _call_llm_chat(config, prompt)
            _json_response(self, result, status=HTTPStatus.OK)
            return

        if self.path == "/api/config":
            content_len = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(content_len)
            try:
                data = json.loads(body.decode("utf-8"))
            except json.JSONDecodeError:
                _json_response(self, {"error": "invalid JSON"}, status=HTTPStatus.BAD_REQUEST)
                return

            # Config aktualisieren und speichern
            from advisor.llm_config import CONFIG_FILE
            current = load_config()
            if data.get("endpoint"):
                current.endpoint = data["endpoint"]
            if data.get("model_name"):
                current.model_name = data["model_name"]
            if data.get("temperature") is not None:
                current.temperature = float(data["temperature"])
            if data.get("max_tokens") is not None:
                current.max_tokens = int(data["max_tokens"])
            if data.get("system_prompt"):
                current.system_prompt = data["system_prompt"]
            if data.get("timeout"):
                current.timeout = int(data["timeout"])

            # Speichern
            CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
            payload = current.to_dict()
            payload["_comment"] = "MTGA Advisor LLM Config (via Dashboard gespeichert)"
            CONFIG_FILE.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )

            _json_response(self, {"status": "saved", "config": current.to_dict()}, status=HTTPStatus.OK)
            return

        _json_response(self, {"error": "not_found", "message": "Pfad nicht gefunden."}, status=HTTPStatus.NOT_FOUND)

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
