#!/usr/bin/env bash
#
# install.sh — Installiert den mtga-helper als LaunchDaemon unter root.
#
# Ersetzt das alte SMJobBless-Verfahren (deprecated seit macOS 13)
# durch den direkten launchctl-Weg: Binary + Plist kopieren, dann
# 'launchctl load'.
#
# Was dieses Skript macht:
#   1.  Binary nach /Library/PrivilegedHelperTools/mtga-helper kopieren
#   2.  LaunchDaemon-plist nach /Library/LaunchDaemons/com.mtga.helper.plist kopieren
#   3.  Daemon laden (launchctl load)
#   4.  Daemon-Status verifizieren
#   5.  Socket-Verfügbarkeit prüfen
#
# Voraussetzungen:
#   - make -C helper wurde erfolgreich ausgeführt (Binary existiert)
#   - Skript wird mit sudo ausgeführt (root-Rechte für /Library)
#
# Usage:
#   sudo ./helper/install.sh
#   sudo ./helper/install.sh --uninstall
#   sudo ./helper/install.sh --status
#
# Exit codes:
#   0 — Erfolg (bzw. Status-Check: Daemon läuft)
#   1 — Voraussetzungen nicht erfüllt oder Installation fehlgeschlagen
#   2 — Status-Check: Daemon läuft nicht (nur bei --status)

set -euo pipefail

# --- Konstanten ---
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

BIN_SRC="$SCRIPT_DIR/build/mtga-helper"
BIN_DST="/Library/PrivilegedHelperTools/mtga-helper"
PLIST_SRC="$SCRIPT_DIR/launchd.plist"
PLIST_DST="/Library/LaunchDaemons/com.mtga.helper.plist"
DAEMON_LABEL="com.mtga.helper"
SOCK_PATH="/var/run/mtga-helper.sock"
HELPER_LOG="/var/log/mtga-helper.log"
HELPER_ERR="/var/log/mtga-helper.err.log"

# --- Farben ---
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

pass() { echo -e "${GREEN}[PASS]${NC} $1"; }
fail() { echo -e "${RED}[FAIL]${NC} $1"; }
info() { echo -e "${YELLOW}[INFO]${NC} $1"; }

# --- Root-Check ---
check_root() {
    if [[ "$(id -u)" -ne 0 ]]; then
        fail "Dieses Skript benötigt root-Rechte. Bitte mit sudo ausführen:"
        echo "  sudo $0"
        exit 1
    fi
}

# --- Voraussetzungen prüfen ---
check_prerequisites() {
    info "Prüfe Voraussetzungen..."

    if [[ ! -f "$BIN_SRC" ]]; then
        fail "Helper-Binary nicht gefunden: $BIN_SRC"
        echo "  Bitte zuerst ausführen: make -C helper"
        exit 1
    fi
    pass "Helper-Binary gefunden: $BIN_SRC"

    if [[ ! -f "$PLIST_SRC" ]]; then
        fail "LaunchDaemon-plist nicht gefunden: $PLIST_SRC"
        exit 1
    fi
    pass "LaunchDaemon-plist gefunden: $PLIST_SRC"

    if [[ ! -x "$BIN_SRC" ]]; then
        fail "Helper-Binary ist nicht ausführbar: $BIN_SRC"
        echo "  Bitte ausführen: chmod +x $BIN_SRC"
        exit 1
    fi
    pass "Helper-Binary ist ausführbar"
}

# --- Daemon entladen (Hilfsfunktion) ---
unload_daemon() {
    # Primär: launchctl unload (wie im Task gefordert)
    if launchctl unload "$PLIST_DST" 2>/dev/null; then
        return 0
    fi
    # Fallback für neuere macOS-Versionen
    if launchctl bootout "system/$DAEMON_LABEL" 2>/dev/null; then
        return 0
    fi
    return 1
}

