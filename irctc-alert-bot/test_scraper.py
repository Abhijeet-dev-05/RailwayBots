"""
test_scraper.py — Local test for scraper.check_availability()

No Telegram or Flask needed. Run directly with:
    python test_scraper.py
"""

from datetime import datetime, timedelta
from scraper import check_availability, format_availability_message

DIVIDER = "-" * 55


def get_next_week_date() -> str:
    """Return a date string 7 days from today in DD-Mon-YYYY format."""
    target = datetime.now() + timedelta(days=7)
    return target.strftime("%d-%b-%Y")


def run_test(label: str, train_number: str, from_stn: str, to_stn: str,
             date: str, travel_class: str) -> None:
    print(f"\n{DIVIDER}")
    print(f"TEST: {label}")
    print(f"  Train : {train_number}")
    print(f"  Route : {from_stn} → {to_stn}")
    print(f"  Date  : {date}")
    print(f"  Class : {travel_class}")
    print(DIVIDER)

    result = check_availability(
        train_number=train_number,
        from_station=from_stn,
        to_station=to_stn,
        date=date,
        travel_class=travel_class,
    )

    # Print every field in the result dict
    print("📦 Result dict:")
    for key, value in result.items():
        print(f"   {key:20s}: {value}")

    # Also show the formatted Telegram message preview
    dummy_watch = {
        "train_number": train_number,
        "from_station": from_stn,
        "to_station":   to_stn,
        "date":         date,
        "travel_class": travel_class,
    }
    print("\n📨 Formatted Telegram message preview:")
    print(format_availability_message(dummy_watch, result))


if __name__ == "__main__":
    next_week = get_next_week_date()
    print(f"\n🗓️  Using date: {next_week} (7 days from today)")

    # ── Test 1: Rajdhani Express BCT → NDLS, 3A ──────────────────────
    run_test(
        label="12951 Mumbai Rajdhani | BCT → NDLS | 3A",
        train_number="12951",
        from_stn="BCT",
        to_stn="NDLS",
        date=next_week,
        travel_class="3A",
    )

    # ── Test 2: Paschim Express BCT → NDLS, SL ───────────────────────
    run_test(
        label="12925 Paschim Express | BCT → NDLS | SL",
        train_number="12925",
        from_stn="BCT",
        to_stn="NDLS",
        date=next_week,
        travel_class="SL",
    )

    # ── Test 3: Past date (should return PAST_DATE without scraping) ──
    run_test(
        label="Past date guard (01-Jan-2024)",
        train_number="12951",
        from_stn="BCT",
        to_stn="NDLS",
        date="01-Jan-2024",
        travel_class="3A",
    )

    # ── Test 4: Invalid train number ──────────────────────────────────
    run_test(
        label="Invalid train number (99999)",
        train_number="99999",
        from_stn="BCT",
        to_stn="NDLS",
        date=next_week,
        travel_class="3A",
    )

    print(f"\n{DIVIDER}")
    print("✅ All scraper tests completed.")
    print(DIVIDER)
