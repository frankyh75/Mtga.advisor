#!/usr/bin/env bash
#
# install-commands.sh — Gibt alle Befehle aus, die Frank manuell als root
# ausführen muss, um den mtga-helper zu installieren und zu verifizieren.
#
# Hintergrund: Der Worker darf kein sudo ausführen. Dieses Skript erzeugt
# KEINE Systemänderungen — es schreibt nur die Befehle nach stdout, die
# Frank dann per Copy-Paste in ein root-Terminal einfügt.
#
# Usage:
#   ./helper/install-commands.sh              # Komplette Installation + Verify
#   ./helper/install-commands.sh --install    # Nur Installation
#   ./helper/install-commands.sh --verify     # Nur Verifikation
#   ./helper/install-commands.sh --uninstall  # Nur Deinstallation
#
# Keine sudo-Rechte erforderlich — Skript gibt nur echo-Befehle aus.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"

# Farben für die Ausgabe
BLUE='\033[0;34m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

header() {
    echo ""
    echo -e "${BLUE}═══════════════════════════════════════════════════════════${NC}"
    echo -e "${BLUE}  $1${NC}"
    echo -e "${BLUE}═══════════════════════════════════════════════════════════${NC}"
}

cmd() {
    echo -e "${GREEN}\$ $1${NC}"
}

note() {
    echo -e "${YELLOW}  # $1${NC}"
}

blank() {
    echo ""
}

# --- Install-Befehle ausgeben ---
emit_install() {
    header "SCHRITT 1: Binary bauen (als normaler User, kein sudo)"
    cmd "cd \"$REPO_DIR\""
    cmd "make -C helper build"
    note "Erzeugt helper/build/mtga-helper"
    blank

    header "SCHRITT 2: Alte Installation entfernen (falls vorhanden)"
    cmd "sudo launchctl unload /Library/LaunchDaemons/com.mtga.helper.plist 2>/dev/null || true"
    cmd "sudo launchctl bootout system/com.mtga.helper 2>/dev/null || true"
    cmd "sudo rm -f /Library/PrivilegedHelperTools/mtga-helper"
    cmd "sudo rm -f /Library/LaunchDaemons/com.mtga.helper.plist"
    cmd "sudo rm -f /var/run/mtga-helper.sock"
    blank

    header "SCHRITT 3: Binary und Plist installieren"
    cmd "sudo mkdir -p /Library/PrivilegedHelperTools"
    cmd "sudo cp \"$SCRIPT_DIR/build/mtga-helper\" /Library/PrivilegedHelperTools/mtga-helper"
    cmd "sudo chmod 755 /Library/PrivilegedHelperTools/mtga-helper"
    cmd "sudo chown root:wheel /Library/PrivilegedHelperTools/mtga-helper"
    cmd "sudo cp \"$SCRIPT_DIR/launchd.plist\" /Library/LaunchDaemons/com.mtga.helper.plist"
    cmd "sudo chmod 644 /Library/LaunchDaemons/com.mtga.helper.plist"
    cmd "sudo chown root:wheel /Library/LaunchDaemons/com.mtga.helper.plist"
    blank

    header "SCHRITT 4: Daemon laden (launchctl load)"
    note "Primärweg: launchctl load"
    cmd "sudo launchctl load /Library/LaunchDaemons/com.mtga.helper.plist"
    note "Falls das fehlschlägt (macOS 15+), alternatives API:"
    cmd "sudo launchctl bootstrap system /Library/LaunchDaemons/com.mtga.helper.plist"
    blank

    header "SCHRITT 5: Warten + erster Check"
    cmd "sleep 2"
    blank

    echo -e "${YELLOW}>>> Jetzt --verify ausführen, um Daemon und Socket zu prüfen.${NC}"
    blank
}

# --- Verify-Befehle ausgeben ---
emit_verify() {
    header "VERIFY A: Daemon in launchctl sichtbar?"
    cmd "sudo launchctl list com.mtga.helper"
    note "Erwartet: Ausgabe mit PID, Label, LastExitStatus=0"
    note "Alternative Anzeige:"
    cmd "sudo launchctl list | grep mtga"
    blank

    header "VERIFY B: Socket existiert?"
    cmd "ls -la /var/run/mtga-helper.sock"
    note "Erwartet: srw-rw----  ...  root  wheel  /var/run/mtga-helper.sock"
    note "OnDemand-Hinweis: Bei KeepAlive=false wird der Socket evtl. erst"
    note "bei der ersten Verbindung erstellt. Falls nicht da, weiter mit C."
    blank

    header "VERIFY C: Ping über Socket senden"
    cmd "echo '{\"action\":\"ping\"}' | nc -U -w 5 /var/run/mtga-helper.sock"
    note "Erwartet: {\"status\":\"ok\",\"version\":\"1.0\"}"
    note "Falls der Socket noch nicht existiert (OnDemand), triggert das"
    note "nc-Kommando den Start. Evtl. 2x ausführen."
    blank

    header "VERIFY D: Binary und Plist am Zielort?"
    cmd "ls -la /Library/PrivilegedHelperTools/mtga-helper"
    cmd "ls -la /Library/LaunchDaemons/com.mtga.helper.plist"
    blank

    header "VERIFY E: plist validieren (Syntax)"
    cmd "sudo plutil -lint /Library/LaunchDaemons/com.mtga.helper.plist"
    note "Erwartet: OK"
    blank

    header "VERIFY F: Logs prüfen (falls etwas schiefgeht)"
    cmd "log show --predicate 'process == \"mtga-helper\"' --last 5m"
    cmd "tail -50 /var/log/mtga-helper.log 2>/dev/null || echo '(kein log)'"
    cmd "tail -50 /var/log/mtga-helper.err.log 2>/dev/null || echo '(kein err-log)'"
    blank

    header "ERGEBNIS"
    note "Wenn A (Daemon geladen) UND C (Ping ok): Helper betriebsbereit."
    note "Wenn Socket fehlt aber Daemon läuft: OnDemand, bei erster"
    note "Verbindung (Ping) startet er und erstellt den Socket."
}

# --- Uninstall-Befehle ausgeben ---
emit_uninstall() {
    header "DEINSTALLATION: mtga-helper entfernen"
    cmd "sudo launchctl unload /Library/LaunchDaemons/com.mtga.helper.plist 2>/dev/null || true"
    cmd "sudo launchctl bootout system/com.mtga.helper 2>/dev/null || true"
    cmd "sudo rm -f /Library/PrivilegedHelperTools/mtga-helper"
    cmd "sudo rm -f /Library/LaunchDaemons/com.mtga.helper.plist"
    cmd "sudo rm -f /var/run/mtga-helper.sock"
    cmd "sudo rm -f /var/log/mtga-helper.log /var/log/mtga-helper.err.log"
    blank
    note "Verifikation dass alles weg ist:"
    cmd "launchctl list | grep mtga || echo 'OK: Daemon nicht mehr geladen'"
    cmd "ls /var/run/mtga-helper.sock 2>/dev/null || echo 'OK: Socket entfernt'"
}

# --- Main ---
case "${1:-all}" in
    --install|-i)
        emit_install
        ;;
    --verify|-v)
        emit_verify
        ;;
    --uninstall|-u)
        emit_uninstall
        ;;
    all|"")
        emit_install
        emit_verify
        ;;
    *)
        echo "Usage: $0 [--install|--verify|--uninstall]"
        echo "  (ohne Argument = install + verify)"
        exit 1
        ;;
esac