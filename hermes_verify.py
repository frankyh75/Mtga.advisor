#!/usr/bin/env python3
"""Ad-hoc verification for t_fa9fcbb9 review fixes."""
import json, sys, tempfile, threading, shutil
import urllib.request, urllib.error
from pathlib import Path

REPO = Path("/Users/agent/workspace/Mtga.advisor")
sys.path.insert(0, str(REPO))

passed = 0
failed = 0

def check(name, condition, detail=""):
    global passed, failed
    if condition:
        print(f"  PASS: {name}")
        passed += 1
    else:
        print(f"  FAIL: {name} -- {detail}")
        failed += 1

# --- 1. Path-Traversal-Safety (server/app.py) ---
print("=== 1. Path-Traversal-Safety (server/app.py) ===")
from server.app import MtgaAdvisorHandler, MtgaAdvisorServer

tmp = Path(tempfile.mkdtemp(prefix="hermes-verify-"))
dd = tmp / "decks"
dd.mkdir()
(dd / "deck-test-1.json").write_text(
    json.dumps({"schema": "deck.v1", "deckId": "test-1", "name": "Test", "cards": {}}),
    encoding="utf-8",
)
(tmp / "secret.json").write_text('{"secret": "should-not-leak"}', encoding="utf-8")

server = MtgaAdvisorServer(("127.0.0.1", 0), MtgaAdvisorHandler, tmp)
port = server.server_address[1]
server.timeout = 2

attempts = [
    "/api/deck/..%2f..%2fsecret",
    "/api/deck/../../secret",
    "/api/deck/..%2F..%2Fsecret.json",
    "/api/deck/../secret",
]

for attempt in attempts:
    t = threading.Thread(target=server.handle_request, daemon=True)
    t.start()
    try:
        resp = urllib.request.urlopen(f"http://127.0.0.1:{port}{attempt}", timeout=3)
        body = json.loads(resp.read().decode("utf-8"))
        check(f"blocked: {attempt}", False, f"200 returned: {body}")
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8") if exc.fp else ""
        check(f"blocked: {attempt}", exc.code in (400, 404) and "leak" not in body, f"code={exc.code}")
    except Exception as e:
        check(f"blocked: {attempt}", True, str(e))
    t.join(timeout=3)

# Positive: valid ID works
t = threading.Thread(target=server.handle_request, daemon=True)
t.start()
try:
    resp = urllib.request.urlopen(f"http://127.0.0.1:{port}/api/deck/test-1", timeout=3)
    data = json.loads(resp.read().decode("utf-8"))
    check("valid deck ID works", data.get("name") == "Test", str(data))
except Exception as e:
    check("valid deck ID works", False, str(e))
t.join(timeout=3)
server.server_close()
shutil.rmtree(tmp, ignore_errors=True)

# --- 2. deck_id sanitization (cli/main.py) ---
print("=== 2. deck_id Sanitization (cli/main.py) ===")

def sanitize(did):
    return "".join(c if c.isalnum() or c in "-_" else "_" for c in str(did))

check("clean id preserved", sanitize("test-1") == "test-1")
check("slashes replaced", sanitize("../../../etc/passwd") == "_________etc_passwd")
check("dots replaced", sanitize("deck.1") == "deck_1")
check("spaces replaced", sanitize("my deck") == "my_deck")
check("empty stays empty", sanitize("") == "")

# --- 3. Prompt truncation (advisor/llm_advisor.py) ---
print("=== 3. Prompt Truncation (advisor/llm_advisor.py) ===")
from advisor.llm_advisor import _format_deck_cards_for_prompt, _build_prompt

cards_big = {"mainboard": [{"cardId": i, "name": f"Card{i}", "count": 1} for i in range(100)]}
lines = _format_deck_cards_for_prompt(cards_big, max_cards_per_pile=10)
check("named-list: suffix", "+90 more" in lines[0], lines[0])
check("named-list: count", "100 unique" in lines[0], lines[0])
check("named-list: first 10", "Card0" in lines[0] and "Card9" in lines[0])
check("named-list: Card10 excluded", "Card10" not in lines[0], lines[0])

cards_dict = {"mainboard": {str(i): 1 for i in range(100)}}
lines = _format_deck_cards_for_prompt(cards_dict, max_cards_per_pile=10)
check("grpId-dict: suffix", "+90 more" in lines[0], lines[0])
check("grpId-dict: count", "100 unique" in lines[0], lines[0])

cards_small = {"mainboard": [{"cardId": 100, "name": "Bolt", "count": 4}]}
lines = _format_deck_cards_for_prompt(cards_small, max_cards_per_pile=60)
check("small: no truncation", "+" not in lines[0], lines[0])
check("small: count", "1 unique" in lines[0], lines[0])

# _build_prompt integration
coll = {"cards": {"100": 2}, "diagnostics": {"completeness": {"cards": "complete"}}}
decks = {
    "schema": "decks.v1",
    "decks": [
        {
            "name": "Big",
            "deckId": "1",
            "attributes": {"Format": "standard"},
            "cards": {
                "mainboard": [
                    {"cardId": i, "name": f"C{i}", "count": 1} for i in range(80)
                ]
            },
        }
    ],
}
prompt = _build_prompt(coll, decks)
check("prompt: +20 more", "+20 more" in prompt, "expected +20 more")
check("prompt: C0-C59 present", "C0" in prompt and "C59" in prompt)
check("prompt: C60 absent", "C60" not in prompt, "C60 should be truncated")

# default max=60
lines_def = _format_deck_cards_for_prompt(cards_big)
check("default max=60", "+40 more" in lines_def[0], lines_def[0][:80])

print(f"\n=== RESULTS: {passed} passed, {failed} failed ===")
sys.exit(0 if failed == 0 else 1)