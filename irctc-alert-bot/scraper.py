"""
scraper.py — RapidAPI IRCTC seat availability checker for the Tatkal Alert Bot.

Uses the irctc1.p.rapidapi.com API (reliable, works from any server IP)
instead of direct NTES scraping (which blocks non-Indian IPs on Render).

API endpoint: GET /api/v1/checkSeatAvailability
"""

import os
import re
import time
from datetime import datetime
from typing import Optional

import requests
from dotenv import load_dotenv

load_dotenv()

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

RAPIDAPI_KEY  = os.environ.get("RAPIDAPI_KEY", "")
RAPIDAPI_HOST = "irctc1.p.rapidapi.com"
AVAIL_URL     = "https://irctc1.p.rapidapi.com/api/v1/checkSeatAvailability"
SCHEDULE_URL  = "https://irctc1.p.rapidapi.com/api/v1/getTrainSchedule"

REQUEST_TIMEOUT = 10   # seconds
SCRAPE_DELAY    = 1    # polite delay between calls

# Date formats accepted from user input
DATE_FORMATS = ["%d-%b-%Y", "%d-%m-%Y", "%d/%m/%Y", "%d-%B-%Y"]

# ---------------------------------------------------------------------------
# Station code normalisation map
#
# The RapidAPI IRCTC backend uses the codes stored in train schedules.
# Some popular codes differ from what users commonly type:
#   BCT  → MMCT  (Mumbai Central)
#   CSTM → CSMT  (Chhatrapati Shivaji)
# All other standard codes (NDLS, SBC, MAS, ADI…) match directly.
# ---------------------------------------------------------------------------

STATION_ALIAS: dict = {
    "BCT":  "MMCT",   # Mumbai Central — BCT is the freight yard code
    "CSTM": "CSMT",   # Mumbai CST
}


def _resolve_station(code: str) -> str:
    """Return the station code the RapidAPI backend expects."""
    return STATION_ALIAS.get(code.upper(), code.upper())


# ---------------------------------------------------------------------------
# Emoji map
# ---------------------------------------------------------------------------

STATUS_EMOJI = {
    "AVAILABLE":     "🟢",
    "RAC":           "🟡",
    "WAITLIST":      "🔴",
    "REGRET":        "⛔",
    "PAST_DATE":     "⏮️",
    "INVALID_TRAIN": "❌",
    "ERROR":         "⚙️",
}


# ---------------------------------------------------------------------------
# Internal result factory — every return has the same shape
# ---------------------------------------------------------------------------

def _make_result(
    status: str,
    exact: str,
    count=None,
    is_available: bool = False,
    is_waitlist: bool = False,
    waitlist_number=None,
    raw_text: str = "",
    confirm_probability: str = "",
    ticket_fare: int = 0,
) -> dict:
    return {
        "status":               status,
        "exact":                exact,
        "count":                count,
        "is_available":         is_available,
        "is_waitlist":          is_waitlist,
        "waitlist_number":      waitlist_number,
        "raw_text":             raw_text,
        "confirm_probability":  confirm_probability,
        "ticket_fare":          ticket_fare,
    }


# ---------------------------------------------------------------------------
# 1. get_class_code — normalise travel class string
# ---------------------------------------------------------------------------

def get_class_code(travel_class: str) -> str:
    """Strip and uppercase the travel class. e.g. ' 3a ' → '3A'."""
    return travel_class.strip().upper()


# ---------------------------------------------------------------------------
# 2. parse_date — try multiple date formats
# ---------------------------------------------------------------------------

def parse_date(date_str: str) -> Optional[datetime]:
    """
    Parse a date string trying multiple formats.
    Returns datetime on success, None on failure.
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
    Returns True if date is strictly before today (midnight),
    False if today or future, None if unparseable.
    """
    parsed = parse_date(date_str)
    if parsed is None:
        return None
    today = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
    return parsed < today


def _format_date_for_api(date_str: str) -> Optional[str]:
    """
    Convert any supported date format to DD-MM-YYYY required by the API.
    e.g. '18-Jul-2026' → '18-07-2026'
    """
    parsed = parse_date(date_str)
    if parsed is None:
        return None
    return parsed.strftime("%d-%m-%Y")


# ---------------------------------------------------------------------------
# 4. check_availability — main function
# ---------------------------------------------------------------------------

