"""LLM-Konfiguration für den MTGA Advisor.

Unterstützt:
- Config-Datei (JSON/YAML) in ~/.config/mtga-advisor/config.json
- Config-Datei im Projektverzeichnis (mtga-advisor.json)
- Umgebungsvariablen (MTGA_LLM_*)
- CLI-Argumente (überschreiben alles)
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any


# Standard-Pfade
CONFIG_DIR = Path.home() / ".config" / "mtga-advisor"
CONFIG_FILE = CONFIG_DIR / "config.json"
PROJECT_CONFIG = Path("mtga-advisor.json")


@dataclass
class LLMConfig:
    """Konfiguration für den LLM-Advisor.

    Fields:
        endpoint: Vollständige URL zum Chat-Completion-Endpoint.
        model_name: Modellname (für Logging/Anzeige).
        temperature: Sampling-Temperatur (0.0 - 1.0).
        max_tokens: Maximale Token-Anzahl für die Antwort.
        system_prompt: System-Prompt für das LLM.
        timeout: Timeout in Sekunden für den HTTP-Request.
    """
    endpoint: str = "http://127.0.0.1:8081/v1/chat/completions"
    model_name: str = "ornith:35b"
    temperature: float = 0.3
    max_tokens: int = 4096
    system_prompt: str = (
        "Du bist ein MTGA Deck-Building Experte. "
        "Antworte ausschließlich mit validem JSON, keinem anderen Text."
    )
    timeout: int = 120

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def load_config(
    config_path: Path | None = None,
    cli_overrides: dict[str, Any] | None = None,
) -> LLMConfig:
    """Lade LLM-Konfiguration mit folgender Priorität (niedrig → hoch):

    1. Default-Werte
    2. Config-Datei (mtga-advisor.json im Projekt oder ~/.config/mtga-advisor/config.json)
    3. Umgebungsvariablen (MTGA_LLM_ENDPOINT, MTGA_LLM_TEMPERATURE, etc.)
    4. CLI-Argumente (über cli_overrides)

    Args:
        config_path: Expliziter Pfad zu einer Config-Datei.
        cli_overrides: Dict mit CLI-Override-Werten.

    Returns:
        LLMConfig mit gemergten Werten.
    """
    config = LLMConfig()

    # 1. Config-Datei laden
    file_config = _load_config_file(config_path)
    if file_config:
        for key, value in file_config.items():
            if hasattr(config, key) and value is not None:
                setattr(config, key, value)

    # 2. Umgebungsvariablen
    env_map = {
        "MTGA_LLM_ENDPOINT": "endpoint",
        "MTGA_LLM_MODEL": "model_name",
        "MTGA_LLM_TEMPERATURE": "temperature",
        "MTGA_LLM_MAX_TOKENS": "max_tokens",
        "MTGA_LLM_TIMEOUT": "timeout",
        "MTGA_LLM_SYSTEM_PROMPT": "system_prompt",
    }
    for env_key, config_key in env_map.items():
        env_val = os.environ.get(env_key)
        if env_val is not None:
            current = getattr(config, config_key)
            if isinstance(current, float):
                setattr(config, config_key, float(env_val))
            elif isinstance(current, int):
                setattr(config, config_key, int(env_val))
            else:
                setattr(config, config_key, env_val)

    # 3. CLI-Overrides
    if cli_overrides:
        for key, value in cli_overrides.items():
            if hasattr(config, key) and value is not None:
                setattr(config, key, value)

    return config


def _load_config_file(config_path: Path | None) -> dict[str, Any] | None:
    """Versuche Config aus Datei zu laden.

    Suchreihenfolge:
    1. Expliziter Pfad (config_path)
    2. mtga-advisor.json im aktuellen Verzeichnis
    3. ~/.config/mtga-advisor/config.json
    """
    candidates: list[Path] = []
    if config_path:
        candidates.append(config_path)
    candidates.append(PROJECT_CONFIG)
    candidates.append(CONFIG_FILE)

    for path in candidates:
        resolved = path.expanduser().resolve() if "~" in str(path) else path.resolve()
        if not resolved.exists():
            continue
        try:
            raw = resolved.read_text(encoding="utf-8")
            if resolved.suffix in (".yaml", ".yml"):
                try:
                    import yaml
                    data = yaml.safe_load(raw)
                except ImportError:
                    # Fallback: JSON parsen
                    data = json.loads(raw)
            else:
                data = json.loads(raw)
            if isinstance(data, dict):
                return data
        except (OSError, json.JSONDecodeError, ValueError):
            continue

    return None


def write_default_config(path: Path | None = None) -> Path:
    """Schreibe eine Default-Config-Datei.

    Args:
        path: Zielpfad. Default: ~/.config/mtga-advisor/config.json.

    Returns:
        Pfad zur geschriebenen Datei.
    """
    target = path or CONFIG_FILE
    target.parent.mkdir(parents=True, exist_ok=True)

    config = LLMConfig()
    payload = config.to_dict()
    # Kommentare als Beschreibung hinzufügen
    payload["_comment"] = (
        "MTGA Advisor LLM Config. "
        "Setze endpoint auf deinen lokalen llama.cpp Server "
        "oder einen kompatiblen OpenAI-API-Endpoint."
    )

    target.write_text(
        json.dumps(payload, ensure_ascii=False, sort_keys=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return target


def _detect_endpoint() -> str:
    """Prüfe welcher LLM-Server läuft, bevorzuge ornith:35b (Port 8081)."""
    import socket

    for host, port in [("127.0.0.1", 8081), ("127.0.0.1", 8080)]:
        try:
            with socket.create_connection((host, port), timeout=1):
                return f"http://{host}:{port}/v1/chat/completions"
        except (OSError, ValueError):
            continue
    return "http://127.0.0.1:8081/v1/chat/completions"
