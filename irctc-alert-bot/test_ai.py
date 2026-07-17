"""
test_ai.py — Local test for all ai_helper.py functions.

Requires a valid GROQ_API_KEY in your .env file.

Run with:
    python test_ai.py
"""

from dotenv import load_dotenv

# Load .env before importing ai_helper so GROQ_API_KEY is available
load_dotenv()

from ai_helper import (
    determine_intent,
    get_alternative_trains,
    parse_natural_language,
    parse_stop_command,
    plan_journey,
)

DIVIDER = "-" * 55


def section(title: str) -> None:
    print(f"\n{DIVIDER}")
    print(f"  {title}")
    print(DIVIDER)


def print_result(label: str, result) -> None:
    if isinstance(result, dict):
        print(f"\n  📦 {label}:")
        for k, v in result.items():
            print(f"    {k:20s}: {v}")
    else:
        print(f"\n  📝 {label}:\n  {result}")


# ── 1. determine_intent ───────────────────────────────────────────────────
section("1. determine_intent() — 3 messages")

test_messages = [
    ("Hinglish booking",   "Mumbai to Delhi kal 3A"),
    ("Stop command",       "stop watch"),
    ("List command",       "list my trains"),
    ("Journey planning",   "best train from Ahmedabad to Chennai"),
]

for label, msg in test_messages:
    intent = determine_intent(msg)
    print(f"  [{label}]")
    print(f"    Input  : \"{msg}\"")
    print(f"    Intent : {intent}")

# ── 2. parse_natural_language ─────────────────────────────────────────────
section("2. parse_natural_language() — 2 messages")

nl_tests = [
    ("Hinglish no train no", "Mumbai se Delhi kal 3A"),
    ("Full structured",      "12951 BCT NDLS 15-Jun-2026 3A"),
    ("Partial — date only",  "train 12925 SL class"),
]

for label, msg in nl_tests:
    print(f"\n  [{label}]")
    print(f"    Input: \"{msg}\"")
    result = parse_natural_language(msg)
    for k, v in result.items():
        print(f"    {k:20s}: {v}")

# ── 3. parse_stop_command ─────────────────────────────────────────────────
section("3. parse_stop_command()")

dummy_watches = [
    {
        "id":           "abc12345-0000-0000-0000-000000000001",
        "train_number": "12951",
        "from_station": "BCT",
        "to_station":   "NDLS",
        "date":         "15-Jun-2026",
        "travel_class": "3A",
    },
    {
        "id":           "def67890-0000-0000-0000-000000000002",
        "train_number": "12925",
        "from_station": "BCT",
        "to_station":   "NDLS",
        "date":         "20-Jun-2026",
        "travel_class": "SL",
    },
]

stop_tests = [
    "stop the rajdhani watch",
    "remove 12925",
    "cancel all alerts",
]

for msg in stop_tests:
    print(f"\n  Input: \"{msg}\"")
    result = parse_stop_command(msg, dummy_watches)
    print(f"    watch_id : {result.get('watch_id')}")
    print(f"    reason   : {result.get('reason')}")

# ── 4. plan_journey ───────────────────────────────────────────────────────
section("4. plan_journey()")
journey_msg = "Best train from Mumbai to Delhi for tomorrow in AC"
print(f"  Input: \"{journey_msg}\"")
print_result("Response", plan_journey(journey_msg))

# ── 5. get_alternative_trains ─────────────────────────────────────────────
section("5. get_alternative_trains()")
print("  Input: BCT → NDLS | 15-Jun-2026 | 3A | WL42")
result = get_alternative_trains(
    from_station="BCT",
    to_station="NDLS",
    date="15-Jun-2026",
    travel_class="3A",
    waitlist_status="WL42",
)
print_result("Response", result)

print(f"\n{DIVIDER}")
print("✅ All AI helper tests completed.")
print(DIVIDER)
