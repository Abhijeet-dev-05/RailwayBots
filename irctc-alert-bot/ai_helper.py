"""
ai_helper.py — Groq AI natural language parser for IRCTC Tatkal Alert Bot.

Uses the Groq Python SDK (model: llama-3.3-70b-versatile) to:
  - Classify user intent
  - Extract structured train booking details from free-form messages
  - Identify which watch to stop
  - Provide journey planning suggestions
  - Suggest alternative trains when waitlisted
"""

import json
import os
from typing import Optional

from groq import Groq

# ---------------------------------------------------------------------------
# Client initialisation (lazy — created on first use so .env is loaded first)
# ---------------------------------------------------------------------------

_client: Optional[Groq] = None
GROQ_MODEL = "llama-3.3-70b-versatile"


def _get_client() -> Groq:
    """
    Return the shared Groq client, creating it on first call.

    Lazy init ensures load_dotenv() in app.py has already populated
    os.environ before we try to read GROQ_API_KEY.
    """
    global _client
    if _client is None:
        api_key = os.environ.get("GROQ_API_KEY")
        if not api_key:
            raise EnvironmentError(
                "GROQ_API_KEY is not set. "
                "Add it to your .env file or set it as an environment variable."
            )
        _client = Groq(api_key=api_key)
    return _client

# ---------------------------------------------------------------------------
# Station code lookup dictionary
# ---------------------------------------------------------------------------

STATION_CODES = {
    # Mumbai
    "mumbai": "BCT", "bombay": "BCT", "bct": "BCT",
    # Delhi
    "delhi": "NDLS", "new delhi": "NDLS", "ndls": "NDLS",
    # Baroda / Vadodara
    "baroda": "BRC", "vadodara": "BRC", "brc": "BRC",
    # Rajkot
    "rajkot": "RAJ", "raj": "RAJ",
    # Surat
    "surat": "ST", "st": "ST",
    # Bangalore / Bengaluru
    "bangalore": "SBC", "bengaluru": "SBC", "sbc": "SBC",
    # Chennai / Madras
    "chennai": "MAS", "madras": "MAS", "mas": "MAS",
    # Kolkata / Calcutta
    "kolkata": "KOAA", "calcutta": "KOAA", "koaa": "KOAA",
    # Ahmedabad
    "ahmedabad": "ADI", "adi": "ADI",
    # Bhavnagar
    "bhavnagar": "BHAV", "bhav": "BHAV",
    # Jamnagar
    "jamnagar": "JAM", "jam": "JAM",
    # Porbandar
    "porbandar": "PBR", "pbr": "PBR",
    # Veraval
    "veraval": "VRL", "vrl": "VRL",
    # Gandhidham
    "gandhidham": "GIMB", "gimb": "GIMB",
    # Bhuj
    "bhuj": "BHUJ",
}

# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _clean_json_response(text: str) -> str:
    """
    Strip markdown code fences that Groq sometimes wraps around JSON.

    e.g. ```json\\n{...}\\n``` → {...}
    """
    text = text.strip()
    if text.startswith("```"):
        # Remove opening fence (```json or ```)
        text = text.split("\n", 1)[-1]
    if text.endswith("```"):
        text = text.rsplit("```", 1)[0]
    return text.strip()


def _chat(prompt: str, max_tokens: int) -> str:
    """
    Send a single user prompt to Groq and return the assistant's reply text.

    Args:
        prompt     (str): The full prompt string.
        max_tokens (int): Upper bound on completion tokens.

    Returns:
        str: Raw response text from the model.
    """
    response = _get_client().chat.completions.create(
        model=GROQ_MODEL,
        messages=[{"role": "user", "content": prompt}],
        max_tokens=max_tokens,
        temperature=0.1,   # low temperature for deterministic JSON outputs
    )
    return response.choices[0].message.content.strip()


# ---------------------------------------------------------------------------
# 1. determine_intent
# ---------------------------------------------------------------------------

def determine_intent(user_message: str) -> str:
    """
    Classify a user message into one of four known intents.

    Intents:
        - create_watch    → user wants to add a new seat alert
        - stop_watch      → user wants to cancel an existing alert
        - list_watches    → user wants to see their active alerts
        - journey_planner → user wants train/route suggestions

    Args:
        user_message (str): Raw message text from Telegram.

    Returns:
        str: One of the four intent strings. Defaults to "create_watch"
             if classification fails or returns an unexpected value.
    """
    valid_intents = {"create_watch", "stop_watch", "list_watches", "journey_planner"}

    prompt = (
        f'Classify the following message into exactly one of these intents: '
        f'create_watch, stop_watch, list_watches, journey_planner.\n'
        f'Message: "{user_message}"\n'
        f'Return ONLY valid JSON with no explanation: {{"intent": "intent_name"}}'
    )

    try:
        raw = _chat(prompt, max_tokens=100)
        cleaned = _clean_json_response(raw)
        data = json.loads(cleaned)
        intent = data.get("intent", "create_watch")

        if intent not in valid_intents:
            print(f"[ai_helper] Unexpected intent '{intent}', defaulting to create_watch")
            return "create_watch"

        return intent

    except Exception as e:
        print(f"[ai_helper] determine_intent error: {e}")
        return "create_watch"


