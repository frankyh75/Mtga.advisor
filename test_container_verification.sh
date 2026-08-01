#!/bin/bash
# Verification script for deck container functionality

set -e

TEST_DIR=$(mktemp -d)
echo "Test directory: $TEST_DIR"

# Create mock decks.json
cat > "$TEST_DIR/decks.json" <<'EOF'
{
  "schema": "decks.v1",
  "decks": [
    {
      "name": "Test Deck 1",
      "deckId": "test001",
      "deckTileId": 100,
      "description": "Test description",
      "attributes": {},
      "formatLegalities": {"standard": true},
      "isCompanionValid": null,
      "mana": "R"
    },
    {
      "name": "Test Deck 2",
      "deckId": "test002",
      "deckTileId": 200,
      "description": null,
      "attributes": {},
      "formatLegalities": {},
      "isCompanionValid": true,
      "mana": "WU"
    }
  ]
}
EOF

# Test 1: Create container
CONTAINER_DIR="$TEST_DIR/decks-container"
mkdir -p "$CONTAINER_DIR"

echo "=== Test: deck container ==="
if python -m cli.main decks container --output "$CONTAINER_DIR"; then
    echo "✓ container export passed"
else
    echo "✗ container export failed"
    exit 1
fi

# Verify container was created
if [ ! -f "$CONTAINER_DIR/index.json" ]; then
    echo "✗ index.json not created"
    exit 1
fi
echo "✓ index.json created"

# Test 2: List decks
echo ""
echo "=== Test: deck list ==="
if python -m cli.main decks list --output "$CONTAINER_DIR"; then
    echo "✓ list passed"
else
    echo "✗ list failed"
    exit 1
fi

# Test 3: Show deck
echo ""
echo "=== Test: deck show ==="
if python -m cli.main decks show --deck-id test001 --output "$CONTAINER_DIR"; then
    echo "✓ show passed"
else
    echo "✗ show failed"
    exit 1
fi

# Cleanup
rm -rf "$TEST_DIR"
echo ""
echo "=========================================="
echo "All verification tests passed! ✓"
echo "=========================================="
