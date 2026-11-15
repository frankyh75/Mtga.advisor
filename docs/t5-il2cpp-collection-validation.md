# T5 – IL2CPP-Collection-Live-Validierung

**Laufzeitpunkt:** 2026-11-15T10:39:10Z  
**Befehl:** `PYTHONPATH=. python3 -m cli.main scan --method il2cpp --non-interactive --output out-memory-live-2`  
**Exitcode:** `1` (fail-closed)  
**Artefakt:** keines – `collection.json` wurde absichtlich nicht geschrieben.

## Gemessener Befund

| Kennzahl | Ergebnis |
|---|---:|
| `unique_cards` | 0 |
| `total_copies` | 0 |
| `playset_copies` | 0 |
| Anker erfüllt | 0/5 verfügbarer Standardanker |
| Erfolgsartefakt | nein |

Der Reader erreichte keinen `WrapperController` und konnte daher keine Collection dekodieren. Die `0`-Werte sind ausschließlich das Ergebnis dieses fehlgeschlagenen Reads, **keine** Collection-Schätzung.

## Diagnose-Trail / Fail-closed-Auszug

```text
ERROR: IL2CPP-Collection verworfen (fail-closed):
anchor-failed:105645 expected>=1 actual=None;
anchor-failed:105771 expected>=1 actual=None;
anchor-failed:105653 expected>=1 actual=None;
anchor-failed:104977 expected>=3 actual=None;
anchor-failed:104945 expected>=1 actual=None;
unique-cards-out-of-range:0

Collection-Navigation fehlgeschlagen bei wrapper_instance:
WrapperController per Backref nicht gefunden
```

Damit greifen korrekt sowohl die Anker- als auch die Band-Prüfung; die alte/stale
Cache-Menge für **We Say Thee Nay!** wird nicht übernommen: verbindlich ist
`104977 >= 3`.

## Referenzvergleich

Ein quantitativer Vergleich ist für diesen fehlgeschlagenen Lauf nicht sinnvoll:

| Referenz | Unique | Total |
|---|---:|---:|
| Untapped.gg, 2026-11-14 | 7.803 | – |
| Lokaler Snapshot, 2026-08-02 | 7.874 | 16.280 |
| Dieser fehlgeschlagene Live-Read | 0 | 0 |

## Offener Blocker

Der Helper liefert nach dem bekannten Live-Befund weiterhin eine auf 4.096
Regionen gekappte Regionskarte. Dadurch findet die Backref-Suche die
`WrapperController`-Instanz nicht zuverlässig. Eine erfolgreiche Abnahme setzt
T0 voraus: Helper 1.1.0 mit `MAX_REGIONS=16384` und `truncated`-Signal. Danach
ist derselbe Befehl erneut auszuführen; erst bei vollständig erfüllten Ankern
und `5.000 <= unique_cards <= 12.000` darf ein Erfolgsartefakt entstehen.

Die Spec erwähnt eine 29-zeilige Deckliste als Standardanker. Im aktuellen Repo
sind jedoch nur fünf numerisch auflösbare `DEFAULT_ANCHORS` vorhanden; die
verbindlichen IDs TVA (`105645`) und We Say Thee Nay! (`104977`) werden dabei
mit den spezifizierten Mengen geprüft. Die übrigen 24 IDs müssen vor der finalen
Live-Abnahme als Datenquelle ergänzt werden.
