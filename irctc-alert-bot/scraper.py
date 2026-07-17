"""
scraper.py — NTES seat availability scraper for IRCTC Tatkal watch requests.

Scrapes the Indian Railways NTES enquiry portal for real-time Tatkal seat
availability and returns structured result dicts consumed by the scheduler
and formatter functions.
"""

import re
import time
from datetime import datetime
from typing import Optional

import requests
from bs4 import BeautifulSoup

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Base URL template for NTES Tatkal availability (quota = TQ)
NTES_URL = (
    "https://enquiry.indianrail.gov.in/mntes/q"
    "?opt=TR&subOpt=avlFar"
    "&trainNo={train_number}"
    "&trainName="
    "&jrnyDate={date}"
    "&jrnyClass={class_code}"
    "&quota=TQ"
    "&aCode=TQ"
    "&fromStn={from_station}"
    "&toStn={to_station}"
    "&submitBut=Check+Availability"
)

# Date formats we try when parsing user-provided date strings
DATE_FORMATS = ["%d-%b-%Y", "%d-%m-%Y", "%d/%m/%Y", "%d-%B-%Y"]

# Request timeout in seconds
REQUEST_TIMEOUT = 8

# Polite delay between requests to avoid hammering the server
SCRAPE_DELAY = 1

# Minimal browser-like headers to reduce the chance of being blocked
DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9",
    "Accept": "text/html,application/xhtml+xml,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}

# Emoji map used by get_current_status()
STATUS_EMOJI = {
    "AVAILABLE":     "🟢",
    "RAC":           "🟡",
    "WAITLIST":      "🔴",
    "REGRET":        "⛔",
    "PAST_DATE":     "⏮️",
    "INVALID_TRAIN": "❌",
}

# ---------------------------------------------------------------------------
# Helper: build a clean "not-found / error" result dict
# ---------------------------------------------------------------------------

def _make_result(
    status: str,
    exact: str,
    count=None,
    is_available: bool = False,
    is_waitlist: bool = False,
    waitlist_number=None,
    raw_text: str = "",
) -> dict:
    """Internal factory so every return path has the same shape."""
    return {
        "status": status,
        "exact": exact,
        "count": count,
        "is_available": is_available,
        "is_waitlist": is_waitlist,
        "waitlist_number": waitlist_number,
        "raw_text": raw_text,
    }


# ---------------------------------------------------------------------------
# 1. get_class_code
# ---------------------------------------------------------------------------

def get_class_code(travel_class: str) -> str:
    """
    Normalise a travel class string for use in the NTES URL.

    Strips surrounding whitespace and uppercases the value.
    Valid codes: SL, 3A, 2A, 1A, CC, EC.

    Args:
        travel_class (str): Raw class string from user input, e.g. " 3a ".

    Returns:
        str: Cleaned class code, e.g. "3A".
    """
    return travel_class.strip().upper()


# ---------------------------------------------------------------------------
# 2. parse_date
# ---------------------------------------------------------------------------

def parse_date(date_str: str) -> Optional[datetime]:
    """
    Try multiple date formats and return a datetime object on the first match.

    Formats attempted in order:
        - "15-Jun-2026"  → %d-%b-%Y
        - "15-06-2026"   → %d-%m-%Y
        - "15/06/2026"   → %d/%m/%Y
        - "15-June-2026" → %d-%B-%Y

    Args:
        date_str (str): Raw date string.

    Returns:
        Optional[datetime]: Parsed datetime, or None if no format matched.
    """
    if not date_str:
        return None

    for fmt in DATE_FORMATS:
        try:
            return datetime.strptime(date_str.strip(), fmt)
        except ValueError:
            continue

    return None


# ---------------------------------------------------------------------------
# 3. is_past_date
# ---------------------------------------------------------------------------

def is_past_date(date_str: str) -> Optional[bool]:
    """
    Check whether the given date is strictly in the past.

    Comparison is done at midnight so today itself is not considered past.

    Args:
        date_str (str): Journey date string.

    Returns:
        bool  : True  → date is in the past.
                False → date is today or in the future.
        Optional: date string could not be parsed.
    """
    parsed = parse_date(date_str)
    if parsed is None:
        return None  # unparseable — caller decides what to do

    today_midnight = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
    return parsed < today_midnight


