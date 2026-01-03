from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable


class MissingLogsError(FileNotFoundError):
    """Raised when no MTGA log files can be found."""


@dataclass(frozen=True)
class PathStrategy:
    name: str
    platform: str
    variant: str
    description: str
    base_path: Path

    def candidate_logs(self) -> list[Path]:
        return [
            self.base_path / "Player.log",
            self.base_path / "Player-prev.log",
        ]


@dataclass(frozen=True)
class PathConfig:
    windows_local_low: Path | None = None
    windows_steam_userdata: Path | None = None
    macos_logs: Path | None = None
    macos_steam_userdata: Path | None = None
    custom_logs: list[Path] = field(default_factory=list)


@dataclass(frozen=True)
class LogDiscovery:
    found: list[Path]
    missing: list[Path]
    strategies: list[PathStrategy]
    validation_tasks: list["ValidationTask"]


@dataclass(frozen=True)
class ValidationTask:
    task_id: str
    title: str
    detail: str
    paths_to_check: list[Path]


WINDOWS_LOCAL_LOW_HINT = (
    "Setze 'windows_local_low' auf den Ordner 'AppData/LocalLow/Wizards Of The Coast/MTGA'."
)
MACOS_LOGS_HINT = (
    "Setze 'macos_logs' auf den Ordner '~/Library/Logs/Wizards Of The Coast/MTGA'."
)
STEAM_HINT = "Setze den Steam-Userdata-Pfad in der Konfiguration oder prüfe den Steam-Installationspfad."

OS_MATRIX = {
    "windows": ("standalone", "steam"),
    "macos": ("standalone", "steam"),
}


def detect_platform() -> str:
    import sys

    if sys.platform.startswith("win"):
        return "windows"
    if sys.platform == "darwin":
        return "macos"
    return "unknown"


def _default_windows_local_low() -> Path | None:
    home = Path.home()
    candidate = home / "AppData" / "LocalLow" / "Wizards Of The Coast" / "MTGA"
    return candidate if candidate.parts else None


def _default_windows_steam_userdata() -> Path | None:
    program_files = Path("C:/Program Files (x86)")
    candidate = program_files / "Steam" / "userdata"
    return candidate


def _default_macos_logs() -> Path | None:
    return Path.home() / "Library" / "Logs" / "Wizards Of The Coast" / "MTGA"


def _default_macos_steam_userdata() -> Path | None:
    return Path.home() / "Library" / "Application Support" / "Steam" / "userdata"


def build_strategies(platform: str, config: PathConfig | None = None) -> list[PathStrategy]:
    config = config or PathConfig()
    strategies: list[PathStrategy] = []

    if platform == "windows":
        local_low = config.windows_local_low or _default_windows_local_low()
        if local_low:
            strategies.append(
                PathStrategy(
                    name="windows-standalone",
                    platform="windows",
                    variant="standalone",
                    description="Windows Standalone (LocalLow)",
                    base_path=local_low,
                )
            )
        steam_userdata = config.windows_steam_userdata or _default_windows_steam_userdata()
        if steam_userdata:
            strategies.append(
                PathStrategy(
                    name="windows-steam",
                    platform="windows",
                    variant="steam",
                    description="Windows Steam userdata",
                    base_path=steam_userdata,
                )
            )
    elif platform == "macos":
        logs_path = config.macos_logs or _default_macos_logs()
        if logs_path:
            strategies.append(
                PathStrategy(
                    name="macos-standalone",
                    platform="macos",
                    variant="standalone",
                    description="macOS Logs",
                    base_path=logs_path,
                )
            )
        steam_userdata = config.macos_steam_userdata or _default_macos_steam_userdata()
        if steam_userdata:
            strategies.append(
                PathStrategy(
                    name="macos-steam",
                    platform="macos",
                    variant="steam",
                    description="macOS Steam userdata",
                    base_path=steam_userdata,
                )
            )

    for custom in config.custom_logs:
        strategies.append(
            PathStrategy(
                name=f"custom-{custom.name}",
                platform=platform,
                variant="custom",
                description="Custom log directory",
                base_path=custom,
            )
        )

    return strategies


def _expand_steam_candidates(strategy: PathStrategy) -> list[Path]:
    if strategy.variant != "steam":
        return strategy.candidate_logs()

    candidates = []
    for userdata_dir in _safe_iterdir(strategy.base_path):
        log_dir = userdata_dir / "760" / "remote" / "MTGA"
        candidates.extend(
            [
                log_dir / "Player.log",
                log_dir / "Player-prev.log",
            ]
        )
    return candidates


def _safe_iterdir(path: Path) -> Iterable[Path]:
    if not path.exists():
        return []
    try:
        return [p for p in path.iterdir() if p.is_dir()]
    except OSError:
        return []


def discover_logs(platform: str, config: PathConfig | None = None) -> LogDiscovery:
    strategies = build_strategies(platform, config)
    found: list[Path] = []
    missing: list[Path] = []

    for strategy in strategies:
        candidates = _expand_steam_candidates(strategy)
        for candidate in candidates:
            if candidate.exists():
                found.append(candidate)
            else:
                missing.append(candidate)

    validation_tasks = build_validation_tasks(strategies, found)

    if not found:
        message = _missing_logs_message(platform, strategies, config)
        raise MissingLogsError(message)

    return LogDiscovery(
        found=sorted(set(found)),
        missing=sorted(set(missing)),
        strategies=strategies,
        validation_tasks=validation_tasks,
    )


def build_validation_tasks(
    strategies: list[PathStrategy], found: list[Path]
) -> list[ValidationTask]:
    found_set = {path.resolve() for path in found}
    tasks: list[ValidationTask] = []

    for strategy in strategies:
        candidates = _expand_steam_candidates(strategy)
        if any(path.resolve() in found_set for path in candidates):
            continue
        hint = _strategy_hint(strategy)
        tasks.append(
            ValidationTask(
                task_id=f"validate-{strategy.name}",
                title=f"{strategy.platform.title()} {strategy.variant.title()}-Pfad prüfen",
                detail=f"Keine Logs im erwarteten Pfad gefunden. {hint}",
                paths_to_check=candidates,
            )
        )

    return tasks


def _strategy_hint(strategy: PathStrategy) -> str:
    if strategy.platform == "windows" and strategy.variant == "standalone":
        return WINDOWS_LOCAL_LOW_HINT
    if strategy.platform == "macos" and strategy.variant == "standalone":
        return MACOS_LOGS_HINT
    if strategy.variant == "steam":
        return STEAM_HINT
    return "Bitte einen gültigen Log-Pfad konfigurieren."


def _missing_logs_message(
    platform: str, strategies: list[PathStrategy], config: PathConfig | None
) -> str:
    hints: list[str] = [
        "Keine MTGA-Logdateien gefunden.",
        "Starte MTGA und versuche es erneut.",
    ]

    if platform == "windows":
        hints.append(WINDOWS_LOCAL_LOW_HINT)
    elif platform == "macos":
        hints.append(MACOS_LOGS_HINT)

    if config and config.custom_logs:
        hints.append("Prüfe die konfigurierten Custom-Log-Pfade.")

    strategies_detail = "\n".join(
        f"- {strategy.description}: {strategy.base_path}" for strategy in strategies
    )
    if strategies_detail:
        hints.append("Geprüfte Strategien:\n" + strategies_detail)

    return "\n".join(hints)
