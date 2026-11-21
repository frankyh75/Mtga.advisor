"""macOS-spezifische Pfade für MTGA-Daten."""

from __future__ import annotations

import os
from pathlib import Path


USERS_DIR = Path("/Users")
EPIC_DIR = USERS_DIR / "Shared" / "Epic Games" / "MagicTheGathering"
_RAW_SUFFIX = Path("MTGA_Data/Downloads/Raw")


def _access_error(path: Path, error: OSError) -> PermissionError:
    return PermissionError(
        error.errno,
        f"Kein Zugriff auf MTGA-Pfad {path}: POSIX-Rechte bzw. ACLs und "
        "Such-/x-Rechte auf allen Zwischenordnern sowie Leserechte prüfen. "
        "Der Pfad kann existieren; Berechtigungen werden nicht geändert.",
        str(path),
    )


def _is_readable_directory(path: Path) -> bool:
    # stat unterscheidet ENOENT von EACCES; scandir prüft Leserechte.
    import stat

    try:
        if not stat.S_ISDIR(path.stat().st_mode):
            return False
        with os.scandir(path) as entries:
            next(entries, None)
        return True
    except (FileNotFoundError, NotADirectoryError):
        return False
    except PermissionError as error:
        raise _access_error(path, error) from error


def _user_candidates(home: Path):
    support = home / "Library" / "Application Support"
    yield support / "com.wizards.mtga" / "Downloads" / "Raw"
    yield support / "MTGA" / _RAW_SUFFIX
    yield support / "Wizards Of The Coast" / "MTGA" / _RAW_SUFFIX
    yield support / "Wizards of the Coast" / "MTGA" / _RAW_SUFFIX
    yield support / "Steam/steamapps/common/MTGA" / _RAW_SUFFIX


def _epic_candidates():
    # os.walk unterdrückt Fehler sonst standardmäßig; explizit weiterreichen.
    def onerror(error: OSError) -> None:
        if isinstance(error, (FileNotFoundError, NotADirectoryError)):
            return
        if isinstance(error, PermissionError):
            raise _access_error(Path(error.filename or EPIC_DIR), error) from error
        raise error

    for root, dirs, _ in os.walk(EPIC_DIR, onerror=onerror, followlinks=False):
        dirs.sort()
        if Path(root).name == "MTGA_Data":
            yield Path(root) / "Downloads" / "Raw"
            dirs[:] = []


def get_macos_mtga_data_path() -> Path | None:
    """Findet lesbare Raw-Daten; Zugriffssperren lösen PermissionError aus.

    Das aktuelle Home wird vor weiteren Benutzerverzeichnissen durchsucht.
    Nicht erreichbare Kandidaten gelten nicht als fehlende Installationen.
    """
    home = Path.home()
    for candidate in _user_candidates(home):
        if _is_readable_directory(candidate):
            return candidate

    for candidate in (
        EPIC_DIR / "MTGA.app/Contents/Resources" / _RAW_SUFFIX,
        EPIC_DIR / _RAW_SUFFIX,
        Path("/Applications/MTGA.app/Contents/Resources") / _RAW_SUFFIX,
    ):
        if _is_readable_directory(candidate):
            return candidate

    for candidate in _epic_candidates():
        if _is_readable_directory(candidate):
            return candidate

    try:
        with os.scandir(USERS_DIR) as entries:
            homes = sorted(Path(entry.path) for entry in entries if entry.name != "Shared")
    except FileNotFoundError:
        homes = []
    except PermissionError as error:
        raise _access_error(USERS_DIR, error) from error
    for other_home in homes:
        if other_home == home:
            continue
        for candidate in _user_candidates(other_home):
            if _is_readable_directory(candidate):
                return candidate
    return None


def get_macos_log_path() -> Path:
    """Pfad zu MTGA Player.log auf macOS.

    Der Log-Pfad variiert bei manchen Installationen leicht in der Gross-/Kleinschreibung.
    """
    candidates = [
        Path.home() / "Library" / "Logs" / "Wizards Of The Coast" / "MTGA" / "Player.log",
        Path.home() / "Library" / "Logs" / "Wizards of the Coast" / "MTGA" / "Player.log",
    ]
    for p in candidates:
        if p.exists():
            return p
    return candidates[0]


def get_macos_mtga_process_names() -> tuple[str, ...]:
    """Mögliche MTGA-Prozessnamen auf macOS.

    MTGA läuft unter Rosetta 2, der Prozess heisst normalerweise 'MTGA'.
    Steam-Version könnte 'MTGA' oder 'MTGALauncher' heissen.
    """
    return ("MTGA", "MTGALauncher", "MTGArena")


def get_macos_mtga_process_name() -> str:
    """Name des bevorzugten MTGA-Prozesses auf macOS."""
    return get_macos_mtga_process_names()[0]


def get_default_cache_dir() -> Path:
    """Cache-Verzeichnis für Karten-DB und Anker.

    Wichtig: Läuft der Scan über ``sudo``, ist ``HOME`` = ``/var/root``.
    Dann würde die Anker-Datei in ``/var/root/.mtga_advisor`` landen — der
    aufrufende User sieht sie nie und muss die 5 Karten bei JEDEM Lauf neu
    eintippen. Deshalb wird das Home des aufrufenden Users bevorzugt.

    Reihenfolge:
      1. ``MTGA_ADVISOR_CACHE_DIR`` (explizit gesetzt — für Tests/Launcher)
      2. ``Path.home()`` — auch unter sudo, denn der Launcher setzt HOME
         bewusst auf das Home des MTGA-Besitzers (dort liegt die Karten-DB)
      3. Nur falls HOME das künstliche root-Home ist: Home des ``SUDO_USER``
    """
    override = os.environ.get("MTGA_ADVISOR_CACHE_DIR")
    if override:
        return Path(override).expanduser()

    home = Path.home()

    # HOME ist unter sudo auf /var/root gesetzt: kein echtes Zuhause, dort
    # liegt keine Karten-DB und der aufrufende User findet die Anker nie.
    # Nur DANN auf den echten Aufrufer zurückfallen — hat der Launcher HOME
    # dagegen schon korrigiert, bleibt seine Wahl bestehen.
    root_homes = {Path("/var/root"), Path("/private/var/root")}
    if home in root_homes:
        sudo_user = os.environ.get("SUDO_USER")
        if sudo_user:
            try:
                import pwd

                return Path(pwd.getpwnam(sudo_user).pw_dir) / ".mtga_advisor"
            except (KeyError, ImportError):
                pass

    return home / ".mtga_advisor"
