"""
storage.py — JSON-based persistence layer for IRCTC Tatkal watch requests.

All watch requests are stored in watches.json in the project root.
Each watch tracks a specific train/route/date/class combination for a Telegram user.
"""

import json
import uuid
from datetime import datetime

# Path to the local JSON storage file
WATCHES_FILE = "watches.json"


def load_watches() -> list:
    """
    Load all watch requests from watches.json.

    Returns:
        list: All stored watch dicts, or an empty list if the file
              doesn't exist or is corrupted.
    """
    try:
        with open(WATCHES_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        # File hasn't been created yet — that's fine
        return []
    except json.JSONDecodeError:
        # File exists but is malformed; start fresh rather than crashing
        return []


def save_watches(watches: list) -> None:
    """
    Persist the full list of watch requests to watches.json.

    Args:
        watches (list): The complete list of watch dicts to save.
    """
    try:
        with open(WATCHES_FILE, "w", encoding="utf-8") as f:
            json.dump(watches, f, indent=2)
    except OSError as e:
        print(f"[storage] ERROR: Could not write to {WATCHES_FILE}: {e}")


def add_watch(
    chat_id: str,
    train_number: str,
    from_station: str,
    to_station: str,
    date: str,
    travel_class: str,
) -> dict:
    """
    Create a new watch request and append it to storage.

    Args:
        chat_id      (str): Telegram chat ID of the requesting user.
        train_number (str): Train number, e.g. "12951".
        from_station (str): Departure station code, e.g. "BCT".
        to_station   (str): Arrival station code, e.g. "NDLS".
        date         (str): Journey date in DD-Mon-YYYY format, e.g. "15-Jun-2026".
        travel_class (str): Coach class, e.g. "3A", "SL", "2A".

    Returns:
        dict: The newly created watch dict.
    """
    try:
        watch = {
            "id": str(uuid.uuid4()),
            "chat_id": str(chat_id),          # always store as string
            "train_number": train_number,
            "from_station": from_station.upper(),
            "to_station": to_station.upper(),
            "date": date,
            "travel_class": travel_class.upper(),
            "created_at": datetime.now().isoformat(),
            "last_status": {},                 # populated later by the scraper
        }

        watches = load_watches()
        watches.append(watch)
        save_watches(watches)

        print(f"[storage] Watch added: {watch['id']} for chat_id={chat_id}")
        return watch

    except Exception as e:
        print(f"[storage] ERROR adding watch: {e}")
        raise


def remove_watch(watch_id: str) -> bool:
    """
    Remove a watch request by its unique ID.

    Args:
        watch_id (str): The UUID of the watch to remove.

    Returns:
        bool: True if the watch was found and removed, False otherwise.
    """
    try:
        watches = load_watches()
        original_count = len(watches)

        # Keep every watch except the one we want to remove
        updated = [w for w in watches if w.get("id") != watch_id]

        if len(updated) == original_count:
            # Nothing was filtered out — watch_id didn't exist
            return False

        save_watches(updated)
        print(f"[storage] Watch removed: {watch_id}")
        return True

    except Exception as e:
        print(f"[storage] ERROR removing watch {watch_id}: {e}")
        return False


def get_watches_for_user(chat_id: str) -> list:
    """
    Retrieve all watch requests belonging to a specific Telegram user.

    Args:
        chat_id (str): Telegram chat ID to filter by.

    Returns:
        list: Watch dicts whose chat_id matches the given value.
    """
    try:
        watches = load_watches()
        # Compare both sides as strings to avoid int/str mismatches
        return [w for w in watches if str(w.get("chat_id")) == str(chat_id)]

    except Exception as e:
        print(f"[storage] ERROR fetching watches for chat_id={chat_id}: {e}")
        return []


def get_all_watches() -> list:
    """
    Retrieve every watch request across all users.

    Returns:
        list: All watch dicts currently in storage.
    """
    try:
        return load_watches()
    except Exception as e:
        print(f"[storage] ERROR fetching all watches: {e}")
        return []


def update_watch_status(watch_id: str, status: dict) -> None:
    """
    Update the last-known seat availability status for a watch.

    Called by the scheduler after each scrape cycle so the bot can
    detect changes and notify the user only when availability shifts.

    Args:
        watch_id (str):  The UUID of the watch to update.
        status   (dict): Scraped availability data to store.
    """
    try:
        watches = load_watches()
        updated = False

        for w in watches:
            if w.get("id") == watch_id:
                w["last_status"] = status
                w["last_checked"] = datetime.now().isoformat()
                updated = True
                break

        if updated:
            save_watches(watches)
            print(f"[storage] Status updated for watch: {watch_id}")
        else:
            print(f"[storage] WARNING: watch_id {watch_id} not found for status update")

    except Exception as e:
        print(f"[storage] ERROR updating status for watch {watch_id}: {e}")
