import json
import threading
import urllib.request

from server.app import MtgaAdvisorHandler, MtgaAdvisorServer


def test_api_preserves_unresolved_cards_when_saving(tmp_path, monkeypatch):
    db = {1: {"name": "Known", "set": "ABC", "collector_number": "1", "rarity": "common"}}
    monkeypatch.setattr("scanner.card_database.load_card_database", lambda: db)
    (tmp_path / "collection.json").write_text(json.dumps({"cards": {"1": 56}}))
    server = MtgaAdvisorServer(("127.0.0.1", 0), MtgaAdvisorHandler, tmp_path)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        payload = json.dumps({"deck_text": "Deck\n56 Known (XYZ) 8\n4 Unknown (TRK) 221"}).encode()
        request = urllib.request.Request(
            f"http://127.0.0.1:{server.server_port}/api/deck-import",
            data=payload, headers={"Content-Type": "application/json"}, method="POST",
        )
        with urllib.request.urlopen(request, timeout=5) as response:
            result = json.load(response)
        assert result["saved"] is True
        assert result["summary"]["totalCards"] == 60
        assert result["summary"]["ownedCards"] == 56
        assert result["summary"]["unresolvedCards"] == 4
        assert result["summary"]["scoreBasis"] == {"owned": 56, "resolved": 56, "total": 60}
        assert result["summary"]["unknownSets"] == ["TRK"]
        assert "arena-id-unbekannt" in result["warnings"]
        saved = json.loads((tmp_path / "deck-cards.json").read_text())
        assert saved["decks"][0]["unresolvedCards"][0]["quantity"] == 4
    finally:
        server.shutdown()
        thread.join(timeout=5)
        server.server_close()