# ---------------------------------------------------------------------------
# 4. check_availability
# ---------------------------------------------------------------------------

def check_availability(
    train_number: str,
    from_station: str,
    to_station: str,
    date: str,
    travel_class: str,
) -> dict:
    """
    Scrape NTES for Tatkal seat availability on a specific train/route/date.

    Flow:
        1. Reject past dates immediately without a network call.
        2. Sleep briefly to be polite to the server.
        3. Fetch the NTES page with a browser-like session.
        4. Parse the HTML and extract availability status via regex.
        5. Return a structured result dict.

    Args:
        train_number  (str): e.g. "12951"
        from_station  (str): e.g. "BCT"
        to_station    (str): e.g. "NDLS"
        date          (str): e.g. "15-Jun-2026"
        travel_class  (str): e.g. "3A"

    Returns:
        dict with keys: status, exact, count, is_available, is_waitlist,
                        waitlist_number, raw_text
    """

    # --- Step 1: Reject past dates without hitting the network ---------------
    past = is_past_date(date)
    if past is True:
        return _make_result(
            status="PAST_DATE",
            exact="Date is in the past",
            count=None,
            is_available=False,
            is_waitlist=False,
            waitlist_number=None,
            raw_text="PAST_DATE",
        )

    # --- Step 2: Polite delay -------------------------------------------------
    time.sleep(SCRAPE_DELAY)

    # --- Step 3: Build URL and fetch -----------------------------------------
    class_code = get_class_code(travel_class)
    url = NTES_URL.format(
        train_number=train_number.strip(),
        date=date.strip(),
        class_code=class_code,
        from_station=from_station.strip().upper(),
        to_station=to_station.strip().upper(),
    )

    try:
        session = requests.Session()
        session.headers.update(DEFAULT_HEADERS)

        response = session.get(url, timeout=REQUEST_TIMEOUT)
        response.raise_for_status()

    except requests.exceptions.Timeout:
        return _make_result(
            status="ERROR",
            exact="Request timed out. NTES server did not respond.",
            raw_text="TIMEOUT",
        )
    except requests.exceptions.RequestException as e:
        return _make_result(
            status="ERROR",
            exact=f"Network error: {e}",
            raw_text="REQUEST_ERROR",
        )

    # --- Step 4: Parse HTML --------------------------------------------------
    try:
        soup = BeautifulSoup(response.text, "lxml")
    except Exception:
        # lxml unavailable; fall back to html.parser
        soup = BeautifulSoup(response.text, "html.parser")

    # Flatten the whole page to uppercase text for regex matching
    page_text = soup.get_text(separator=" ", strip=True).upper()

    # --- Step 5: Match known status strings ----------------------------------

    # Invalid / unknown train
    if "TRAIN NOT FOUND" in page_text or "NO TRAIN" in page_text:
        return _make_result(
            status="INVALID_TRAIN",
            exact="Train not found on NTES",
            raw_text="INVALID_TRAIN",
        )

    # REGRET — fully booked, no Tatkal quota left
    if re.search(r"\bREGRET\b", page_text):
        return _make_result(
            status="REGRET",
            exact="REGRET — No seats available",
            is_available=False,
            raw_text=page_text[:300],
        )

    # AVAILABLE N  (e.g. "AVAILABLE 42")
    avail_match = re.search(r"AVAILABLE[-\s]+(\d+)", page_text)
    if avail_match:
        count = int(avail_match.group(1))
        return _make_result(
            status="AVAILABLE",
            exact=f"AVAILABLE {count}",
            count=count,
            is_available=True,
            raw_text=page_text[:300],
        )

    # RAC N  (Reservation Against Cancellation)
    rac_match = re.search(r"\bRAC[-\s]+(\d+)", page_text)
    if rac_match:
        count = int(rac_match.group(1))
        return _make_result(
            status="RAC",
            exact=f"RAC {count}",
            count=count,
            is_available=False,   # not a confirmed berth
            is_waitlist=True,     # still on a list
            waitlist_number=count,
            raw_text=page_text[:300],
        )

    # WL N or WAITLIST N
    wl_match = re.search(r"(?:WL|WAITLIST)[-\s#/]+(\d+)", page_text)
    if wl_match:
        wl_number = int(wl_match.group(1))
        return _make_result(
            status="WAITLIST",
            exact=f"WL{wl_number}",
            count=wl_number,
            is_waitlist=True,
            waitlist_number=wl_number,
            raw_text=page_text[:300],
        )

    # Could not identify the status from the page
    return _make_result(
        status="UNKNOWN",
        exact="Could not parse availability from page",
        raw_text=page_text[:300],
    )


