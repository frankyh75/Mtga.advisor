"""LLM-gestützter MTGA Advisor.

Nimmt Collection + Decks und erzeugt per LLM:
- Wildcard-Crafting-Prioritäten (was zuerst craften)
- Deck-Optimierungsvorschläge (Mana-Kurve, Synergien, Sideboard)
- Meta-Relevanz-Bewertung

LLM-Konfiguration über LLMConfig (siehe llm_config.py):
- Config-Datei: ~/.config/mtga-advisor/config.json oder mtga-advisor.json
- Umgebungsvariablen: MTGA_LLM_ENDPOINT, MTGA_LLM_TEMPERATURE, etc.
- CLI-Argumente
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

from .llm_config import LLMConfig, load_config


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
    llm_config: LLMConfig | None = None,
    cli_overrides: dict[str, Any] | None = None,
) -> LLMAdvisorResult:
    """Führe den LLM-Advisor aus.

    Args:
        collection_path: Pfad zu collection.json.
        decks_path: Optional, Pfad zu decks.json.
        output_dir: Optional, schreibt advisor-result.json.
        llm_config: Optional, LLMConfig-Objekt. Wenn nicht gesetzt, wird
                    automatisch geladen (Config-Datei → Env → Defaults).
        cli_overrides: Optional, Dict mit CLI-Override-Werten
                       (z.B. {"endpoint": "...", "temperature": 0.5}).

    Returns:
        LLMAdvisorResult mit Empfehlungen.
    """
    result = LLMAdvisorResult()
    result.generatedAt = _iso_now()

    # 1. Config laden
    config = llm_config or load_config(cli_overrides=cli_overrides)
    result.model = config.model_name

    # 2. Daten laden
    collection = _load_json(collection_path)
    if collection is None:
        result.warnings.append("collection.json nicht gefunden oder ungültig")
        return result

    decks = None
    if decks_path and decks_path.exists():
        decks = _load_json(decks_path)

    # 3. Prompt bauen
    prompt = _build_prompt(collection, decks)

    # 4. LLM aufrufen
    try:
        raw = _call_llm(config, prompt)
        result.rawResponse = raw
    except Exception as exc:
        result.warnings.append(f"LLM-Aufruf fehlgeschlagen: {exc}")
        return result

    # 5. Antwort parsen
    _parse_response(result, raw)

    # 6. Optional schreiben
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

            # Deck cards (from decks-container or deck-scan format)
            cards = deck.get("cards", {})
            if cards:
                deck_lines = _format_deck_cards_for_prompt(cards, max_cards_per_pile=60)
                lines.extend(deck_lines)

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


def _format_deck_cards_for_prompt(cards: dict[str, Any], max_cards_per_pile: int = 60) -> list[str]:
    """Format deck cards for the LLM prompt.

    Supports two formats:
    - Named card lists: {"mainboard": [{"cardId": 123, "name": "Lightning Bolt", "count": 4}, ...]}
    - grpId→qty dicts:  {"mainboard": {"123": 4, "456": 2}, ...}

    Args:
        max_cards_per_pile: Maximum number of card entries per pile to include
            in the prompt. Excess entries are truncated with a summary count.
    """
    pile_labels = [
        ("mainboard", "Mainboard"),
        ("sideboard", "Sideboard"),
        ("commandZone", "Command Zone"),
        ("companions", "Companions"),
    ]
    result: list[str] = []
    for key, label in pile_labels:
        pile = cards.get(key)
        if not pile:
            continue
        if isinstance(pile, list):
            # Named card list format
            entries = []
            for card in pile[:max_cards_per_pile]:
                name = card.get("name") or f"ID:{card.get('cardId', '?')}"
                count = card.get("count", 1)
                entries.append(f"{count}x {name}")
            if entries:
                suffix = ""
                if len(pile) > max_cards_per_pile:
                    suffix = f" (+{len(pile) - max_cards_per_pile} more)"
                result.append(f"  - {label} ({len(pile)} unique): {', '.join(entries)}{suffix}")
        elif isinstance(pile, dict):
            # grpId→qty format (no names available)
            items = sorted(pile.items())
            entries = [f"{qty}x ID:{grp_id}" for grp_id, qty in items[:max_cards_per_pile]]
            if entries:
                suffix = ""
                if len(items) > max_cards_per_pile:
                    suffix = f" (+{len(items) - max_cards_per_pile} more)"
                result.append(f"  - {label} ({len(items)} unique): {', '.join(entries)}{suffix}")
    return result


def _call_llm(config: LLMConfig, prompt: str) -> str:
    """Rufe den Chat-Completion-Endpoint auf (OpenAI-kompatibel)."""
    payload = {
        "messages": [
            {"role": "system", "content": config.system_prompt},
            {"role": "user", "content": prompt},
        ],
        "temperature": config.temperature,
        "max_tokens": config.max_tokens,
        "stop": [],
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
        raise RuntimeError(f"HTTP {exc.code}: {error_body}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(
            f"LLM nicht erreichbar ({config.endpoint}): {exc.reason}"
        ) from exc

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
