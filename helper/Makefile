# Makefile — mtga-helper Build, Bundle und Code-Signing
#
# Targets:
#   make            — kompiliert das Binary und erzeugt das .app-Bundle
#   make build      — nur das Binary kompilieren (ohne Bundle/Signing)
#   make bundle     — Bundle-Struktur erstellen und Binary/Info.plist hineinlegen
#   make sign       — Bundle signieren (Certificate oder ad-hoc Fallback)
#   make sign-check — Prüfen ob ein Code-Signing-Zertifikat existiert
#   make clean      — Build-Produkte entfernen
#   make install    — Bundle via install.sh installieren (sudo erforderlich)
#   make verify     — Code-Signing und Bundle-Struktur verifizieren
#   make test       — Binary für E2E-Test bereitstellen
#
# Variablen (über Override anpassbar):
#   CERT_NAME       — Name des Code-Signing-Zertifikats (Keychain)
#   BUNDLE_ID       — Bundle-Identifier (muss mit Info.plist übereinstimmen)
#   HELPER_DIR      — Build-Output-Verzeichnis
#
# Usage:
#   make -C helper
#   make -C helper CERT_NAME="Developer ID Application: Frank Y."
#   make -C helper clean
#   sudo make -C helper install

CC          := clang
CERT_NAME   ?= MTGA Helper Dev
BUNDLE_ID   := com.mtga.helper
HELPER_DIR  := $(dir $(abspath $(lastword $(MAKEFILE_LIST))))

# Paths
BIN_NAME    := mtga-helper
BUNDLE_NAME := mtga-helper.bundle
BUILD_DIR   := build
SRC         := main.c
BIN         := $(BUILD_DIR)/$(BIN_NAME)
BUNDLE      := $(BUILD_DIR)/$(BUNDLE_NAME)

# Compiler Flags
CFLAGS      := -Wall -Wextra -O2 -std=c11 -D_DARWIN_C_SOURCE
LDFLAGS     := -framework Foundation

# Bundle-Struktur
BUNDLE_CONTENTS   := $(BUNDLE)/Contents
BUNDLE_MACOS      := $(BUNDLE_CONTENTS)/MacOS

# Default: alles bauen
.PHONY: all build bundle sign clean install verify test sign-check

all: bundle sign

# --- Build: nur Binary kompilieren ---
build: $(BIN)

$(BIN): $(SRC)
	@mkdir -p $(BUILD_DIR)
	$(CC) $(CFLAGS) -o $@ $< $(LDFLAGS)

# --- Bundle: .app-Bundle-Struktur erstellen ---
bundle: build $(BUNDLE)

$(BUNDLE): $(BIN) Info.plist
	@mkdir -p $(BUNDLE_MACOS)
	cp $(BIN) $(BUNDLE_MACOS)/$(BIN_NAME)
	cp Info.plist $(BUNDLE_CONTENTS)/Info.plist
	@echo "Bundle erstellt: $(BUNDLE)"

# --- Sign: Bundle mit Code-Signing-Zertifikat signieren ---
# Fallback auf ad-hoc Signing (-), wenn keine Identity gefunden wird.
# Ad-hoc signierte Bundles laufen lokal, werden aber von SMJobBless abgelehnt.
sign: bundle
	@if security find-identity -v -p codesigning 2>/dev/null | grep -q "$(CERT_NAME)"; then \
		echo "Signiere Bundle mit Zertifikat: $(CERT_NAME)"; \
		codesign --force --sign "$(CERT_NAME)" \
			--entitlements Entitlements.plist \
			--requirements "identifier \"$(BUNDLE_ID)\"" \
			$(BUNDLE); \
		echo "Signatur fertig (Certificate)."; \
	else \
		echo "WARN: Zertifikat '$(CERT_NAME)' nicht gefunden — ad-hoc Signatur."; \
		codesign --force --sign - \
			--entitlements Entitlements.plist \
			$(BUNDLE); \
		echo "Signatur fertig (ad-hoc). SMJobBless benötigt ein echtes Zertifikat."; \
	fi

# --- Install: Bundle + LaunchDaemon installieren ---
install: all
	@echo "Installiere mtga-helper (erfordert sudo)..."
	./install.sh

# --- Verify: Code-Signing und Bundle prüfen ---
verify: $(BUNDLE)
	@echo "Verifiziere Bundle-Struktur..."
	@test -f $(BUNDLE_CONTENTS)/Info.plist || { echo "FAIL: Info.plist fehlt"; exit 1; }
	@test -f $(BUNDLE_MACOS)/$(BIN_NAME) || { echo "FAIL: Binary fehlt"; exit 1; }
	@echo "OK: Bundle-Struktur intakt"
	@echo "Prüfe Code-Signing..."
	@if codesign -dv $(BUNDLE) 2>/dev/null; then \
		echo "OK: Bundle ist signiert"; \
		codesign -d -r- $(BUNDLE) 2>&1 || true; \
	else \
		echo "WARN: Bundle nicht signiert (run 'make sign')"; \
	fi

# --- Sign-Check: Prüfen ob ein echtes Signaturzertifikat existiert ---
sign-check:
	@echo "Verfügbare Code-Signing-Identities:"
	@security find-identity -v -p codesigning 2>/dev/null || true
	@echo ""
	@if security find-identity -v -p codesigning 2>/dev/null | grep -q "$(CERT_NAME)"; then \
		echo "OK: Zertifikat '$(CERT_NAME)' gefunden"; \
	else \
		echo "WARN: Zertifikat '$(CERT_NAME)' nicht gefunden."; \
		echo "      'make sign' verwendet ad-hoc Signatur (kein SMJobBless)."; \
		echo "      Für SMJobBless: Zertifikat in Keychain erzeugen oder CERT_NAME override."; \
	fi

# --- Clean: Build-Produkte entfernen ---
clean:
	rm -rf $(BUILD_DIR)
	rm -f $(BIN_NAME)
	rm -f /tmp/mtga-helper-test.sock
	@echo "Build-Verzeichnis entfernt."
	@echo "(Hinweis: /var/run/mtga-helper.sock wird vom Daemon verwaltet — sudo rm -f /var/run/mtga-helper.sock)"

# --- Test: Binary für E2E-Test bereitstellen ---
test: build
	cp $(BIN) $(BIN_NAME)
	@echo "Test-Binary bereit: $(BIN_NAME)"