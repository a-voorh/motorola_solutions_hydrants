"""Transcript-to-fact extraction (the deterministic first implementation).

The public interface here is the seam a future AI extractor can replace::

    extract_flow(transcript) -> (flow_l_min | None, flow_explicitly_stated)
    detect_update(message)   -> UpdateFacts

No Streamlit, routing, or solver dependencies.
"""

from __future__ import annotations

import re

from domain import UpdateFacts

# L/min: "4000 L/min", "4,000 l/min", "2.5 litres per minute",
# "4000 liters per minute", "4000 lpm", "4000 LPM".
_FLOW_RE = re.compile(
    r"(?P<num>\d[\d,\.]*)\s*"
    r"(?:l|L)(?:itres?|iters?)?\s*(?:/|per)?\s*(?:min|minute)?"
    r"|\b(?P<num2>\d[\d,\.]*)\s*lpm\b",
    re.IGNORECASE,
)

# Word-form numbers: "four thousand", "twenty three", "two point five".
_UNITS = {
    "zero": 0, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5,
    "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10, "eleven": 11,
    "twelve": 12, "thirteen": 13, "fourteen": 14, "fifteen": 15,
    "sixteen": 16, "seventeen": 17, "eighteen": 18, "nineteen": 19,
}
_TENS = {
    "twenty": 20, "thirty": 30, "forty": 40, "fifty": 50,
    "sixty": 60, "seventy": 70, "eighty": 80, "ninety": 90,
}
_DIGITS = {k: v for k, v in _UNITS.items() if v < 10}

_NUMBER_WORD = "|".join(sorted(set(_UNITS) | set(_TENS) | {"hundred", "thousand", "point", "and"}))

_WORD_FLOW_RE = re.compile(
    rf"(?P<words>(?:\b(?:{_NUMBER_WORD})\b[\s-]*)+)\s*"
    r"(?:l|L)(?:itres?|iters?)?\s*(?:/|per)?\s*(?:min|minute)?",
    re.IGNORECASE,
)

# Hydrant IDs look like H0479 (case-insensitive).
_HYDRANT_RE = re.compile(r"\bH\d+\b", re.IGNORECASE)

# A hydrant failure is signalled by an ID plus one of these phrases.
_FAILURE_KW_RE = re.compile(
    r"unavailable|failed|out\s+of\s+service|not\s+working|broken", re.IGNORECASE
)

# A flow is treated as a NEW total only when one of these phrases is present.
_DEMAND_PHRASE_RE = re.compile(
    r"increase\s+demand\s+to|raise\s+demand\s+to|update\s+demand\s+to"
    r"|now\s+require\w*|new\s+required\s+flow\s+is",
    re.IGNORECASE,
)


def _words_to_number(text):
    """Convert a word-form number (cardinals + 'point' decimals) to a float."""
    tokens = re.findall(r"[a-z]+", text.lower())
    total = 0
    current = 0
    i = 0
    while i < len(tokens):
        tok = tokens[i]
        if tok == "point":
            frac = 0.0
            scale = 0.1
            i += 1
            while i < len(tokens) and tokens[i] in _DIGITS:
                frac += _DIGITS[tokens[i]] * scale
                scale /= 10.0
                i += 1
            return float(total + current + frac)
        if tok in _UNITS:
            current += _UNITS[tok]
        elif tok in _TENS:
            current += _TENS[tok]
        elif tok == "hundred":
            current = (current or 1) * 100
        elif tok == "thousand":
            total += (current or 1) * 1000
            current = 0
        # "and" (and any other filler) is ignored
        i += 1
    return float(total + current)


def extract_flow(transcript):
    """Rule-based flow extractor -> (required_flow_l_min, flow_explicitly_stated).

    Handles digit forms ("4000", "2.5") and word forms ("four thousand",
    "two point five"), both in L/min units.
    """
    if not transcript:
        return None, False
    m = _FLOW_RE.search(transcript)
    if m:
        raw = m.group("num") or m.group("num2")
        return float(raw.replace(",", "")), True
    m = _WORD_FLOW_RE.search(transcript)
    if m:
        words = m.group("words")
        tokens = re.findall(r"[a-z]+", words.lower())
        if any(t in _UNITS or t in _TENS or t in ("hundred", "thousand") for t in tokens):
            return _words_to_number(words), True
    return None, False


# Latitude/longitude pairs, in several common shapes:
#   * "55.664178, 12.607972"  (comma)
#   * "55.664178; 12.607972"  (semicolon)
#   * "55.664178 12.607972"   (whitespace)
#   * "lat 55.664178 lon 12.607972" / "lat: 55.66, lon: 12.61" (labels)
# Requiring a decimal point on both coordinates avoids matching flow numbers
# like "4000 L/min".
_COORD_NUM = r"-?\d{1,3}\.\d+"
_LOCATION_RE = re.compile(
    rf"(?:lat\w*\s*[:=]?\s*(?P<lat1>{_COORD_NUM})\s*[,;]?\s*(?:lon\w*\s*[:=]?\s*)?(?P<lon1>{_COORD_NUM}))"
    rf"|(?P<lat2>{_COORD_NUM})\s*[,;]\s*(?P<lon2>{_COORD_NUM})"
    rf"|(?P<lat3>{_COORD_NUM})\s+(?P<lon3>{_COORD_NUM})",
    re.IGNORECASE,
)


def extract_location(text):
    """Extract an incident location (latitude, longitude) from ``text``.

    Returns ``(lat, lon)`` if a plausible decimal-degree pair is found, else
    ``None``. Coordinates are range-checked (-90..90 latitude, -180..180
    longitude).
    """
    if not text:
        return None
    for m in _LOCATION_RE.finditer(text):
        lat = m.group("lat1") or m.group("lat2") or m.group("lat3")
        lon = m.group("lon1") or m.group("lon2") or m.group("lon3")
        if lat is None or lon is None:
            continue
        lat = float(lat)
        lon = float(lon)
        if -90.0 <= lat <= 90.0 and -180.0 <= lon <= 180.0:
            return lat, lon
    return None


def _normalize_hydrant(raw):
    digits = re.sub(r"\D", "", raw)
    return f"H{int(digits):04d}" if digits else None


def detect_update(message):
    """Parse one radio message into :class:`UpdateFacts`.

    A single message may carry both a failure and a new demand.
    """
    text = message or ""
    flow, stated = extract_flow(text)
    m = _HYDRANT_RE.search(text)
    hydrant = _normalize_hydrant(m.group(0)) if m else None
    failure = hydrant is not None and bool(_FAILURE_KW_RE.search(text))
    demand_phrase = bool(_DEMAND_PHRASE_RE.search(text))
    return UpdateFacts(
        flow=flow,
        stated=stated,
        demand_phrase=demand_phrase,
        hydrant=hydrant,
        failure=failure,
    )
