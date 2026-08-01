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
        
        # Test 1: List decks
        print("Test 1: deck list")
        result = subprocess.run(
            ["python", "-m", "cli.main", "decks", "list", "--output", str(tmpdir)],
            capture_output=True,
            text=True
        )
        print(result.stdout)
        if result.returncode == 0:
            print("✓ deck list works")
        else:
            print(f"✗ deck list failed: {result.stderr}")
            return False
        
        # Test 2: Show deck
        print("\nTest 2: deck show")
        result = subprocess.run(
            ["python", "-m", "cli.main", "decks", "show", "--deck-id", "test001", "--output", str(tmpdir)],
            capture_output=True,
            text=True
        )
        print(result.stdout)
        if result.returncode == 0:
            print("✓ deck show works")
        else:
            print(f"✗ deck show failed: {result.stderr}")
            return False
        
        # Test 3: Container export
        print("\nTest 3: deck container")
        container_dir = tmpdir / "decks-container"
        result = subprocess.run(
            ["python", "-m", "cli.main", "decks", "container", "--output", str(container_dir)],
            capture_output=True,
            text=True
        )
        print(result.stdout)
        if result.returncode == 0:
            print("✓ deck container works")
            # Verify container was created
            index_file = container_dir / "index.json"
            if index_file.exists():
                index_data = json.loads(index_file.read_text())
                if index_data["deckCount"] == 2:
                    print(f"✓ Container created with {index_data['deckCount']} decks")
                else:
                    print(f"✗ Container has wrong deck count: {index_data['deckCount']}")
                    return False
            else:
                print("✗ Container index.json not created")
                return False
        else:
            print(f"✗ deck container failed: {result.stderr}")
            return False
        
        # Test 4: List from container
        print("\nTest 4: deck list (from container)")
        result = subprocess.run(
            ["python", "-m", "cli.main", "decks", "list", "--output", str(container_dir)],
            capture_output=True,
            text=True
        )
        print(result.stdout)
        if result.returncode == 0:
            print("✓ deck list (container) works")
        else:
            print(f"✗ deck list (container) failed: {result.stderr}")
            return False
    
    print("\n" + "="*50)
    print("All CLI verification tests passed! ✓")
    print("="*50)
    return True

if __name__ == "__main__":
    success = test_cli_container_export()
    sys.exit(0 if success else 1)
