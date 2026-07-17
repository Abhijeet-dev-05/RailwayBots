"""
app.py — Main FastAPI webhook server for the IRCTC Tatkal Seat Alert Bot.

Handles all incoming Telegram messages via webhook, routes them through
AI intent detection, and manages watch creation/deletion/status commands.
"""

import os
import asyncio
from contextlib import asynccontextmanager
from functools import partial

import requests
import uvicorn
from dotenv import load_dotenv
from fastapi import FastAPI, Request
from fastapi.responses import PlainTextResponse

import scheduler
import scraper
import storage
from ai_helper import (
    determine_intent,
    get_alternative_trains,
    parse_natural_language,
    parse_stop_command,
    plan_journey,
)

# ---------------------------------------------------------------------------
# Setup
# ---------------------------------------------------------------------------

load_dotenv()

TELEGRAM_TOKEN   = os.getenv("TELEGRAM_TOKEN")
TELEGRAM_API_URL = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}"

# Deduplicate Telegram retries — store last 100 processed update_ids
_seen_update_ids: set = set()

# ---------------------------------------------------------------------------
# Lifespan — startup / shutdown
# ---------------------------------------------------------------------------

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Start the background scheduler when the server boots."""
    print("[app] Starting background scheduler...")
    scheduler.start_scheduler()
    print("[app] ✅ Scheduler running. Bot is live.")
    yield
    # Nothing special needed on shutdown — APScheduler daemon thread exits with the process
    print("[app] 🛑 Shutting down.")


app = FastAPI(lifespan=lifespan)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def send_message(chat_id: str, text: str) -> None:
    """
    Send an HTML-formatted Telegram message.

    Args:
        chat_id (str): Telegram chat ID.
        text    (str): HTML message body.
    """
    url     = f"{TELEGRAM_API_URL}/sendMessage"
    payload = {
        "chat_id":    chat_id,
        "text":       text,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }
    try:
        resp = requests.post(url, json=payload, timeout=10)
        resp.raise_for_status()
    except requests.exceptions.RequestException as e:
        print(f"[app] ❌ send_message failed for chat_id={chat_id}: {e}")


def _build_watch_list_message(watches: list) -> str:
    """Return a formatted HTML string listing all watch dicts."""
    lines = [f"📋 <b>Your Active Watches ({len(watches)})</b>\n"]
    for i, w in enumerate(watches, start=1):
        last = w.get("last_status", {})
        status_text = last.get("exact", "Pending first check")
        lines.append(
            f"<b>{i}. Train {w.get('train_number', '?')}</b>\n"
            f"   🛫 {w.get('from_station', '?')} → 🛬 {w.get('to_station', '?')}\n"
            f"   📅 {w.get('date', '?')} | 💺 {w.get('travel_class', '?')}\n"
            f"   📊 Status: {status_text}\n"
            f"   🆔 ID: <code>{w.get('id', '')[:8]}</code>\n"
        )
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Webhook route
# ---------------------------------------------------------------------------

DIVIDER = "━━━━━━━━━━━━━━━━━━━━━━"

@app.post(f"/webhook/{TELEGRAM_TOKEN}")
async def telegram_webhook(request: Request) -> PlainTextResponse:
    """
    Receive and process all Telegram updates.

    Always returns HTTP 200 so Telegram does not retry failed deliveries.
    Every code path is wrapped in try/except — the bot must never crash.
    """
    try:
        data = await request.json()

        # ── Deduplicate: ignore updates we've already processed ──────────
        update_id = data.get("update_id")
        if update_id is not None:
            if update_id in _seen_update_ids:
                print(f"[app] Duplicate update_id {update_id} — skipping.")
                return PlainTextResponse("ok", status_code=200)
            _seen_update_ids.add(update_id)
            # Keep the set bounded so it doesn't grow forever
            if len(_seen_update_ids) > 100:
                _seen_update_ids.pop()

        # ── Extract core fields ──────────────────────────────────────────
        message = data.get("message") or data.get("edited_message")
        if not message:
            return PlainTextResponse("ok", status_code=200)

        chat_id = str(message.get("chat", {}).get("id", ""))
        text    = message.get("text", "").strip()

        if not chat_id or not text:
            return PlainTextResponse("ok", status_code=200)

        print(f"[app] Message from {chat_id}: {text}")

        # ================================================================
        # COMMAND ROUTING
        # ================================================================

        # ── /start  /help ────────────────────────────────────────────────
        if text.startswith("/start") or text.startswith("/help"):
            send_message(chat_id, (
                f"🚂 <b>IRCTC Tatkal Seat Alert Bot</b>\n"
                f"{DIVIDER}\n"
                f"Monitor train seats &amp; get instant Telegram alerts!\n\n"
                f"<b>Commands:</b>\n"
                f"/watch — Start monitoring a train\n"
                f"/list — See your active watches\n"
                f"/stop [id] — Stop a watch\n"
                f"/status [id] — Check live status\n"
                f"/statusall — Check all watches live\n"
                f"/help — Show this help\n\n"
                f"<b>Quick add example:</b>\n"
                f"<code>12951 BCT NDLS 15-Jun-2026 3A</code>\n"
                f"{DIVIDER}"
            ))

        # ── /watch ───────────────────────────────────────────────────────
        elif text.startswith("/watch"):
            send_message(chat_id, (
                f"🚂 <b>Add a Watch</b>\n"
                f"{DIVIDER}\n"
                f"Send your train details in this format:\n\n"
                f"<code>TRAIN_NO FROM TO DATE CLASS</code>\n\n"
                f"Example:\n"
                f"<code>12951 BCT NDLS 15-Jun-2026 3A</code>\n"
                f"{DIVIDER}"
            ))

        # ── /list ────────────────────────────────────────────────────────
        elif text.startswith("/list"):
            watches = storage.get_watches_for_user(chat_id)
            if not watches:
                send_message(chat_id, (
                    f"📋 <b>No active watches.</b>\n\n"
                    f"Send train details to start monitoring!\n"
                    f"Example: <code>12951 BCT NDLS 15-Jun-2026 3A</code>"
                ))
            else:
                send_message(chat_id, _build_watch_list_message(watches))

        # ── /stop [id] ───────────────────────────────────────────────────
        elif text.startswith("/stop"):
            parts = text.split()
            if len(parts) < 2:
                send_message(chat_id, (
                    "⚠️ Please provide a watch ID.\n"
                    "Usage: <code>/stop &lt;id&gt;</code>\n"
                    "Use /list to see your watch IDs."
                ))
            else:
                partial_id  = parts[1].strip()
                all_watches = storage.get_watches_for_user(chat_id)
                matched     = next(
                    (w for w in all_watches if w.get("id", "").startswith(partial_id)),
                    None,
                )
                if matched:
                    storage.remove_watch(matched["id"])
                    send_message(chat_id, (
                        f"❌ <b>Watch stopped</b> for train "
                        f"<b>{matched.get('train_number', '?')}</b>\n"
                        f"({matched.get('from_station')} → {matched.get('to_station')}, "
                        f"{matched.get('date')})"
                    ))
                else:
                    send_message(chat_id, (
                        f"⚠️ Watch with ID starting <code>{partial_id}</code> not found.\n"
                        f"Use /list to see your active watch IDs."
                    ))

        # ── /status [id] ────────────────────────────────────────────────
        elif text.startswith("/status "):
            parts       = text.split()
            partial_id  = parts[1].strip() if len(parts) > 1 else ""
            all_watches = storage.get_watches_for_user(chat_id)
            matched     = next(
                (w for w in all_watches if w.get("id", "").startswith(partial_id)),
                None,
            )
            if not matched:
                send_message(chat_id, "⚠️ Watch not found. Use /list to see your IDs.")
            else:
                send_message(chat_id, f"⏳ Checking live status for train <b>{matched.get('train_number')}</b>...")
                live = await asyncio.to_thread(scraper.get_current_status, matched)
                send_message(chat_id, (
                    f"📊 <b>Live Status</b>\n"
                    f"{DIVIDER}\n"
                    f"🚂 Train: <b>{matched.get('train_number')}</b>\n"
                    f"🛫 Route: {matched.get('from_station')} → {matched.get('to_station')}\n"
                    f"📅 Date:  {matched.get('date')}\n"
                    f"💺 Class: {matched.get('travel_class')}\n"
                    f"{DIVIDER}\n"
                    f"📡 Status: {live}\n"
                    f"{DIVIDER}"
                ))

        # ── /statusall ───────────────────────────────────────────────────
        elif text.startswith("/statusall"):
            watches = storage.get_watches_for_user(chat_id)
            if not watches:
                send_message(chat_id, "📋 No active watches to check.")
            else:
                send_message(chat_id, f"⏳ Checking live status for <b>{len(watches)}</b> watch(es)...")
                lines = [f"📡 <b>Live Status — All Watches</b>\n{DIVIDER}"]
                for w in watches:
                    live = await asyncio.to_thread(scraper.get_current_status, w)
                    lines.append(
                        f"🚂 <b>{w.get('train_number')}</b> | "
                        f"{w.get('from_station')}→{w.get('to_station')} | "
                        f"{w.get('date')} | {w.get('travel_class')}\n"
                        f"   └─ {live}"
                    )
                lines.append(DIVIDER)
                send_message(chat_id, "\n".join(lines))

        # ── /testAlert ───────────────────────────────────────────────────
        elif text.startswith("/testAlert") or text.startswith("/testalert"):
            send_message(chat_id, (
                f"🚨 <b>SEAT ALERT!</b> 🚨\n"
                f"{DIVIDER}\n"
                f"✅ <b>AVAILABLE — 12 Seats!</b>\n\n"
                f"🚂 Train: <b>12951</b>\n"
                f"🛫 BCT → 🛬 NDLS | 15-Jun-2026 | 3A\n"
                f"{DIVIDER}\n"
                f"🎟️ <a href='https://www.irctc.co.in'>Book on IRCTC</a>\n"
                f"⚡ <i>Jaldi karo! Tatkal seats go fast!</i>\n"
                f"{DIVIDER}"
            ))

        # ================================================================
        # AI-POWERED FREE TEXT ROUTING
        # ================================================================
        else:
            intent = determine_intent(text)
            print(f"[app] Intent detected: {intent}")

            # ── stop_watch ───────────────────────────────────────────────
            if intent == "stop_watch":
                active_watches = storage.get_watches_for_user(chat_id)
                if not active_watches:
                    send_message(chat_id, "📋 You have no active watches to stop.")
                else:
                    stop_result = parse_stop_command(text, active_watches)
                    watch_id    = stop_result.get("watch_id")
                    if watch_id:
                        matched = next(
                            (w for w in active_watches if w.get("id") == watch_id),
                            None,
                        )
                        storage.remove_watch(watch_id)
                        train_no = matched.get("train_number", "?") if matched else "?"
                        send_message(chat_id, (
                            f"❌ <b>Watch removed!</b>\n"
                            f"Train <b>{train_no}</b> is no longer being monitored."
                        ))
                    else:
                        send_message(chat_id, (
                            "⚠️ Could not identify which watch to stop.\n"
                            "Use <code>/stop &lt;id&gt;</code> to stop a specific watch.\n"
                            "Use /list to see your watch IDs."
                        ))

            # ── list_watches ─────────────────────────────────────────────
            elif intent == "list_watches":
                watches = storage.get_watches_for_user(chat_id)
                if not watches:
                    send_message(chat_id, (
                        "📋 <b>No active watches.</b>\n\n"
                        "Send train details to start monitoring!\n"
                        "Example: <code>12951 BCT NDLS 15-Jun-2026 3A</code>"
                    ))
                else:
                    send_message(chat_id, _build_watch_list_message(watches))

            # ── journey_planner ──────────────────────────────────────────
            elif intent == "journey_planner":
                result = plan_journey(text)
                send_message(chat_id, result)

            # ── create_watch (default) ───────────────────────────────────
            else:
                parsed = parse_natural_language(text)

                from_station  = parsed.get("from_station", "")
                to_station    = parsed.get("to_station", "")
                date          = parsed.get("date", "")
                train_number  = parsed.get("train_number")
                travel_class  = parsed.get("travel_class", "SL")

                has_route     = bool(from_station and to_station)
                missing_train = not train_number

                # Route found but no train number — ask user to provide it
                if has_route and missing_train:
                    send_message(chat_id, (
                        f"🚂 <b>Route Detected!</b>\n"
                        f"{DIVIDER}\n"
                        f"🛫 From:  <b>{from_station}</b>\n"
                        f"🛬 To:    <b>{to_station}</b>\n"
                        f"📅 Date:  <b>{date or 'Not specified'}</b>\n\n"
                        f"Please also send the <b>train number</b>.\n\n"
                        f"Example:\n"
                        f"<code>19569 {from_station} {to_station} "
                        f"{date or 'DD-Mon-YYYY'} SL</code>"
                    ))

                # All required fields present — create the watch
                elif parsed.get("is_valid"):
                    send_message(chat_id, (
                        f"⏳ Checking availability for train "
                        f"<b>{train_number}</b>..."
                    ))

                    # Run the blocking scrape in a thread pool so the
                    # async event loop is not stalled during sleep+HTTP
                    result = await asyncio.to_thread(
                        scraper.check_availability,
                        train_number,
                        from_station,
                        to_station,
                        date,
                        travel_class,
                    )

                    # Persist the watch
                    watch = storage.add_watch(
                        chat_id=chat_id,
                        train_number=train_number,
                        from_station=from_station,
                        to_station=to_station,
                        date=date,
                        travel_class=travel_class,
                    )

                    # Immediately store the first status so comparisons work
                    storage.update_watch_status(watch["id"], result)

                    # Build status display from result we already have —
                    # do NOT call get_current_status() again (wastes an API call)
                    status_code  = result.get("status", "UNKNOWN")
                    emoji        = scraper.STATUS_EMOJI.get(status_code, "❓")
                    exact        = result.get("exact", status_code)
                    emoji_status = f"{emoji} {exact}"
                    cp           = result.get("confirm_probability", "")
                    fare         = result.get("ticket_fare", 0)

                    # Build the confirmation message
                    confirm_msg = (
                        f"✅ <b>Watch Created!</b>\n"
                        f"{DIVIDER}\n"
                        f"🚂 Train: <b>{train_number}</b>\n"
                        f"🛫 Route: {from_station} → {to_station}\n"
                        f"📅 Date:  {date}\n"
                        f"💺 Class: {travel_class} (Tatkal)\n"
                        f"{DIVIDER}\n"
                        f"📊 Current Status: {emoji_status}\n"
                    )
                    if cp:
                        cp_emoji = {"High": "🔥", "Med": "🌡️", "Low": "🧊"}.get(cp, "📈")
                        confirm_msg += f"{cp_emoji} Confirm Probability: {cp}\n"
                    if fare:
                        confirm_msg += f"💰 Tatkal Fare: ₹{fare}\n"
                    confirm_msg += (
                        f"🆔 Watch ID: <code>{watch['id'][:8]}</code>\n"
                        f"{DIVIDER}\n"
                        f"🔔 You'll be alerted the moment seats open up.\n"
                        f"⏱️ Monitoring every <b>10 minutes</b>."
                    )
                    send_message(chat_id, confirm_msg)

                # Validation failed — show suggestion or generic error
                else:
                    suggestion = parsed.get("suggestion", "")
                    send_message(chat_id, (
                        f"{suggestion}\n\n"
                        if suggestion else
                        f"⚠️ <b>Invalid format.</b>\n\n"
                        f"Please use:\n"
                        f"<code>TRAIN FROM TO DATE CLASS</code>\n\n"
                        f"Example:\n"
                        f"<code>12951 BCT NDLS 15-Jun-2026 3A</code>"
                    ))

    except Exception as e:
        # Top-level safety net — log and continue, never crash
        print(f"[app] ❌ Unhandled exception in webhook: {e}")

    return PlainTextResponse("ok", status_code=200)


# ---------------------------------------------------------------------------
# Health check
# ---------------------------------------------------------------------------

@app.get("/")
async def root() -> PlainTextResponse:
    """Simple health check endpoint for Render / uptime monitors."""
    return PlainTextResponse("IRCTC Tatkal Alert Bot is running. ✅", status_code=200)


@app.get("/favicon.ico")
async def favicon():
    """Suppress browser favicon 404 noise."""
    return PlainTextResponse("", status_code=204)


# ---------------------------------------------------------------------------
# Local dev entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    uvicorn.run("app:app", host="0.0.0.0", port=8000, reload=True)
