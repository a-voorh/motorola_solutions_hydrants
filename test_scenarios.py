"""Deterministic tests for the scripted talk-group scenario model and loader."""

import json

import pandas as pd
import pytest

from data import available_scenarios, default_scenario, load_scenario
from domain import Scenario, ScenarioMessage
from extraction import detect_update, extract_flow
from workflow import apply_scenario_message, run_scenario


def _scenario_hydrants():
    return pd.DataFrame({
        "Hydrant": ["H0479", "H0476", "H0484"],
        "Latitude": [55.664178, 55.6650, 55.6635],
        "Longitude": [12.607972, 12.6085, 12.6070],
        "Capacity_L_min": [1600.0, 1600.0, 1600.0],
        "Available": [True, True, True],
    })


def test_default_scenario_has_lively_messages_in_order():
    scenario = default_scenario()
    assert isinstance(scenario, Scenario)
    assert scenario.id == "default"
    assert len(scenario.messages) == 12

    kinds = [m.kind for m in scenario.messages]
    assert kinds == [
        "request", "chatter", "chatter", "failure", "force_majeure",
        "demand", "chatter", "location", "force_majeure", "demand",
        "chatter", "chatter",
    ]


def test_message_fields_are_populated():
    scenario = default_scenario()
    for m in scenario.messages:
        assert isinstance(m, ScenarioMessage)
        assert m.timestamp  # non-empty ISO-8601
        assert m.speaker
        assert m.text
        assert m.offset_seconds >= 0.0

    # offsets are non-decreasing
    offsets = [m.offset_seconds for m in scenario.messages]
    assert offsets == sorted(offsets)

    # The first message establishes the incident; subsequent messages carry
    # their own location only when they move the incident.
    assert scenario.messages[0].location == (55.664178, 12.607972)
    assert all(m.location is None for m in scenario.messages[1:])


def test_available_scenarios_includes_default():
    names = available_scenarios()
    assert "default" in names


def test_scenarios_available():
    names = available_scenarios()
    for name in ("default", "warehouse_fire", "apartment_block", "growing_fire"):
        assert name in names
        assert load_scenario(name).messages  # loads and validates


def test_every_script_starts_with_flow_and_location():
    for name in available_scenarios():
        first = load_scenario(name).messages[0]
        flow, stated = extract_flow(first.text)
        assert stated, name
        assert flow is not None, name
        assert first.location is not None, name


def test_request_message_is_parser_ready():
    scenario = default_scenario()
    facts = detect_update(scenario.messages[0].text)
    assert facts.stated is True
    assert facts.flow == pytest.approx(600.0)


def test_extract_location_parses_coordinates():
    from extraction import extract_location

    assert extract_location("We need 800 L/min at 55.664178, 12.607972") == (55.664178, 12.607972)
    assert extract_location("at 55.66; 12.61") == (55.66, 12.61)
    assert extract_location("55.664178 12.607972") == (55.664178, 12.607972)
    assert extract_location("lat 55.66 lon 12.61") == (55.66, 12.61)
    assert extract_location("lat: 55.664178, lon: 12.607972") == (55.664178, 12.607972)
    assert extract_location("We need 800 L/min") is None
    assert extract_location("") is None


def test_update_message_is_parser_ready():
    scenario = default_scenario()
    facts = detect_update(scenario.messages[3].text)
    assert facts.failure is True
    assert facts.hydrant == "H0479"


def test_hydrant_word_form_is_normalized():
    facts = detect_update("Hydrant 0479 is out of service")
    assert facts.failure is True
    assert facts.hydrant == "H0479"


def test_demand_update_is_absolute_not_incremental():
    absolute = detect_update("Increase demand to 5000 L/min")
    assert absolute.stated is True
    assert absolute.demand_phrase is True
    assert absolute.flow == pytest.approx(5000.0)

    increment = detect_update("Increase demand by 5000 L/min")
    assert increment.stated is True
    assert increment.demand_phrase is True
    assert increment.demand_is_incremental is True
    assert increment.flow == pytest.approx(5000.0)