def check_availability(
    train_number: str,
    from_station: str,
    to_station: str,
    date: str,
    travel_class: str,
) -> dict:
    """
    Check Tatkal seat availability via the RapidAPI IRCTC endpoint.

    Args:
        train_number  (str): e.g. "12951"
        from_station  (str): e.g. "BCT" or "MMCT"
        to_station    (str): e.g. "NDLS"
        date          (str): e.g. "18-Jul-2026" or "18-07-2026"
        travel_class  (str): e.g. "3A", "SL", "2A"

    Returns:
        dict with keys: status, exact, count, is_available, is_waitlist,
                        waitlist_number, raw_text, confirm_probability, ticket_fare
    """

    # ── Guard: past date ──────────────────────────────────────────────────
    past = is_past_date(date)
    if past is True:
        return _make_result(
            status="PAST_DATE",
            exact="Date is in the past",
            raw_text="PAST_DATE",
        )

    # ── Format date for API ───────────────────────────────────────────────
    api_date = _format_date_for_api(date)
    if not api_date:
        return _make_result(
            status="ERROR",
            exact=f"Could not parse date: {date}",
            raw_text="DATE_PARSE_ERROR",
        )

    # ── Polite delay ──────────────────────────────────────────────────────
    time.sleep(SCRAPE_DELAY)

    # ── Build request ─────────────────────────────────────────────────────
    api_key = RAPIDAPI_KEY or os.environ.get("RAPIDAPI_KEY", "")
    if not api_key:
        return _make_result(
            status="ERROR",
            exact="RAPIDAPI_KEY not set in environment.",
            raw_text="NO_API_KEY",
        )

    headers = {
        "x-rapidapi-host": RAPIDAPI_HOST,
        "x-rapidapi-key":  api_key,
    }
    params = {
        "classType":       get_class_code(travel_class),
        "fromStationCode": _resolve_station(from_station),
        "toStationCode":   _resolve_station(to_station),
        "quota":           "TQ",    # Tatkal quota
        "trainNo":         train_number.strip(),
        "date":            api_date,
    }

    print(f"[scraper] Calling RapidAPI | train={train_number} "
          f"| {_resolve_station(from_station)}→{_resolve_station(to_station)} "
          f"| {api_date} | {get_class_code(travel_class)} | TQ")

    # ── HTTP call ─────────────────────────────────────────────────────────
    try:
        resp = requests.get(AVAIL_URL, headers=headers, params=params,
                            timeout=REQUEST_TIMEOUT)
        # If rate-limited, wait 3s and retry once
        if resp.status_code == 429:
            print("[scraper] ⚠️  Rate limited (429). Retrying in 3s...")
            time.sleep(3)
            resp = requests.get(AVAIL_URL, headers=headers, params=params,
                                timeout=REQUEST_TIMEOUT)
        resp.raise_for_status()
    except requests.exceptions.Timeout:
        return _make_result(
            status="ERROR",
            exact="RapidAPI request timed out.",
            raw_text="TIMEOUT",
        )
    except requests.exceptions.RequestException as e:
        return _make_result(
            status="ERROR",
            exact=f"Network error: {e}",
            raw_text="REQUEST_ERROR",
        )

    # ── Parse response ────────────────────────────────────────────────────
    try:
        body = resp.json()
    except Exception:
        return _make_result(
            status="ERROR",
            exact="Could not parse API response as JSON.",
            raw_text=resp.text[:300],
        )

    print(f"[scraper] Raw API response: {body}")

    # API-level error (status: false)
    if not body.get("status"):
        msg = body.get("message", "Unknown API error")

        # Map well-known API messages to clean statuses
        if "outside" in msg.lower() and "arp" in msg.lower():
            return _make_result(
                status="PAST_DATE",
                exact="Date is outside Tatkal booking window (ARP)",
                raw_text=msg,
            )
        if "not an intermediate station" in msg.lower():
            return _make_result(
                status="INVALID_STATION",
                exact=f"Station not valid for this train: {msg}",
                raw_text=msg,
            )
        if "train" in msg.lower() and ("not found" in msg.lower() or "invalid" in msg.lower()):
            return _make_result(
                status="INVALID_TRAIN",
                exact=f"Train not found: {msg}",
                raw_text=msg,
            )
        return _make_result(
            status="ERROR",
            exact=msg,
            raw_text=msg,
        )

    # Successful response — extract first date entry
    data = body.get("data", [])
    if not data:
        return _make_result(
            status="UNKNOWN",
            exact="No availability data returned.",
            raw_text=str(body),
        )

    entry      = data[0]
    avl_status = entry.get("availablity_status", "").upper()   # e.g. "TQWL4/WL4"
    avl_text   = entry.get("seat_avl_text", "").upper()        # e.g. "WAITLIST"
    seat_count = entry.get("seat_avl", 0)
    cp_prob    = entry.get("confirm_probability", "")          # "Low"/"Med"/"High"
    fare       = entry.get("total_fare", 0)
    raw        = avl_status

    # ── Classify status ───────────────────────────────────────────────────

    if avl_text == "AVAILABLE" or re.search(r"\bAVAILABLE\b", avl_status):
        return _make_result(
            status="AVAILABLE",
            exact=f"AVAILABLE {seat_count}",
            count=seat_count,
            is_available=True,
            raw_text=raw,
            confirm_probability=cp_prob,
            ticket_fare=fare,
        )

    if avl_text == "RAC" or re.search(r"\bRAC\b", avl_status):
        num = seat_count if seat_count else 0
        return _make_result(
            status="RAC",
            exact=f"RAC {num}",
            count=num,
            is_available=False,
            is_waitlist=True,
            waitlist_number=num,
            raw_text=raw,
            confirm_probability=cp_prob,
            ticket_fare=fare,
        )

    if avl_text == "WAITLIST" or re.search(r"(?:TQWL|WL)\d+", avl_status):
        # Extract numeric WL position from e.g. "TQWL4/WL4"
        wl_match = re.search(r"WL(\d+)", avl_status)
        wl_num   = int(wl_match.group(1)) if wl_match else seat_count
        return _make_result(
            status="WAITLIST",
            exact=f"WL{wl_num} ({avl_status})",
            count=wl_num,
            is_waitlist=True,
            waitlist_number=wl_num,
            raw_text=raw,
            confirm_probability=cp_prob,
            ticket_fare=fare,
        )

    if "REGRET" in avl_status or avl_text == "REGRET":
        return _make_result(
            status="REGRET",
            exact="REGRET — No Tatkal seats available",
            raw_text=raw,
        )

    # Fallback — show raw status string so user can see what happened
    return _make_result(
        status="UNKNOWN",
        exact=avl_status or avl_text or "Unknown",
        raw_text=raw,
        confirm_probability=cp_prob,
        ticket_fare=fare,
    )


