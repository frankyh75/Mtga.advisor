"""LLM-gestützter MTGA Advisor.

Nimmt Collection + Decks und erzeugt per lokalem LLM:
- Wildcard-Crafting-Prioritäten (was zuerst craften)
- Deck-Optimierungsvorschläge (Mana-Kurve, Synergien, Sideboard)
- Meta-Relevanz-Bewertung

Nutzt llama.cpp Server (Port 8081, ornith:35b) oder fallback auf Port 8080 (qwen3.5:9b).
"""

from __future__ import annotations

import json
import re
import urllib.request
import urllib.error
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


LLAMA_ENDPOINT = "http://127.0.0.1:8081/v1/chat/completions"
QWEN_ENDPOINT = "http://127.0.0.1:8080/v1/chat/completions"
REQUEST_TIMEOUT = 120


@dataclass
class LLMAdvisorResult:
    schema: str = "llm-advisor.v1"
    generatedAt: str = ""
    summary: dict[str, Any] = field(default_factory=dict)
    craftingPriorities: list[dict[str, Any]] = field(default_factory=list)
    deckOptimizations: list[dict[str, Any]] = field(default_factory=list)
    metaNotes: list[str] = field(default_factory=list)
    rawResponse: str = ""
    model: str = ""
    warnings: list[str] = field(default_factory=list)


def run_llm_advisor(
    collection_path: Path,
    decks_path: Path | None = None,
    output_dir: Path | None = None,
    model_endpoint: str | None = None,
) -> LLMAdvisorResult:
    """Führe den LLM-Advisor aus.

    Args:
        collection_path: Pfad zu collection.json.
        decks_path: Optional, Pfad zu decks.json.
        output_dir: Optional, schreibt advisor-result.json.
        model_endpoint: Optional, überschreibt LLM-Endpoint.

    Returns:
        LLMAdvisorResult mit Empfehlungen.
    """
    result = LLMAdvisorResult()
    result.generatedAt = _iso_now()

    # 1. Daten laden
    collection = _load_json(collection_path)
    if collection is None:
        result.warnings.append("collection.json nicht gefunden oder ungültig")
        return result

    decks = None
    if decks_path and decks_path.exists():
        decks = _load_json(decks_path)

    # 2. Prompt bauen
    prompt = _build_prompt(collection, decks)

    # 3. LLM aufrufen
    endpoint = model_endpoint or _detect_endpoint()
    result.model = endpoint

    try:
        raw = _call_llm(endpoint, prompt)
        result.rawResponse = raw
    except Exception as exc:
        result.warnings.append(f"LLM-Aufruf fehlgeschlagen: {exc}")
        return result

    # 4. Antwort parsen
    _parse_response(result, raw)

    # 5. Optional schreiben
    if output_dir:
        output_dir.mkdir(parents=True, exist_ok=True)
        path = output_dir / "advisor-result.json"
        path.write_text(
            json.dumps(_result_to_dict(result), ensure_ascii=False, sort_keys=True, indent=2) + "\n",
            encoding="utf-8",
        )

    return result


def _load_json(path: Path) -> dict[str, Any] | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def _detect_endpoint() -> str:
    """Prüfe welcher LLM-Server läuft, bevorzuge ornith:35b."""
    import socket

    for endpoint in [LLAMA_ENDPOINT, QWEN_ENDPOINT]:
        host, port_part = endpoint.split("://")[1].split("/")[0].split(":")
        try:
            with socket.create_connection((host, int(port_part)), timeout=1):
                return endpoint
        except (OSError, ValueError):
            continue
    return LLAMA_ENDPOINT  # Default