def test_load_missing_scenario_raises():
    with pytest.raises(FileNotFoundError):
        load_scenario("does_not_exist")


def test_load_scenario_by_name_equals_default():
    scenario = load_scenario("default")
    assert scenario.messages[0].text == "We need 600 L/min at Amager Bio."


def test_malformed_scenario_raises(tmp_path, monkeypatch):
    import data.scenarios as ds

    (tmp_path / "bad.json").write_text(json.dumps({
        "id": "bad",
        "title": "bad",
        "messages": [
            {"timestamp": "2026-08-16T09:14:32", "speaker": "", "text": "hi"}
        ],
    }))
    monkeypatch.setattr(ds, "SCENARIOS_DIR", tmp_path)
    with pytest.raises(ValueError):
        ds.load_scenario("bad")


# --- scenario runner -------------------------------------------------------

def test_run_scenario_produces_expected_event_sequence():
    plan, event_log, comparison = run_scenario(
        default_scenario(), _scenario_hydrants(), model="B", distance_method="gis",
    )
    assert [e["kind"] for e in event_log] == [
        "initial", "chatter", "chatter", "failure", "failure", "demand",
        "chatter", "location", "failure", "demand", "chatter", "chatter",
    ]
    assert "H0479" in plan["unavailable"]
    assert "H0479" not in plan["selected"]
    assert len(comparison) == 4  # Models A/B/C-soft/C-hard


def test_step_through_messages_matches_run_scenario():
    scenario = default_scenario()
    hydrants = _scenario_hydrants()
    plan, comparison, log = None, [], []
    for message in scenario.messages:
        plan, event, comparison = apply_scenario_message(
            plan, comparison, message, hydrants, model="B", distance_method="gis",
        )
        log.append(event["kind"])
    assert log == [
        "initial", "chatter", "chatter", "failure", "failure", "demand",
        "chatter", "location", "failure", "demand", "chatter", "chatter",
    ]
    assert plan is not None
    assert "H0479" in plan["unavailable"]


def test_scenario_without_located_request_raises():
    scenario = Scenario(
        id="noloc", title="noloc",
        messages=[ScenarioMessage(
            timestamp="2026-08-16T09:14:32",
            speaker="Dispatch",
            text="We need 600 L/min",
            offset_seconds=0.0,
            location=None,
            kind="request",
        )],
    )
    with pytest.raises(ValueError):
        run_scenario(scenario, _scenario_hydrants(), model="B", distance_method="gis")


def test_chatter_event_renders_no_action():
    from ui.components import describe_event

    assert describe_event({"kind": "chatter"}) == "No action required"


def test_scenario_events_carry_timestamps():
    scenario = default_scenario()
    plan, event_log, _comparison = run_scenario(
        scenario, _scenario_hydrants(), model="B", distance_method="gis",
    )
    assert [e["timestamp"] for e in event_log] == [m.timestamp for m in scenario.messages]


def test_demand_increase_scenario_replans_from_existing_lines():
    scenario = load_scenario("growing_fire")

    # Both absolute and incremental demand updates must be recognized.
    absolute = scenario.messages[2]
    assert absolute.kind == "demand"
    facts = detect_update(absolute.text)
    assert facts.stated and facts.demand_phrase
    assert facts.flow == pytest.approx(1000.0)

    incremental = scenario.messages[4]
    incremental_facts = detect_update(incremental.text)
    assert incremental_facts.demand_is_incremental is True
    assert incremental_facts.flow == pytest.approx(500.0)

    plan, event_log, _comparison = run_scenario(
        scenario, _scenario_hydrants(), model="B", distance_method="gis",
    )
    assert [e["kind"] for e in event_log] == [
        "initial", "chatter", "demand", "chatter", "demand", "failure",
        "chatter", "demand", "failure", "chatter",
    ]
    assert plan["stated_minimum_flow_l_min"] == pytest.approx(2000.0)
    assert plan["effective_demand"] == pytest.approx(3000.0)  # 2000 * 1.5 reserve
