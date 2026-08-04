#!/usr/bin/env bash
#
# test_e2e.sh — End-to-End Test für den MTGA Helper
#
# Testablauf:
#   1. Prüfe, ob das helper Binary existiert (make -C helper)
#   2. Starte Helper im Hintergrund (--test-mode)
#   3. Sende Ping-Kommando → erwarte {"status":"ok"}
#   4. Sende list_regions → erwarte JSON-Response mit "regions"-Array
#   5. Sende read_memory mit Mock-Adresse → erwarte "data"-Feld (Base64)
#   6. Sende ungültiges Kommando → erwarte "error"-Feld
#   7. Stoppe Helper, Cleanup Socket
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

# --- Socket-Helper: Python-Fallback wenn nc -U nicht verfügbar ---
sock_send() {
    local sock="$1"
    local msg="$2"
    # Versuche nc -U, falle auf python3 zurück
    if command -v nc >/dev/null 2>&1; then
        echo "$msg" | nc -U -w 5 "$sock" 2>/dev/null || echo ""
    else
        python3 -c "
import socket, sys
sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
sock.settimeout(5)
try:
    sock.connect('$sock')
    sock.sendall(('$msg\n').encode())
    data = b''
    while True:
        chunk = sock.recv(65536)
        if not chunk:
            break
        data += chunk
        if len(chunk) < 65536:
            break
    sys.stdout.buffer.write(data)
except Exception:
    pass
finally:
    sock.close()
" 2>/dev/null || echo ""
    fi
}

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
    info "Binary nicht gefunden, versuche 'make -C helper test'..."
    if make -C "$HELPER_DIR" test 2>/dev/null; then
        pass "Binary erfolgreich gebaut"
    else
        fail "Helper Binary nicht gefunden und 'make test' fehlgeschlagen: $HELPER_BIN"
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
PING_RESP=$(sock_send "$SOCK_PATH" '{"action":"ping"}')
if echo "$PING_RESP" | grep -q '"status":"ok"'; then
    pass "Ping-Antwort korrekt: $PING_RESP"
else
    fail "Ping-Antwort ungültig: '$PING_RESP'"
fi

# --- Test 4: list_regions (Mock-Modus) ---
info "Test 4: Sende list_regions-Kommando..."
REGIONS_RESP=$(sock_send "$SOCK_PATH" '{"action":"list_regions"}')
if echo "$REGIONS_RESP" | grep -q '"regions"'; then
    pass "list_regions-Antwort enthält 'regions'-Feld"
    if echo "$REGIONS_RESP" | python3 -c "import sys,json; d=json.load(sys.stdin); assert 'regions' in d; assert isinstance(d['regions'], list)" 2>/dev/null; then
        pass "list_regions-Antwort ist valides JSON mit regions-Liste"
    else
        fail "list_regions-Antwort ist kein valides JSON oder regions ist keine Liste"
    fi
else
    fail "list_regions-Antwort enthält kein 'regions'-Feld: '$REGIONS_RESP'"
fi

# --- Test 5: read_memory (Mock-Modus) ---
info "Test 5: Sende read_memory-Kommando (Mock-Adresse 4294967296)..."
READ_RESP=$(sock_send "$SOCK_PATH" '{"action":"read_memory","address":4294967296,"size":64}')
if echo "$READ_RESP" | grep -q '"data"'; then
    pass "read_memory-Antwort enthält 'data'-Feld"
    if echo "$READ_RESP" | python3 -c "
import sys, json, base64
d = json.load(sys.stdin)
assert 'data' in d
assert 'bytes_read' in d
# Base64 dekodieren und Länge prüfen
raw = base64.b64decode(d['data'])
assert len(raw) == d['bytes_read']
" 2>/dev/null; then
        pass "read_memory-Antwort ist valides JSON mit dekodierbaren Base64-Daten"
    else
        fail "read_memory-Antwort: Base64-Daten nicht dekodierbar oder bytes_read fehlt"
    fi
else
    fail "read_memory-Antwort enthält kein 'data'-Feld: '$READ_RESP'"
fi

# --- Test 6: Ungültiges Kommando ---
info "Test 6: Sende ungültiges Kommando..."
ERR_RESP=$(sock_send "$SOCK_PATH" '{"action":"unknown"}')
if echo "$ERR_RESP" | grep -q '"error"'; then
    pass "Ungültiges Kommando liefert Fehler-Antwort"
else
    fail "Ungültiges Kommando liefert keine Fehler-Antwort: '$ERR_RESP'"
fi

# --- Ergebnis ---
echo ""
if [[ $FAILED -eq 0 ]]; then
    echo -e "${GREEN}========================================${NC}"
    echo -e "${GREEN}Alle Tests bestanden (6/6)${NC}"
    echo -e "${GREEN}========================================${NC}"
    exit 0
else
    echo -e "${RED}========================================${NC}"
    echo -e "${RED}Ein oder mehrere Tests fehlgeschlagen${NC}"
    echo -e "${RED}========================================${NC}"
    exit 1
fi