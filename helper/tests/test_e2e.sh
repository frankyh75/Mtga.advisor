#!/usr/bin/env bash
#
# test_e2e.sh — End-to-End Test für den MTGA Helper
#
# Testablauf:
#   1. Prüfe, ob das helper Binary existiert (make -C helper)
#   2. Starte Helper im Hintergrund
#   3. Sende Ping-Kommando → erwarte {"status":"ok"}
#   4. Sende Scan-Kommando mit Mock-PID → erwarte JSON-Response mit "cards"-Feld
#   5. Stoppe Helper, Cleanup Socket
#
# Dieser Test kann ohne laufenden MTGA-Prozess ausgeführt werden,
# da der Helper im Test-Modus Mock-Daten zurückgibt.
#
# Usage:
#   bash helper/tests/test_e2e.sh
#
# Exit codes:
#   0 — alle Tests bestanden
#   1 — ein oder mehrere Tests fehlgeschlagen
#

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
HELPER_DIR="$PROJECT_ROOT/helper"
SOCK_PATH="${MTGA_HELPER_SOCK:-/tmp/mtga-helper-test.sock}"
HELPER_BIN="$HELPER_DIR/mtga-helper"
TIMEOUT=10

# --- Farben ---
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

pass() { echo -e "${GREEN}[PASS]${NC} $1"; }
fail() { echo -e "${RED}[FAIL]${NC} $1"; FAILED=1; }
info() { echo -e "${YELLOW}[INFO]${NC} $1"; }

FAILED=0
HELPER_PID=""

cleanup() {
    if [[ -n "$HELPER_PID" ]] && kill -0 "$HELPER_PID" 2>/dev/null; then
        kill "$HELPER_PID" 2>/dev/null || true
        wait "$HELPER_PID" 2>/dev/null || true
        info "Helper gestoppt (PID $HELPER_PID)"
    fi
    rm -f "$SOCK_PATH"
}
trap cleanup EXIT

# --- Test 1: Binary existiert ---
info "Test 1: Prüfe helper Binary..."
if [[ ! -f "$HELPER_BIN" ]]; then
    info "Binary nicht gefunden, versuche 'make -C helper'..."
    if make -C "$HELPER_DIR" 2>/dev/null; then
        pass "Binary erfolgreich gebaut"
    else
        fail "Helper Binary nicht gefunden und 'make' fehlgeschlagen: $HELPER_BIN"
        exit 1
    fi
else
    pass "Helper Binary gefunden: $HELPER_BIN"
fi

# --- Test 2: Helper starten ---
info "Test 2: Starte Helper..."
# Nutze Test-Socket-Pfad via Umgebungsvariable
export MTGA_HELPER_SOCK="$SOCK_PATH"
"$HELPER_BIN" --sock "$SOCK_PATH" --test-mode &
HELPER_PID=$!

# Warte bis Socket bereit ist
for i in $(seq 1 $TIMEOUT); do
    if [[ -S "$SOCK_PATH" ]]; then
        break
    fi
    sleep 1
done

if [[ ! -S "$SOCK_PATH" ]]; then
    fail "Helper Socket nicht erstellt nach $TIMEOUT Sekunden: $SOCK_PATH"
    exit 1
fi
pass "Helper gestartet, Socket bereit: $SOCK_PATH"

# --- Test 3: Ping ---
info "Test 3: Sende Ping-Kommando..."
PING_RESP=$(echo '{"action":"ping"}' | nc -U -w 5 "$SOCK_PATH" 2>/dev/null || echo "")
if echo "$PING_RESP" | grep -q '"status":"ok"'; then
    pass "Ping-Antwort korrekt: $PING_RESP"
else
    fail "Ping-Antwort ungültig: '$PING_RESP'"
fi

# --- Test 4: Scan (Mock-Modus) ---
info "Test 4: Sende Scan-Kommando (Mock-PID 99999)..."
SCAN_RESP=$(echo '{"action":"scan","pid":99999}' | nc -U -w 10 "$SOCK_PATH" 2>/dev/null || echo "")
if echo "$SCAN_RESP" | grep -q '"cards"'; then
    pass "Scan-Antwort enthält 'cards'-Feld"
    # Prüfe, ob cards ein Objekt/Array ist
    if echo "$SCAN_RESP" | python3 -c "import sys,json; d=json.load(sys.stdin); assert 'cards' in d" 2>/dev/null; then
        pass "Scan-Antwort ist valides JSON mit 'cards'-Key"
    else
        fail "Scan-Antwort ist kein valides JSON oder 'cards' fehlt"
    fi
else
    fail "Scan-Antwort enthält kein 'cards'-Feld: '$SCAN_RESP'"
fi

# --- Test 5: Ungültiges Kommando ---
info "Test 5: Sende ungültiges Kommando..."
ERR_RESP=$(echo '{"action":"unknown"}' | nc -U -w 5 "$SOCK_PATH" 2>/dev/null || echo "")
if echo "$ERR_RESP" | grep -q '"error"'; then
    pass "Ungültiges Kommando liefert Fehler-Antwort"
else
    fail "Ungültiges Kommando liefert keine Fehler-Antwort: '$ERR_RESP'"
fi

# --- Ergebnis ---
echo ""
if [[ $FAILED -eq 0 ]]; then
    echo -e "${GREEN}========================================${NC}"
    echo -e "${GREEN}Alle Tests bestanden (5/5)${NC}"
    echo -e "${GREEN}========================================${NC}"
    exit 0
else
    echo -e "${RED}========================================${NC}"
    echo -e "${RED}Ein oder mehrere Tests fehlgeschlagen${NC}"
    echo -e "${RED}========================================${NC}"
    exit 1
fi