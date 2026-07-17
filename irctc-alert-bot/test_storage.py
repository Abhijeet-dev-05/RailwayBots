"""
test_storage.py — Local test for all storage.py functions.

Writes to watches.json in the current directory.
The test cleans up after itself (removes the watch it creates).

Run with:
    python test_storage.py
"""

import json
import os

import storage

DIVIDER  = "-" * 55
CHAT_ID  = "123456"    # dummy Telegram chat ID


def section(title: str) -> None:
    print(f"\n{DIVIDER}")
    print(f"  {title}")
    print(DIVIDER)


def assert_equal(label: str, got, expected) -> None:
    status = "✅ PASS" if got == expected else f"❌ FAIL (expected {expected!r}, got {got!r})"
    print(f"  {label}: {status}")


if __name__ == "__main__":
    # ── 0. Clean slate: remove any leftover watches.json ─────────────
    if os.path.exists("watches.json"):
        os.remove("watches.json")
        print("🗑️  Removed existing watches.json for clean test run.")

    # ── 1. add_watch ─────────────────────────────────────────────────
    section("1. add_watch()")
    watch = storage.add_watch(
        chat_id=CHAT_ID,
        train_number="12951",
        from_station="BCT",
        to_station="NDLS",
        date="15-Jun-2026",
        travel_class="3A",
    )
    print(f"  Created watch:")
    for k, v in watch.items():
        print(f"    {k:15s}: {v}")

    assert_equal("train_number",  watch["train_number"],  "12951")
    assert_equal("from_station",  watch["from_station"],  "BCT")
    assert_equal("to_station",    watch["to_station"],    "NDLS")
    assert_equal("travel_class",  watch["travel_class"],  "3A")
    assert_equal("chat_id",       watch["chat_id"],       CHAT_ID)
    assert_equal("last_status",   watch["last_status"],   {})

    watch_id = watch["id"]
    print(f"\n  Watch ID: {watch_id}")

    # ── 2. load_watches ───────────────────────────────────────────────
    section("2. load_watches() — all watches in file")
    all_watches = storage.load_watches()
    print(f"  Total watches in file: {len(all_watches)}")
    assert_equal("watch count", len(all_watches), 1)
    print(f"  Raw JSON preview:\n  {json.dumps(all_watches[0], indent=4)}")

    # ── 3. get_watches_for_user ───────────────────────────────────────
    section("3. get_watches_for_user('123456')")
    user_watches = storage.get_watches_for_user(CHAT_ID)
    print(f"  Watches for chat_id={CHAT_ID}: {len(user_watches)}")
    assert_equal("user watch count", len(user_watches), 1)

    # Also verify a different user gets nothing
    other_watches = storage.get_watches_for_user("999999")
    print(f"  Watches for chat_id=999999:  {len(other_watches)}")
    assert_equal("other user watch count", len(other_watches), 0)

    # ── 4. get_all_watches ────────────────────────────────────────────
    section("4. get_all_watches()")
    all_w = storage.get_all_watches()
    print(f"  Total watches returned: {len(all_w)}")
    assert_equal("get_all_watches count", len(all_w), 1)

    # ── 5. update_watch_status ────────────────────────────────────────
    section("5. update_watch_status()")
    dummy_status = {
        "status":          "AVAILABLE",
        "exact":           "AVAILABLE 12",
        "count":           12,
        "is_available":    True,
        "is_waitlist":     False,
        "waitlist_number": None,
        "raw_text":        "AVAILABLE 12",
    }
    storage.update_watch_status(watch_id, dummy_status)

    # Reload and verify status was written
    updated_watches = storage.load_watches()
    updated_watch   = next((w for w in updated_watches if w["id"] == watch_id), None)

    if updated_watch:
        print(f"  last_status after update:")
        for k, v in updated_watch["last_status"].items():
            print(f"    {k:15s}: {v}")
        assert_equal("is_available",   updated_watch["last_status"]["is_available"], True)
        assert_equal("count",          updated_watch["last_status"]["count"], 12)
        assert_equal("last_checked present", "last_checked" in updated_watch, True)
        print(f"  last_checked: {updated_watch.get('last_checked')}")
    else:
        print("  ❌ FAIL: watch not found after update")

    # ── 6. remove_watch ───────────────────────────────────────────────
    section("6. remove_watch()")
    removed = storage.remove_watch(watch_id)
    assert_equal("remove_watch returned True", removed, True)

    # Trying to remove the same ID again should return False
    removed_again = storage.remove_watch(watch_id)
    assert_equal("second remove returns False", removed_again, False)

    # Confirm file is now empty
    remaining = storage.load_watches()
    print(f"  Watches remaining after removal: {len(remaining)}")
    assert_equal("watches after removal", len(remaining), 0)

    # ── 7. Cleanup ────────────────────────────────────────────────────
    section("7. Cleanup")
    if os.path.exists("watches.json"):
        os.remove("watches.json")
        print("  🗑️  watches.json removed.")

    print(f"\n{DIVIDER}")
    print("✅ All storage tests completed.")
    print(DIVIDER)
