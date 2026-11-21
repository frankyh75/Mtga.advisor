from __future__ import annotations

import gzip
import io
import json
import sys
from types import SimpleNamespace

import pytest

from scanner import card_database


@pytest.mark.parametrize("compressed", [False, True])
def test_refresh_reports_missing_source_records_and_not_on_cache_load(tmp_path, monkeypatch, capsys, compressed):
    cards = ([{"name": "Known", "set": "abc", "arena_id": 123}]
             + [{"set": "trk"}] * 3 + [{"set": "fra"}] * 2
             + [{"set": f"s{i:02}"} for i in range(12)])
    payload = gzip.compress(b"\n".join(json.dumps(card).encode() for card in cards))
    url = "https://example.invalid/cards.jsonl.gz" if compressed else "https://example.invalid/cards.json"

    class Response:
        def __init__(self, metadata=False):
            self.metadata = metadata
            self.raw = io.BytesIO(payload)

        def raise_for_status(self):
            pass

        def json(self):
            return {"download_uri": url} if self.metadata else cards

    monkeypatch.setitem(sys.modules, "requests", SimpleNamespace(get=lambda url, **kw: Response("bulk-data" in url)))
    monkeypatch.setattr(card_database, "get_default_cache_dir", lambda: tmp_path)
    monkeypatch.setattr(card_database, "get_macos_mtga_data_path", lambda: None)
    lookup = card_database.load_card_database(refresh_cache=True)
    output = capsys.readouterr().out
    assert list(lookup) == [123]
    assert "source=scryfall, cardCount=1" in output
    assert "TRK: 3, FRA: 2" in output
    assert "S07: 1" in output and "S08: 1" not in output
    assert "nicht notwendigerweise" in output
    assert "vollständig fehlende Sets sind nicht erkennbar" in output
    assert card_database.load_card_database() == lookup
    assert "Refresh-Report" not in capsys.readouterr().out


def test_refresh_local_reports_coverage_unknown(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(card_database, "get_default_cache_dir", lambda: tmp_path)
    monkeypatch.setattr(card_database, "get_macos_mtga_data_path", lambda: tmp_path)
    monkeypatch.setattr(card_database, "load_local_mtga_database", lambda: {42: {"set": "TRK"}})
    assert len(card_database.load_card_database(refresh_cache=True)) == 1
    output = capsys.readouterr().out
    assert "source=local-mtga, cardCount=1" in output
    assert "nicht ermittelt" in output


def test_refresh_failed_download_does_not_claim_no_gaps(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(card_database, "get_default_cache_dir", lambda: tmp_path)
    monkeypatch.setattr(card_database, "get_macos_mtga_data_path", lambda: None)
    def fail(*args, **kwargs):
        raise OSError("offline")
    monkeypatch.setitem(sys.modules, "requests", SimpleNamespace(get=fail))
    assert card_database.load_card_database(refresh_cache=True) == {}
    output = capsys.readouterr().out
    assert "source=scryfall, cardCount=0" in output
    assert "nicht ermittelt" in output