# ---------------------------------------------------------------------------
# 2. parse_natural_language
# ---------------------------------------------------------------------------

def parse_natural_language(user_message: str) -> dict:
    """
    Extract structured train booking fields from a free-form user message.

    Two-pass approach:
        1. Rule-based: scan the message for known station names/codes and
           use word-boundary heuristics ("to", "se") to assign from/to.
        2. AI-based: send the full message to Groq for everything else
           (train number, date, class, validation).
        3. Override the AI's from/to with rule-based results when found,
           since the local dict is more reliable for common cities.

    Args:
        user_message (str): Raw Telegram message, e.g.
                            "Alert for 12951 mumbai to delhi 3A on 15-Jun-2026"

    Returns:
        dict: {
            "train_number": str or None,
            "from_station": str,
            "to_station": str,
            "date": str,           # DD-Mon-YYYY
            "travel_class": str,   # SL / 3A / 2A / 1A / CC / EC
            "is_valid": bool,
            "missing": list,
            "suggestion": str
        }
        On any error returns {"is_valid": False, "suggestion": "<help text>"}
    """

    # ------------------------------------------------------------------
    # Pass 1: Rule-based station extraction
    # ------------------------------------------------------------------
    msg_lower = user_message.lower()
    rule_from = None
    rule_to = None

    # Collect all station mentions in the order they appear in the message
    found_stations = []
    # Check multi-word keys first (e.g. "new delhi") before single words
    sorted_keys = sorted(STATION_CODES.keys(), key=len, reverse=True)

    for key in sorted_keys:
        if key in msg_lower:
            code = STATION_CODES[key]
            # Record (position, code) — use first occurrence
            pos = msg_lower.index(key)
            found_stations.append((pos, code, key))

    # Remove duplicate codes (keep earliest occurrence)
    seen_codes = set()
    unique_stations = []
    for pos, code, key in sorted(found_stations, key=lambda x: x[0]):
        if code not in seen_codes:
            seen_codes.add(code)
            unique_stations.append((pos, code, key))

    # Try to assign from/to using "to" / "se" directional keywords
    if len(unique_stations) >= 2:
        # Check if "to" or "se" appears between two stations
        for i in range(len(unique_stations) - 1):
            pos_a, code_a, key_a = unique_stations[i]
            pos_b, code_b, key_b = unique_stations[i + 1]
            between = msg_lower[pos_a + len(key_a): pos_b]
            if " to " in between or " se " in between:
                rule_from = code_a
                rule_to = code_b
                break

        # Fallback: just use order of appearance
        if not rule_from:
            rule_from = unique_stations[0][1]
            rule_to = unique_stations[1][1]

    elif len(unique_stations) == 1:
        # Only one station found; leave the other for AI to figure out
        rule_from = unique_stations[0][1]

    # ------------------------------------------------------------------
    # Pass 2: Groq extraction
    # ------------------------------------------------------------------
    prompt = (
        f'Extract train booking details from the following message.\n'
        f'Message: "{user_message}"\n\n'
        f'CRITICAL rules:\n'
        f'1. Use Indian Railways station codes: BCT, NDLS, BRC, RAJ, ST, SBC, MAS, KOAA, ADI, BHAV, JAM, PBR, VRL, GIMB, BHUJ\n'
        f'2. If the user provides a 5-digit train number, extract it as train_number\n'
        f'3. If NO train number is given, set train_number to null\n'
        f'4. Date must be in DD-Mon-YYYY format (e.g. 15-Jun-2026)\n'
        f'5. travel_class must be one of: SL, 3A, 2A, 1A, CC, EC\n'
        f'6. Set is_valid to false and list missing fields in "missing" if any required field is absent\n'
        f'7. Provide a helpful suggestion if is_valid is false\n\n'
        f'Return ONLY valid JSON with no explanation:\n'
        f'{{"train_number": "xxxxx" or null, "from_station": "XXX", '
        f'"to_station": "XXX", "date": "DD-Mon-YYYY", "travel_class": "XX", '
        f'"is_valid": true or false, "missing": [], "suggestion": "message"}}'
    )

    try:
        raw = _chat(prompt, max_tokens=400)
        cleaned = _clean_json_response(raw)
        result = json.loads(cleaned)

        # ------------------------------------------------------------------
        # Pass 3: Override AI stations with rule-based results (more reliable)
        # ------------------------------------------------------------------
        if rule_from:
            result["from_station"] = rule_from
        if rule_to:
            result["to_station"] = rule_to

        # Ensure keys exist with safe defaults
        result.setdefault("train_number", None)
        result.setdefault("from_station", "")
        result.setdefault("to_station", "")
        result.setdefault("date", "")
        result.setdefault("travel_class", "SL")
        result.setdefault("is_valid", False)
        result.setdefault("missing", [])
        result.setdefault("suggestion", "")

        return result

    except Exception as e:
        print(f"[ai_helper] parse_natural_language error: {e}")
        return {
            "is_valid": False,
            "suggestion": (
                "Parse error. Please try the format:\n"
                "<code>TRAIN_NO FROM TO DATE CLASS</code>\n"
                "e.g. <code>12951 BCT NDLS 15-Jun-2026 3A</code>"
            ),
        }