# --- Daemon laden (Hilfsfunktion) ---
load_daemon() {
    # Primär: launchctl load (wie im Task gefordert)
    if launchctl load "$PLIST_DST" 2>/dev/null; then
        return 0
    fi
    # Fallback für neuere macOS-Versionen
    if launchctl bootstrap system "$PLIST_DST" 2>/dev/null; then
        return 0
    fi
    return 1
}

# --- Deinstallation ---
uninstall() {
    info "Deinstalliere mtga-helper..."

    # Daemon entladen
    if launchctl list "$DAEMON_LABEL" &>/dev/null; then
        if unload_daemon; then
            pass "Daemon entladen: $DAEMON_LABEL"
        else
            fail "Daemon konnte nicht entladen werden"
            echo "  Manuell versuchen: sudo launchctl unload $PLIST_DST"
        fi
    else
        info "Daemon war nicht geladen"
    fi

    # Dateien entfernen
    rm -f "$PLIST_DST" && pass "plist entfernt: $PLIST_DST" || true
    rm -f "$BIN_DST" && pass "Binary entfernt: $BIN_DST" || true
    rm -f "$SOCK_PATH" && pass "Socket entfernt: $SOCK_PATH" || true
    rm -f "$HELPER_LOG" "$HELPER_ERR" 2>/dev/null || true

    pass "Deinstallation abgeschlossen"
}

# --- Status-Check ---
status() {
    info "Status-Check für $DAEMON_LABEL"
    echo ""

    local daemon_running=false
    local socket_present=false

    # launchctl print system/com.mtga.helper — prüft, ob der Job GELADEN ist.
    # (Nicht `launchctl list <label>`: das schlägt fehl, wenn der OnDemand-Daemon
    #  keinen laufenden Prozess hat — KeepAlive=false + RunAtLoad=false → der
    #  Job ist geladen, startet aber erst bei der ersten Socket-Verbindung.)
    if launchctl print "system/$DAEMON_LABEL" &>/dev/null; then
        local pid
        pid="$(launchctl print "system/$DAEMON_LABEL" 2>/dev/null | awk '/pid =/ {gsub(/[^0-9]/, "", $0); print; exit}')" || pid="-"
        pass "Daemon geladen: $DAEMON_LABEL (PID: ${pid:--})"
        launchctl print "system/$DAEMON_LABEL" 2>/dev/null | grep -E "state =|pid =" | head -5
        daemon_running=true
    else
        fail "Daemon ist nicht geladen"
        info "  LaunchDaemon-Liste (Filter):"
        launchctl list 2>/dev/null | grep -i mtga || echo "  (kein Eintrag mit 'mtga' gefunden)"
    fi

    echo ""

    # ls -la /var/run/mtga-helper.sock
    if [[ -S "$SOCK_PATH" ]]; then
        pass "Socket vorhanden:"
        ls -la "$SOCK_PATH"
        socket_present=true
    else
        fail "Socket nicht vorhanden: $SOCK_PATH"
    fi

    echo ""

    # Binary im Ziel?
    if [[ -f "$BIN_DST" ]]; then
        pass "Binary installiert: $BIN_DST"
    else
        fail "Binary nicht installiert: $BIN_DST"
    fi

    # Plist im Ziel?
    if [[ -f "$PLIST_DST" ]]; then
        pass "Plist installiert: $PLIST_DST"
    else
        fail "Plist nicht installiert: $PLIST_DST"
    fi

    echo ""
    if $daemon_running && $socket_present; then
        pass "Helper ist betriebsbereit."
        return 0
    else
        fail "Helper ist NICHT betriebsbereit."
        return 2
    fi
}

