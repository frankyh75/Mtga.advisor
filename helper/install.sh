#!/usr/bin/env bash
#
# install.sh — Installiert den mtga-helper als LaunchDaemon unter root.
#
# Was dieses Skript macht:
#   1.  Bundle nach /Library/PrivilegedHelperTools/ kopieren
#   2.  LaunchDaemon-plist nach /Library/LaunchDaemons/ kopieren
#   3.  Daemon laden (launchctl bootstrap / load)
#   4.  Daemon-Status verifizieren
#   5.  Socket-Verfügbarkeit prüfen
#
# Voraussetzungen:
#   - make -C helper wurde erfolgreich ausgeführt (Bundle existiert)
#   - Skript wird mit sudo ausgeführt (root-Rechte für /Library)
#
# Usage:
#   sudo ./helper/install.sh
#   sudo ./helper/install.sh --uninstall
#
# Exit codes:
#   0 — Installation erfolgreich
#   1 — Voraussetzungen nicht erfüllt oder Installation fehlgeschlagen
#

set -euo pipefail

# --- Konstanten ---
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

BUNDLE_SRC="$SCRIPT_DIR/build/mtga-helper.bundle"
BUNDLE_DST="/Library/PrivilegedHelperTools/mtga-helper.bundle"
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

    if [[ ! -d "$BUNDLE_SRC" ]]; then
        fail "Helper-Bundle nicht gefunden: $BUNDLE_SRC"
        echo "  Bitte zuerst ausführen: make -C helper"
        exit 1
    fi
    pass "Helper-Bundle gefunden: $BUNDLE_SRC"

    if [[ ! -f "$PLIST_SRC" ]]; then
        fail "LaunchDaemon-plist nicht gefunden: $PLIST_SRC"
        exit 1
    fi
    pass "LaunchDaemon-plist gefunden: $PLIST_SRC"

    # Code-Signing prüfen (Warnung, kein Fehler)
    if ! codesign -dv "$BUNDLE_SRC" 2>/dev/null; then
        info "WARN: Bundle ist nicht signiert. SMJobBless wird fehlschlagen."
        info "      Bitte ausführen: make -C helper sign"
    else
        pass "Bundle ist signiert"
    fi
}

# --- Deinstallation ---
uninstall() {
    info "Deinstalliere mtga-helper..."

    # Daemon entladen
    if launchctl list "$DAEMON_LABEL" &>/dev/null; then
        launchctl bootout system/"$DAEMON_LABEL" 2>/dev/null || \
        launchctl unload "$PLIST_DST" 2>/dev/null || true
        pass "Daemon entladen: $DAEMON_LABEL"
    else
        info "Daemon war nicht geladen"
    fi

    # Dateien entfernen
    rm -f "$PLIST_DST" && pass "plist entfernt: $PLIST_DST" || true
    rm -rf "$BUNDLE_DST" && pass "Bundle entfernt: $BUNDLE_DST" || true
    rm -f "$SOCK_PATH" && pass "Socket entfernt: $SOCK_PATH" || true
    rm -f "$HELPER_LOG" "$HELPER_ERR" 2>/dev/null || true

    pass "Deinstallation abgeschlossen"
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
        launchctl bootout system/"$DAEMON_LABEL" 2>/dev/null || \
        launchctl unload "$PLIST_DST" 2>/dev/null || true
        sleep 1
    fi
    rm -rf "$BUNDLE_DST"
    rm -f "$PLIST_DST"
    rm -f "$SOCK_PATH"

    # 3. Bundle kopieren
    cp -R "$BUNDLE_SRC" "$BUNDLE_DST"
    chmod 755 "$BUNDLE_DST"
    pass "Bundle kopiert: $BUNDLE_DST"

    # 4. LaunchDaemon-plist kopieren
    cp "$PLIST_SRC" "$PLIST_DST"
    chmod 644 "$PLIST_DST"
    chown root:wheel "$PLIST_DST"
    pass "plist installiert: $PLIST_DST"

    # 5. Daemon laden (macOS 13+: bootstrap, Fallback: load)
    info "Lade LaunchDaemon..."
    if launchctl bootstrap system "$PLIST_DST" 2>/dev/null; then
        pass "Daemon via bootstrap geladen"
    elif launchctl load "$PLIST_DST" 2>/dev/null; then
        pass "Daemon via load geladen"
    else
        fail "Daemon konnte nicht geladen werden"
        echo "  Manuell versuchen: launchctl load $PLIST_DST"
        echo "  Logs: log show --predicate 'process == \"mtga-helper\"' --last 5m"
        exit 1
    fi

    # 6. Daemon-Status verifizieren
    sleep 2
    if launchctl list "$DAEMON_LABEL" &>/dev/null; then
        pass "Daemon läuft: $DAEMON_LABEL"
    else
        fail "Daemon ist nicht in launchctl list sichtbar"
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
    echo "Deinstallieren:"
    echo "  sudo $0 --uninstall"
}

# --- Main ---
main() {
    check_root

    if [[ "${1:-}" == "--uninstall" || "${1:-}" == "-u" ]]; then
        uninstall
    else
        install
    fi
}

main "$@"