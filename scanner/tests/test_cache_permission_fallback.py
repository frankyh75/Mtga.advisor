import pytest

from scanner import card_database


def test_inaccessible_local_path_uses_existing_cache_with_warning(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(card_database, 'get_default_cache_dir', lambda: tmp_path)
    monkeypatch.setattr(card_database, '_lookup_file', lambda: tmp_path / 'cache.json')
    expected = {1: {'name': 'Known'}}
    card_database._write_lookup_cache(tmp_path / 'cache.json', expected, source='scryfall', source_path=None)
    def denied():
        raise PermissionError('ACL und x-Rechte prüfen')
    monkeypatch.setattr(card_database, 'get_macos_mtga_data_path', denied)
    assert card_database.load_card_database() == expected
    assert 'ACL und x-Rechte prüfen' in capsys.readouterr().out
    with pytest.raises(PermissionError, match='ACL'):
        card_database.load_card_database(refresh_cache=True)
    (tmp_path / 'cache.json').unlink()
    with pytest.raises(PermissionError, match='ACL'):
        card_database.load_card_database()