# --- Installation ---
install() {
    check_prerequisites

    info "Installiere mtga-helper..."

    # 1. PrivilegedHelperTools-Verzeichnis sicherstellen
    mkdir -p /Library/PrivilegedHelperTools
    pass "Verzeichnis sichergestellt: /Library/PrivilegedHelperTools"

    # 2. Alte Version stoppen und entfernen
    if launchctl list "$DAEMON_LABEL" &>/dev/null; then
        info "Alter Daemon läuft — wird entladen..."
        unload_daemon || true
        sleep 1
    fi
    rm -f "$BIN_DST"
    rm -f "$PLIST_DST"
    rm -f "$SOCK_PATH"

    # 3. Binary kopieren
    cp "$BIN_SRC" "$BIN_DST"
    chmod 755 "$BIN_DST"
    chown root:wheel "$BIN_DST"
    pass "Binary kopiert: $BIN_DST"

    # 4. LaunchDaemon-plist kopieren
    cp "$PLIST_SRC" "$PLIST_DST"
    chmod 644 "$PLIST_DST"
    chown root:wheel "$PLIST_DST"
    pass "plist installiert: $PLIST_DST"

    # 5. Daemon laden (launchctl load)
    info "Lade LaunchDaemon..."
    if ! load_daemon; then
        fail "Daemon konnte nicht geladen werden"
        echo "  Manuell versuchen: sudo launchctl load $PLIST_DST"
        echo "  Logs: log show --predicate 'process == \"mtga-helper\"' --last 5m"
        exit 1
    fi
    pass "Daemon geladen via launchctl load"

    # 6. Daemon-Status verifizieren
    #    OnDemand-Daemon (KeepAlive=false + RunAtLoad=false): der Job ist
    #    geladen, startet aber erst bei der ersten Socket-Verbindung. Daher
    #    `launchctl print system/<label>` prüfen (Job geladen), nicht
    #    `launchctl list <label>` (das verlangt einen laufenden Prozess).
    sleep 2
    if launchctl print "system/$DAEMON_LABEL" &>/dev/null; then
        pass "Daemon geladen: $DAEMON_LABEL (OnDemand — startet bei erster Verbindung)"
    else
        fail "Daemon ist nicht in launchctl geladen"
        echo "  Prüfe Logs: log show --predicate 'process == \"mtga-helper\"' --last 5m"
        exit 1
    fi

    # 7. Socket prüfen (nur wenn der Helper OnDemand=false hat)
    #    Bei OnDemand=true wird der Socket erst bei Verbindung erstellt.
    if [[ -S "$SOCK_PATH" ]]; then
        pass "Socket vorhanden: $SOCK_PATH"
    else
        info "Socket noch nicht vorhanden (OnDemand — wird bei erster Verbindung erstellt)"
    fi

    # 8. Ping-Test (falls Socket da ist)
    if [[ -S "$SOCK_PATH" ]]; then
        info "Sende Ping..."
        PING_RESP=$(echo '{"action":"ping"}' | nc -U -w 5 "$SOCK_PATH" 2>/dev/null || echo "")
        if echo "$PING_RESP" | grep -q '"status":"ok"'; then
            pass "Ping erfolgreich: $PING_RESP"
        else
            info "Ping nicht erfolgreich (Helper startet evtl. noch): '$PING_RESP'"
        fi
    fi

    echo ""
    echo -e "${GREEN}========================================${NC}"
    echo -e "${GREEN}Installation abgeschlossen!${NC}"
    echo -e "${GREEN}========================================${NC}"
    echo ""
    echo "Helper läuft als LaunchDaemon unter root."
    echo "Socket: $SOCK_PATH"
    echo ""
    echo "Testen:"
    echo "  echo '{\"action\":\"ping\"}' | nc -U $SOCK_PATH"
    echo ""
    echo "Status prüfen:"
    echo "  sudo $0 --status"
    echo ""
    echo "Deinstallieren:"
    echo "  sudo $0 --uninstall"
}

# --- Main ---
main() {
    check_root

    case "${1:-}" in
        --uninstall|-u)
            uninstall
            ;;
        --status|-s)
            status
            ;;
        ""|install|--install|-i)
            install
            ;;
        *)
            fail "Unbekannter Parameter: $1"
            echo "Usage: sudo $0 [--uninstall|--status]"
            exit 1
            ;;
    esac
}

main "$@"