"""Tests für die HOME-Auflösung des Cache-/Anker-Ordners.

Hintergrund: Läuft der Scan über ``sudo``, ist HOME=/var/root. Dann landet
``last_anchors.json`` in ``/var/root/.mtga_advisor`` — der aufrufende User
sieht die Datei nie und muss die 5 Anker-Karten bei JEDEM Lauf neu eintippen.
Genau das war der gemeldete Fehler ("fragt mich nach ankern").

Diese Tests sichern die Auflösungsreihenfolge ab und stellen sicher, dass
eine vom Launcher bereits korrigierte HOME-Einstellung nicht überschrieben
wird (der Launcher setzt HOME auf das Home des MTGA-Besitzers, weil dort die
Karten-DB liegt).
"""

from __future__ import annotations

import os
import pwd
from pathlib import Path

import pytest

from scanner.macos_paths import get_default_cache_dir


@pytest.fixture(autouse=True)
def _clear_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keine Umgebungs-Reste aus anderen Tests."""
    monkeypatch.delenv("MTGA_ADVISOR_CACHE_DIR", raising=False)
    monkeypatch.delenv("SUDO_USER", raising=False)


def test_explicit_cache_dir_wins(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MTGA_ADVISOR_CACHE_DIR", str(tmp_path / "explicit"))
    assert get_default_cache_dir() == tmp_path / "explicit"


def test_normal_home_used_as_is(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Ohne sudo: schlicht HOME/.mtga_advisor."""
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))

    assert get_default_cache_dir() == tmp_path / ".mtga_advisor"


def test_root_home_falls_back_to_sudo_user(monkeypatch: pytest.MonkeyPatch) -> None:
    """Als root mit HOME=/var/root: Home des echten Aufrufers nehmen.

    Geprüft wird gegen den echten passwd-Eintrag des aktuellen Users, damit
    der Test den tatsächlichen Pfad prüft und nicht nur "nicht /var/root".
    """
    monkeypatch.setenv("HOME", "/var/root")
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: Path("/var/root")))

    current_user = pwd.getpwuid(os.getuid()).pw_name
    monkeypatch.setenv("SUDO_USER", current_user)

    expected = Path(pwd.getpwnam(current_user).pw_dir) / ".mtga_advisor"
    assert get_default_cache_dir() == expected
    assert "/var/root" not in str(get_default_cache_dir())


def test_launcher_corrected_home_is_respected(monkeypatch: pytest.MonkeyPatch) -> None:
    """Der Launcher setzt HOME bereits auf das Home des MTGA-Besitzers.

    Diese Entscheidung darf NICHT vom SUDO_USER-Fallback überschrieben werden —
    sonst sucht der Scanner die Karten-DB im falschen Zuhause.
    """
    corrected = Path("/Users/frankhermann")
    monkeypatch.setenv("HOME", str(corrected))
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: corrected))
    monkeypatch.setenv("SUDO_USER", "agent")

    assert get_default_cache_dir() == corrected / ".mtga_advisor"


def test_sudo_user_without_passwd_entry_does_not_crash(monkeypatch: pytest.MonkeyPatch) -> None:
    """Unbekannter SUDO_USER darf keinen Fehler werfen."""
    monkeypatch.setenv("HOME", "/var/root")
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: Path("/var/root")))
    monkeypatch.setenv("SUDO_USER", "does-not-exist-12345")

    result = get_default_cache_dir()
    assert result.name == ".mtga_advisor"