def _build_prompt(collection: dict[str, Any], decks: dict[str, Any] | None) -> str:
    """Baue den Prompt für den LLM-Advisor.

    Enthält: Wildcard-Bestand, Decks mit fehlenden Karten, Anweisungen.
    """
    cards = collection.get("cards", {})
    wildcards = collection.get("wildcards", {})
    total_cards = sum(int(v) for v in cards.values()) if isinstance(cards, dict) else 0
    unique_cards = len(cards) if isinstance(cards, dict) else 0

    lines = [
        "Du bist ein MTG Arena Deck-Building Advisor. Analysiere die folgende Sammlung und Decks.",
        "",
        "## Sammlung",
        f"- {unique_cards} unique Karten, {total_cards} total",
    ]

    if wildcards:
        wc_lines = []
        for key in ("wcCommon", "wcUncommon", "wcRare", "wcMythic"):
            val = wildcards.get(key, 0)
            label = key.replace("wc", "").lower()
            wc_lines.append(f"  - {label}: {val}")
        lines.append("- Wildcards:")
        lines.extend(wc_lines)
    else:
        lines.append("- Wildcards: unbekannt")

    # Decks
    if decks:
        deck_list = decks.get("decks", [])
        lines.append(f"\n## Decks ({len(deck_list)} gesamt)")

        for deck in deck_list[:15]:  # Max 15 Decks im Prompt
            name = deck.get("name", "Unnamed")
            fmt = deck.get("attributes", {}).get("Format", "unknown")
            deck_id = deck.get("deckId", "?")
            lines.append(f"\n### {name} ({fmt})")
            lines.append(f"  - DeckId: {deck_id}")

            # Format-Legalities
            legalities = deck.get("formatLegalities", {})
            legal_formats = [f for f, v in legalities.items() if v]
            if legal_formats:
                lines.append(f"  - Legal in: {', '.join(legal_formats[:5])}")

        if len(deck_list) > 15:
            lines.append(f"\n... und {len(deck_list) - 15} weitere Decks")
    else:
        lines.append("\n## Decks: keine gefunden")

    lines.extend([
        "",
        "## Aufgabe",
        "",
        "Gib eine strukturierte Analyse in folgendem JSON-Format (nur JSON, kein Markdown drumherum):",
        "",
        """{
  "summary": {
    "totalDecks": <anzahl>,
    "analysedDecks": <anzahl analysierte>,
    "topPriority": "<kurze Zusammenfassung der höchsten Priorität>"
  },
  "craftingPriorities": [
    {
      "reason": "<warum>",
      "cards": [
        {"name": "<Kartenname>", "count": <anzahl>, "rarity": "<rarity>", "forDecks": ["<Deckname>", ...]}
      ]
    }
  ],
  "deckOptimizations": [
    {
      "deckName": "<Name>",
      "format": "<Format>",
      "issues": ["<Problem 1>", ...],
      "suggestions": ["<Vorschlag 1>", ...],
      "craftingNeeded": "<kurze Einschätzung>"
    }
  ],
  "metaNotes": [
    "<Hinweis 1>",
    "<Hinweis 2>"
  ]
}""",
        "",
        "Wichtig:",
        "- Priorisiere Rares und Mythics (teuerste Wildcards zuerst)",
        "- Nenne konkrete Kartennamen, keine Platzhalter",
        "- Berücksichtige Meta-Relevanz (was wird gerade viel gespielt)",
        "- Wenn Wildcards unbekannt sind, sag 'what-if' statt konkreter Craft-Anweisung",
        "- Maximal 10 Crafting-Prioritäten, maximal 5 Deck-Optimierungen",
        "- Sei präzise und hilfreich, kein Marketing-Sprech",
    ])

    return "\n".join(lines)


def _call_llm(endpoint: str, prompt: str) -> str:
    """Rufe den llama.cpp Chat-Completion-Endpoint auf."""
    payload = {
        "messages": [
            {
                "role": "system",
                "content": "Du bist ein MTGA Deck-Building Experte. Antworte ausschließlich mit validem JSON, keinem anderen Text.",
            },
            {"role": "user", "content": prompt},
        ],
        "temperature": 0.3,
        "max_tokens": 4096,
        "stop": [],
    }

    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        endpoint,
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )

    try:
        with urllib.request.urlopen(req, timeout=REQUEST_TIMEOUT) as resp:
            body = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        error_body = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"HTTP {exc.code}: {error_body}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"LLM nicht erreichbar ({endpoint}): {exc.reason}") from exc

    choices = body.get("choices", [])
    if not choices:
        raise RuntimeError(f"LLM gab keine Choices zurück: {json.dumps(body, indent=2)[:500]}")

    content = choices[0].get("message", {}).get("content", "")
    if not content:
        raise RuntimeError("LLM gab leere Antwort")

    return content


def _parse_response(result: LLMAdvisorResult, raw: str) -> None:
    """Parse die LLM-Antwort in strukturierte Daten.

    Versucht JSON aus der Antwort zu extrahieren (auch wenn Markdown-Code-Blöcke drum sind).
    """
    # JSON aus Markdown-Code-Block extrahieren
    json_str = raw.strip()

    # ```json ... ``` oder ``` ... ``` entfernen
    code_block = re.search(r"```(?:json)?\s*\n?(.*?)\n?```", json_str, re.DOTALL)
    if code_block:
        json_str = code_block.group(1).strip()

    # JSON parsen
    try:
        data = json.loads(json_str)
    except json.JSONDecodeError:
        # Versuche geschweifte Klammern zu finden
        brace_start = json_str.find("{")
        brace_end = json_str.rfind("}")
        if brace_start >= 0 and brace_end > brace_start:
            try:
                data = json.loads(json_str[brace_start : brace_end + 1])
            except json.JSONDecodeError:
                result.warnings.append("LLM-Antwort enthielt kein valides JSON")
                return
        else:
            result.warnings.append("LLM-Antwort enthielt kein JSON")
            return

    # Strukturierte Felder extrahieren
    result.summary = data.get("summary", {})
    result.craftingPriorities = data.get("craftingPriorities", [])
    result.deckOptimizations = data.get("deckOptimizations", [])
    result.metaNotes = data.get("metaNotes", [])


def _result_to_dict(result: LLMAdvisorResult) -> dict[str, Any]:
    return {
        "schema": result.schema,
        "generatedAt": result.generatedAt,
        "model": result.model,
        "summary": result.summary,
        "craftingPriorities": result.craftingPriorities,
        "deckOptimizations": result.deckOptimizations,
        "metaNotes": result.metaNotes,
        "warnings": result.warnings,
    }


def _iso_now() -> str:
    return (
        datetime.now(timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z")
    )
