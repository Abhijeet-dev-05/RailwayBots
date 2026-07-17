"""
scheduler.py — APScheduler background engine for IRCTC Tatkal Alert Bot.

Runs a job every 10 minutes that:
  1. Loads all active watch requests from storage.
  2. Scrapes NTES for current seat availability on each watch.
  3. Compares the new result against the previously stored status.
  4. Sends a Telegram alert ONLY when availability changes from False → True
     (i.e. seats just opened up — avoids spamming the user every cycle).
  5. Persists the latest status back to storage after every check.
"""

import os

import requests
from apscheduler.schedulers.background import BackgroundScheduler

import scraper
import storage

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
TELEGRAM_API_URL = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}"


# ---------------------------------------------------------------------------
# 1. send_telegram_message
# ---------------------------------------------------------------------------

def send_telegram_message(chat_id: str, text: str) -> None:
    """
    Send an HTML-formatted message to a Telegram chat.

    Args:
        chat_id (str): Telegram chat ID of the recipient.
        text    (str): HTML message body (supports <b>, <i>, <a> tags).
    """
    url = f"{TELEGRAM_API_URL}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }

    try:
        response = requests.post(url, json=payload, timeout=10)
        response.raise_for_status()
        print(f"[scheduler] ✅ Message sent to chat_id={chat_id}")

    except requests.exceptions.Timeout:
        print(f"[scheduler] ⚠️  Telegram API timed out for chat_id={chat_id}")
    except requests.exceptions.HTTPError as e:
        print(
            f"[scheduler] ❌ Telegram HTTP error for chat_id={chat_id}: {e} "
            f"| Response: {response.text}"
        )
    except requests.exceptions.RequestException as e:
        print(f"[scheduler] ❌ Failed to send message to chat_id={chat_id}: {e}")


# ---------------------------------------------------------------------------
# 2. check_all_watches
# ---------------------------------------------------------------------------

def check_all_watches() -> None:
    """
    Main scheduled job: check every active watch and alert on availability change.

    Called automatically every 10 minutes by APScheduler.
    Each watch is processed independently — an error on one never blocks others.

    Alert logic:
        - was_available = last stored is_available (False if no prior check)
        - now_available = current scrape result is_available
        - Alert fires ONLY when: NOT was_available AND now_available
          (seats just became available — first positive detection)
    """
    print("\n" + "=" * 55)
    print("🔄 Running scheduled availability check...")
    print("=" * 55)

    watches = storage.get_all_watches()
    print(f"📋 Found {len(watches)} watch(es) to check\n")

    if not watches:
        print("[scheduler] No active watches. Sleeping until next cycle.")
        print("=" * 55 + "\n")
        return

    alerted = 0
    errors  = 0
    skipped = 0

    for idx, watch in enumerate(watches, start=1):
        watch_id     = watch.get("id", "unknown")
        chat_id      = watch.get("chat_id", "unknown")
        train_number = watch.get("train_number", "?")
        from_stn     = watch.get("from_station", "?")
        to_stn       = watch.get("to_station", "?")
        date         = watch.get("date", "?")
        travel_class = watch.get("travel_class", "?")

        print(
            f"[{idx}/{len(watches)}] Watch {watch_id[:8]}... "
            f"| Train {train_number} | {from_stn}→{to_stn} | {date} | {travel_class}"
        )

        try:
            # ----------------------------------------------------------------
            # a. Scrape current availability from NTES
            # ----------------------------------------------------------------
            result = scraper.check_availability(
                train_number=train_number,
                from_station=from_stn,
                to_station=to_stn,
                date=date,
                travel_class=travel_class,
            )

            current_status = result.get("status", "UNKNOWN")
            now_available  = result.get("is_available", False)
            exact          = result.get("exact", "—")

            print(f"         └─ Scraped: {current_status} | {exact}")

            # ----------------------------------------------------------------
            # b & c. Read last known status to detect change
            # ----------------------------------------------------------------
            last_status   = watch.get("last_status", {})
            was_available = last_status.get("is_available", False)

            # ----------------------------------------------------------------
            # d. Always persist the fresh result back to storage
            # ----------------------------------------------------------------
            storage.update_watch_status(watch_id, result)

            # ----------------------------------------------------------------
            # e. Send alert ONLY on False → True availability transition
            # ----------------------------------------------------------------
            if now_available and not was_available:
                # f. Build formatted HTML Telegram message and deliver it
                print(f"         🚨 CHANGE DETECTED: seats just became AVAILABLE — alerting!")
                message = scraper.format_availability_message(watch, result)
                send_telegram_message(chat_id, message)
                alerted += 1

            elif not now_available and was_available:
                # Seats disappeared — log but don't alert (no action needed)
                print(
                    f"         ℹ️  Was available, now {current_status}. "
                    f"Availability dropped — no alert sent."
                )
                skipped += 1

            elif current_status in ("PAST_DATE", "INVALID_TRAIN", "ERROR"):
                # g. Non-actionable statuses — skip silently
                print(f"         ⏭️  Non-actionable status ({current_status}). Skipping.")
                skipped += 1

            else:
                # g. No change — nothing to do
                print(f"         ✓  No availability change. No alert needed.")

        except Exception as e:
            # h. Isolate failures — one bad watch must not stop the rest
            errors += 1
            print(f"         ❌ ERROR processing watch {watch_id[:8]}: {e}")

        print()  # blank line between watches for readability in Render logs

    # Cycle summary — always visible at the top level of Render logs
    print("-" * 55)
    print(
        f"✅ Cycle complete | "
        f"Alerted: {alerted} | "
        f"Skipped: {skipped} | "
        f"Errors: {errors}"
    )
    print("=" * 55 + "\n")


# ---------------------------------------------------------------------------
# 3. start_scheduler
# ---------------------------------------------------------------------------

def start_scheduler() -> BackgroundScheduler:
    """
    Initialise and start the APScheduler BackgroundScheduler.

    Adds check_all_watches as an interval job that fires every 10 minutes.
    The scheduler runs in a daemon thread so it shuts down automatically
    when the main Flask / Gunicorn process exits.

    Returns:
        BackgroundScheduler: The running scheduler instance.
                             Keep a reference to it in app.py so it is not
                             garbage-collected.
    """
    scheduler = BackgroundScheduler(
        job_defaults={
            "coalesce": True,       # merge missed runs into one instead of piling up
            "max_instances": 1,     # never run two check cycles simultaneously
            "misfire_grace_time": 60,  # tolerate up to 60s late start before skipping
        }
    )

    scheduler.add_job(
        func=check_all_watches,
        trigger="interval",
        minutes=10,
        id="check_all_watches",
        name="IRCTC Tatkal availability check",
        replace_existing=True,
    )

    scheduler.start()

    print("[scheduler] ✅ BackgroundScheduler started — checking every 10 minutes.")
    print(f"[scheduler]    Next run: {scheduler.get_job('check_all_watches').next_run_time}")

    return scheduler
