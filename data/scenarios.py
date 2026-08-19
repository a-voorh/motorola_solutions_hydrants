"""Scripted talk-group scenario loading and validation.

Scenarios are simple JSON files under ``scenarios/`` (see the explicit schema
below). ``load_scenario`` reads and validates one file into :class:`Scenario` /
:class:`ScenarioMessage` dataclasses; no scenario content is hard-coded here.

JSON schema (per file)::

    {
       "id": "1",                                # required, non-empty
      "title": "Standard residential fire",     # required, non-empty
       "messages": [                             # required, non-empty list; first starts incident
        {
          "timestamp": "2026-08-16T09:14:32",   # required, ISO-8601 string
          "speaker": "Dispatch",                # required, non-empty
          "text": "Unit 2, confirm status.",    # required, non-empty
          "offset_seconds": 0,                  # optional number (default 0)
           "location": null,                     # first message must be [lat, lon]
          "kind": "chatter"                     # optional string or null
        }
      ]
    }
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from domain import Scenario, ScenarioMessage
from extraction import extract_flow

SCENARIOS_DIR = Path(__file__).resolve().parent.parent / "scenarios"

_ISO_TIMESTAMP_RE = re.compile(
    r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})?$"
)


def _validate_scenario(raw, name):
    if not isinstance(raw, dict):
        raise ValueError(f"Scenario '{name}': file must be a JSON object.")
    if not str(raw.get("id", "")).strip():
        raise ValueError(f"Scenario '{name}': missing or empty 'id'.")
    if not str(raw.get("title", "")).strip():
        raise ValueError(f"Scenario '{name}': missing or empty 'title'.")
    messages = raw.get("messages")
    if not isinstance(messages, list) or not messages:
        raise ValueError(f"Scenario '{name}': 'messages' must be a non-empty list.")
    for i, msg in enumerate(messages):
        _validate_message(msg, name, i)

    first = messages[0]
    _flow, stated = extract_flow(first["text"])
    if not stated:
        raise ValueError(
            f"Scenario '{name}': first message must state the required flow."
        )
    if first.get("location") is None:
        raise ValueError(
            f"Scenario '{name}': first message must include an incident location."
        )
    return raw


def _validate_message(msg, name, i):
    where = f"Scenario '{name}' message {i}"
    if not isinstance(msg, dict):
        raise ValueError(f"{where}: must be a JSON object.")
    for field in ("timestamp", "speaker", "text"):
        if not str(msg.get(field, "")).strip():
            raise ValueError(f"{where}: missing or empty '{field}'.")
    if not _ISO_TIMESTAMP_RE.match(str(msg["timestamp"])):
        raise ValueError(f"{where}: 'timestamp' must be ISO-8601.")
    if "offset_seconds" in msg and not isinstance(msg["offset_seconds"], (int, float)):
        raise ValueError(f"{where}: 'offset_seconds' must be a number.")
    if msg.get("location") is not None:
        loc = msg["location"]
        if (not isinstance(loc, (list, tuple)) or len(loc) != 2
                or not all(isinstance(v, (int, float)) for v in loc)):
            raise ValueError(f"{where}: 'location' must be null or [lat, lon].")


def _message_from_json(msg):
    location = msg.get("location")
    return ScenarioMessage(
        timestamp=str(msg["timestamp"]),
        speaker=str(msg["speaker"]),
        text=str(msg["text"]),
        offset_seconds=float(msg.get("offset_seconds", 0.0)),
        location=tuple(float(v) for v in location) if location is not None else None,
        kind=str(msg["kind"]) if msg.get("kind") is not None else None,
    )


def load_scenario(name="1"):
    """Load and validate ``scenarios/<name>.json`` into a :class:`Scenario`."""
    path = SCENARIOS_DIR / f"{name}.json"
    if not path.is_file():
        raise FileNotFoundError(f"Scenario '{name}' not found at {path}.")
    with open(path, encoding="utf-8") as f:
        raw = json.load(f)
    _validate_scenario(raw, name)
    return Scenario(
        id=str(raw["id"]),
        title=str(raw["title"]),
        messages=[_message_from_json(m) for m in raw["messages"]],
    )


def available_scenarios():
    """Return the names of all scenario files in ``scenarios/``."""
    return sorted(p.stem for p in SCENARIOS_DIR.glob("*.json"))


def default_scenario():
    """Return the default demo scenario."""
    return load_scenario("1")
