#!/usr/bin/env python3
"""Verification script for CLI deck container functionality."""

import sys
import tempfile
from pathlib import Path

# Add project to path
sys.path.insert(0, str(Path(__file__).parent))

def test_cli_container_export():
    """Test CLI deck container export command."""
    import subprocess
    import json
    
    with tempfile.TemporaryDirectory() as tmpdir:
        tmpdir = Path(tmpdir)
        
        # Create a mock decks.json file
        mock_decks = {
            "schema": "decks.v1",
            "decks": [
                {
                    "name": "Test Deck 1",
                    "deckId": "test001",
                    "deckTileId": 100,
                    "description": "Test description",
                    "attributes": {},
                    "formatLegalities": {"standard": True},
                    "isCompanionValid": None,
                    "mana": "R"
                },
                {
                    "name": "Test Deck 2",
                    "deckId": "test002",
                    "deckTileId": 200,
                    "description": None,
                    "attributes": {},
                    "formatLegalities": {},
                    "isCompanionValid": True,
                    "mana": "WU"
                }
            ]
        }
        
        decks_file = tmpdir / "decks.json"
        decks_file.write_text(json.dumps(mock_decks, indent=2))
        
        # Test 1: Create container from decks.json
        print("Test 1: deck container")
        container_dir = tmpdir / "decks-container"
        result = subprocess.run(
            ["python", "-m", "cli.main", "decks", "container", "--output", str(container_dir)],
            capture_output=True,
            text=True
        )
        print(result.stdout)
        assert result.returncode == 0, f"deck container failed: {result.stderr}"
        index_file = container_dir / "index.json"
        assert index_file.exists(), "index.json not created"
        index_data = json.loads(index_file.read_text())
        assert index_data["deckCount"] == 2, f"wrong deck count: {index_data['deckCount']}"
        print(f"✓ container created with {index_data['deckCount']} decks")
        
        # Test 2: List decks from container
        print("\nTest 2: deck list")
        result = subprocess.run(
            ["python", "-m", "cli.main", "decks", "list", "--output", str(container_dir)],
            capture_output=True,
            text=True
        )
        print(result.stdout)
        assert result.returncode == 0, f"deck list failed: {result.stderr}"
        print("✓ deck list works")
        
        # Test 3: Show deck from container
        print("\nTest 3: deck show")
        result = subprocess.run(
            ["python", "-m", "cli.main", "decks", "show", "--deck-id", "test001", "--output", str(container_dir)],
            capture_output=True,
            text=True
        )
        print(result.stdout)
        assert result.returncode == 0, f"deck show failed: {result.stderr}"
        show_data = json.loads(result.stdout)
        assert show_data["deckId"] == "test001"
        assert show_data["name"] == "Test Deck 1"
        print("✓ deck show works")
        
        # Test 4: List from container (again, idempotent)
        print("\nTest 4: deck list (container, idempotent)")
        result = subprocess.run(
            ["python", "-m", "cli.main", "decks", "list", "--output", str(container_dir)],
            capture_output=True,
            text=True
        )
        print(result.stdout)
        assert result.returncode == 0, f"deck list (container) failed: {result.stderr}"
        print("✓ deck list (container) works")
    
    print("\n" + "="*50)
    print("All CLI verification tests passed! ✓")
    print("="*50)

if __name__ == "__main__":
    test_cli_container_export()