# ---------------------------------------------------------------------------
# 5. get_current_status
# ---------------------------------------------------------------------------

def get_current_status(watch: dict) -> str:
    """
    Run a scrape for a watch request and return a single emoji status indicator.

    Args:
        watch (dict): A watch dict as stored by storage.py.

    Returns:
        str: One of 🟢 🟡 🔴 ⛔ ⏮️ ❌ ❓ followed by the exact status text.
    """
    try:
        result = check_availability(
            train_number=watch["train_number"],
            from_station=watch["from_station"],
            to_station=watch["to_station"],
            date=watch["date"],
            travel_class=watch["travel_class"],
        )
        status = result.get("status", "UNKNOWN")
        emoji = STATUS_EMOJI.get(status, "❓")
        return f"{emoji} {result.get('exact', status)}"

    except Exception as e:
        return f"❓ Error checking status: {e}"


# ---------------------------------------------------------------------------
# 6. format_availability_message
# ---------------------------------------------------------------------------

def format_availability_message(watch: dict, result: dict) -> str:
    """
    Build an HTML-formatted Telegram message for a seat availability alert.

    Args:
        watch  (dict): Watch request dict (train_number, stations, date, class).
        result (dict): Result dict returned by check_availability().

    Returns:
        str: HTML string ready to send via Telegram's parse_mode=HTML.
    """
    status = result.get("status", "UNKNOWN")
    exact  = result.get("exact", "—")

    # Choose the right emoji + label for the status line
    status_line_map = {
        "AVAILABLE": "✅ <b>AVAILABLE</b>",
        "RAC":       "⚠️ <b>RAC</b>",
        "WAITLIST":  "🛑 <b>WAITLIST</b>",
        "REGRET":    "❌ <b>REGRET — Fully Booked</b>",
        "PAST_DATE": "⏮️ <b>Past Date</b>",
        "INVALID_TRAIN": "❌ <b>Invalid Train</b>",
        "ERROR":     "⚙️ <b>Error</b>",
        "UNKNOWN":   "❓ <b>Unknown</b>",
    }
    status_line = status_line_map.get(status, f"❓ <b>{status}</b>")

    divider = "━━━━━━━━━━━━━━"

    message = (
        f"🚨 <b>SEAT ALERT!</b> 🚨\n"
        f"{divider}\n"
        f"🚂 <b>Train:</b> {watch.get('train_number', '—')}\n"
        f"🛫 <b>From:</b> {watch.get('from_station', '—')}\n"
        f"🛬 <b>To:</b>   {watch.get('to_station', '—')}\n"
        f"📅 <b>Date:</b>  {watch.get('date', '—')}\n"
        f"💺 <b>Class:</b> {watch.get('travel_class', '—')} (Tatkal)\n"
        f"{divider}\n"
        f"📊 <b>Status:</b> {status_line}\n"
        f"🔍 <b>Details:</b> {exact}\n"
        f"{divider}\n"
    )

    # Add a booking CTA only when seats might actually be bookable
    if status in ("AVAILABLE", "RAC"):
        message += (
            f"🎟️ <b>Book now:</b> <a href='https://www.irctc.co.in'>www.irctc.co.in</a>\n"
            f"⚡ <i>Jaldi karo! Tatkal seats go fast!</i>\n"
        )
    else:
        message += f"ℹ️ <i>Keep watching — cancellations can open seats!</i>\n"

    message += f"{divider}"

    return message