# ---------------------------------------------------------------------------
# 5. get_current_status — emoji + text summary for display
# ---------------------------------------------------------------------------

def get_current_status(watch: dict) -> str:
    """
    Run a live check for a watch and return a one-line emoji status string.

    Returns e.g. "🔴 WL4 (TQWL4/WL4)" or "🟢 AVAILABLE 12"
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
        emoji  = STATUS_EMOJI.get(status, "❓")
        return f"{emoji} {result.get('exact', status)}"
    except Exception as e:
        return f"❓ Error: {e}"


# ---------------------------------------------------------------------------
# 6. format_availability_message — HTML Telegram alert
# ---------------------------------------------------------------------------

def format_availability_message(watch: dict, result: dict) -> str:
    """
    Build an HTML-formatted Telegram message for a seat availability alert.
    """
    status = result.get("status", "UNKNOWN")
    exact  = result.get("exact", "—")
    cp     = result.get("confirm_probability", "")
    fare   = result.get("ticket_fare", 0)

    status_line_map = {
        "AVAILABLE":       "✅ <b>AVAILABLE</b>",
        "RAC":             "⚠️ <b>RAC</b>",
        "WAITLIST":        "🛑 <b>WAITLIST</b>",
        "REGRET":          "❌ <b>REGRET — Fully Booked</b>",
        "PAST_DATE":       "⏮️ <b>Past Date / Outside Booking Window</b>",
        "INVALID_TRAIN":   "❌ <b>Invalid Train Number</b>",
        "INVALID_STATION": "❌ <b>Invalid Station for this Train</b>",
        "ERROR":           "⚙️ <b>Check Error</b>",
        "UNKNOWN":         "❓ <b>Unknown Status</b>",
    }
    status_line = status_line_map.get(status, f"❓ <b>{status}</b>")
    divider = "━━━━━━━━━━━━━━"

    msg = (
        f"🚨 <b>SEAT ALERT!</b> 🚨\n"
        f"{divider}\n"
        f"🚂 <b>Train:</b> {watch.get('train_number', '—')}\n"
        f"🛫 <b>From:</b>  {watch.get('from_station', '—')}\n"
        f"🛬 <b>To:</b>    {watch.get('to_station', '—')}\n"
        f"📅 <b>Date:</b>  {watch.get('date', '—')}\n"
        f"💺 <b>Class:</b> {watch.get('travel_class', '—')} (Tatkal)\n"
        f"{divider}\n"
        f"📊 <b>Status:</b> {status_line}\n"
        f"🔍 <b>Details:</b> {exact}\n"
    )

    if cp:
        cp_emoji = {"High": "🔥", "Med": "🌡️", "Low": "🧊"}.get(cp, "📈")
        msg += f"{cp_emoji} <b>Confirm Probability:</b> {cp}\n"

    if fare:
        msg += f"💰 <b>Tatkal Fare:</b> ₹{fare}\n"

    msg += f"{divider}\n"

    if status in ("AVAILABLE", "RAC"):
        msg += (
            f"🎟️ <b>Book now:</b> "
            f"<a href='https://www.irctc.co.in'>www.irctc.co.in</a>\n"
            f"⚡ <i>Jaldi karo! Tatkal seats go fast!</i>\n"
        )
    else:
        msg += "ℹ️ <i>Keep watching — cancellations can open seats!</i>\n"

    msg += divider
    return msg
