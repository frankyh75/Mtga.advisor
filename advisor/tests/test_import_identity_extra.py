from advisor.analyze import analyze_deck, arena_deck_to_deck_cards
from advisor.deck_import import import_arena_deck


def test_unknown_without_suffix_counts_through_analysis():
    imported = import_arena_deck('Deck\n2 Missing Card\n', card_db={}, deck_format='standard')
    result = analyze_deck(deck=arena_deck_to_deck_cards(imported), collection={}, wildcards={})
    assert result['summary']['totalCards'] == 2
    assert result['summary']['unresolvedCards'] == 2
    assert result['unresolvedCards'][0]['reason'] == 'unknown-card-name'
    assert 'arena-id-unbekannt' not in result['warnings']


def test_known_name_wins_over_conflicting_set_collector():
    db = {
        1: {'name': 'Known', 'set': 'DEF', 'collector_number': '2'},
        2: {'name': 'Other', 'set': 'ABC', 'collector_number': '1'},
    }
    deck = import_arena_deck('4 Known (ABC) 1', card_db=db, deck_format='standard')
    assert deck['mainboard'][0]['arenaId'] == 1
    assert deck['diagnostics']['unresolvedCards'] == 0
    assert deck['diagnostics']['unknownSets'] == []


def test_set_collector_mismatch_does_not_resolve():
    db = {1: {'name': 'Correct', 'set': 'ABC', 'collector_number': '1'}}
    deck = import_arena_deck('4 Wrong (ABC) 1', card_db=db, deck_format='standard')
    assert deck['mainboard'][0]['reason'] == 'identity-mismatch'
    result = analyze_deck(deck=arena_deck_to_deck_cards(deck), collection={}, wildcards={}, card_db=db)
    assert result['summary']['totalCards'] == 4
    assert result['summary']['unresolvedCards'] == 4
    assert result['unresolvedCards'][0]['reason'] == 'identity-mismatch'
