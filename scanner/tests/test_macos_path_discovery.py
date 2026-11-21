import errno
from pathlib import Path

import pytest

from scanner import card_database, macos_paths


@pytest.fixture
def paths(tmp_path, monkeypatch):
    users = tmp_path / "Users"
    home = users / "agent"
    home.mkdir(parents=True)
    monkeypatch.setattr(Path, "home", lambda: home)
    monkeypatch.setattr(macos_paths, "USERS_DIR", users)
    monkeypatch.setattr(macos_paths, "EPIC_DIR", users / "Shared/Epic Games/MagicTheGathering")
    return users, home


def test_finds_other_user_wizards_data(paths):
    users, _ = paths
    raw = users / "frank/Library/Application Support/Wizards Of The Coast/MTGA/MTGA_Data/Downloads/Raw"
    raw.mkdir(parents=True)
    assert macos_paths.get_macos_mtga_data_path() == raw


def test_finds_nested_epic_installation(paths):
    users, _ = paths
    raw = users / "Shared/Epic Games/MagicTheGathering/version/MTGA.app/Contents/Resources/MTGA_Data/Downloads/Raw"
    raw.mkdir(parents=True)
    assert macos_paths.get_macos_mtga_data_path() == raw


def test_missing_paths_are_not_permission_errors(paths):
    assert macos_paths.get_macos_mtga_data_path() is None


def test_inaccessible_intermediate_directory_does_not_fall_back(paths, monkeypatch):
    users, home = paths
    (users / "frank").mkdir()
    original = Path.stat

    def denied(path, *args, **kwargs):
        if str(path).startswith(str(users / "frank")):
            raise PermissionError(errno.EACCES, "Permission denied", str(path))
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, "stat", denied)
    with pytest.raises(PermissionError, match=r"ACLs.*x-Rechte.*Zwischenordner") as exc:
        card_database.load_local_mtga_database()
    assert exc.value.errno == errno.EACCES
    assert "frank" in str(exc.value)


def test_unreadable_raw_directory_raises(paths, monkeypatch):
    _, home = paths
    raw = home / "Library/Application Support/MTGA/MTGA_Data/Downloads/Raw"
    raw.mkdir(parents=True)
    original = macos_paths.os.scandir

    def denied(path):
        if Path(path) == raw:
            raise PermissionError(errno.EACCES, "Permission denied", str(path))
        return original(path)

    monkeypatch.setattr(macos_paths.os, "scandir", denied)
    with pytest.raises(PermissionError, match="Leserechte"):
        macos_paths.get_macos_mtga_data_path()


def test_epic_walk_permission_error_not_ignored(paths, monkeypatch):
    epic = macos_paths.EPIC_DIR
    epic.mkdir(parents=True)
    original = macos_paths.os.scandir

    def denied(path):
        if Path(path) == epic:
            raise PermissionError(errno.EACCES, "Permission denied", str(path))
        return original(path)

    monkeypatch.setattr(macos_paths.os, "scandir", denied)
    with pytest.raises(PermissionError, match="ACLs"):
        macos_paths.get_macos_mtga_data_path()