# ---------------------------------------------------------------------------
# 3. parse_stop_command
# ---------------------------------------------------------------------------

def parse_stop_command(user_message: str, active_watches: list) -> dict:
    """
    Identify which active watch the user wants to cancel.

    Sends the user message along with a compact list of active watches to
    Groq so it can match by train number, route, or any other clue.

    Args:
        user_message   (str):  e.g. "stop the 12951 alert"
        active_watches (list): Watch dicts from storage.get_watches_for_user()

    Returns:
        dict: {"watch_id": "<uuid>" | None, "reason": "<explanation>"}
              watch_id is None when no match is found or on error.
    """
    # Build a compact representation so we don't bloat the prompt
    context = [
        {"id": w.get("id"), "train": w.get("train_number"),
         "from": w.get("from_station"), "to": w.get("to_station"),
         "date": w.get("date"), "class": w.get("travel_class")}
        for w in active_watches
    ]

    prompt = (
        f'A user wants to stop a train seat watch alert.\n'
        f'User message: "{user_message}"\n'
        f'Active watches: {json.dumps(context)}\n\n'
        f'Identify which watch the user is referring to.\n'
        f'Return ONLY valid JSON with no explanation:\n'
        f'{{"watch_id": "<id from the list above>" or null, '
        f'"reason": "brief explanation of why you matched or did not match"}}'
    )

    try:
        raw = _chat(prompt, max_tokens=150)
        cleaned = _clean_json_response(raw)
        result = json.loads(cleaned)

        result.setdefault("watch_id", None)
        result.setdefault("reason", "")
        return result

    except Exception as e:
        print(f"[ai_helper] parse_stop_command error: {e}")
        return {"watch_id": None, "reason": f"Parse error: {e}"}


# ---------------------------------------------------------------------------
# 4. plan_journey
# ---------------------------------------------------------------------------

def plan_journey(user_message: str) -> str:
    """
    Provide a short, friendly journey planning suggestion in Hinglish.

    Args:
        user_message (str): e.g. "Best train from Mumbai to Delhi in AC?"

    Returns:
        str: 3-line Hinglish suggestion from the model, or an error string.
    """
    prompt = (
        f'You are an expert on Indian Railways and train travel in India.\n'
        f'A user says: "{user_message}"\n\n'
        f'Suggest the best train and class for their journey in exactly 3 short lines.\n'
        f'Reply in Hinglish (mix of Hindi and English). Be helpful and friendly.'
    )

    try:
        return _chat(prompt, max_tokens=200)

    except Exception as e:
        print(f"[ai_helper] plan_journey error: {e}")
        return (
            "Sorry yaar, abhi suggestion dene mein problem ho rahi hai. "
            "Please thodi der baad try karo! 🙏"
        )


# ---------------------------------------------------------------------------
# 5. get_alternative_trains
# ---------------------------------------------------------------------------

def get_alternative_trains(
    from_station: str,
    to_station: str,
    date: str,
    travel_class: str,
    waitlist_status: str,
) -> str:
    """
    Suggest alternative trains when the user's current train is waitlisted.

    Args:
        from_station    (str): e.g. "BCT"
        to_station      (str): e.g. "NDLS"
        date            (str): e.g. "15-Jun-2026"
        travel_class    (str): e.g. "3A"
        waitlist_status (str): e.g. "WL42" or "REGRET"

    Returns:
        str: 3-line Hinglish reply with alternative train suggestions,
             or an error string.
    """
    prompt = (
        f'You are an expert on Indian Railways.\n'
        f'A user is travelling from {from_station} to {to_station} '
        f'on {date} in {travel_class} class.\n'
        f'Their current train status is: {waitlist_status}.\n\n'
        f'Suggest 2 good alternative trains for the same route and date.\n'
        f'Reply in exactly 3 short lines in Hinglish. '
        f'Include train names or numbers if possible.'
    )

    try:
        return _chat(prompt, max_tokens=300)

    except Exception as e:
        print(f"[ai_helper] get_alternative_trains error: {e}")
        return (
            "Sorry yaar, alternatives dhundhne mein problem aa gayi. "
            "IRCTC app pe manually check karo please! 🙏"
        )
