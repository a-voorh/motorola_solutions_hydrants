"""Pytest tests for the Streamlit hydrant demo (propose -> commit workspace UI)."""

import pytest
from streamlit.testing.v1 import AppTest


@pytest.fixture()
def app():
    at = AppTest.from_file("app.py", default_timeout=120)
    at.run()
    assert not at.exception
    # The shared workspace is identical in both modes; drive it from Scripts so
    # the technical extras (scenario demo) are also exercised.
    at.radio(key="app_mode").set_value("Scripts")
    at.run()
    assert not at.exception
    return at


def _set_location(app, lat=55.664178, lon=12.607972, method="gis"):
    app.number_input(key="lat").set_value(lat)
    app.number_input(key="lon").set_value(lon)
    app.radio(key="distance_method").set_value(method)
    app.run()
    assert not app.exception


def _send(app, message):
    app.text_input(key="live_dialog_input").set_value(message)
    app.button(key="live_send_btn").click()
    app.run()
    assert not app.exception


def _send_and_accept(app, message):
    _send(app, message)
    app.button(key="live_accept_btn").click()
    app.run()
    assert not app.exception


def _initial_analysis(app, message="We need 800 L/min", method="gis"):
    _set_location(app, method=method)
    _send_and_accept(app, message)


# --- mode toggle ------------------------------------------------------------

def test_default_mode_is_live_dialog():
    at = AppTest.from_file("app.py", default_timeout=120)
    at.run()
    assert not at.exception
    assert at.radio(key="app_mode").value == "Live Dialog"


def test_mode_switch_clears_dialog_keeps_plan():
    at = AppTest.from_file("app.py", default_timeout=120)
    at.run()

    _set_location(at)
    _send_and_accept(at, "We need 800 L/min")
    assert "live_messages" in at.session_state and at.session_state["live_messages"]

    at.radio(key="app_mode").set_value("Scripts")
    at.run()
    assert "live_messages" not in at.session_state or at.session_state["live_messages"] == []
    assert at.session_state["plan"] is not None


def test_page_switch_clears_dialog_keeps_plan():
    at = AppTest.from_file("app.py", default_timeout=120)
    at.run()

    _set_location(at)
    _send_and_accept(at, "We need 800 L/min")
    assert "live_messages" in at.session_state and at.session_state["live_messages"]

    at.switch_page("visualization_page.py")
    at.run()
    assert "live_messages" not in at.session_state or at.session_state["live_messages"] == []
    assert at.session_state["plan"] is not None


# --- propose -> commit flow -------------------------------------------------

def test_live_dialog_accept_commits_optimizer_recommendation():
    at = AppTest.from_file("app.py", default_timeout=120)
    at.run()
    assert not at.exception

    _set_location(at)
    _send(at, "We need 800 L/min")

    assert at.session_state["awaiting_decision"] is True
    assert at.session_state["proposed_plan"] is not None

    at.button(key="live_accept_btn").click()
    at.run()

    assert at.session_state["plan"] is not None
    assert at.session_state["plan"]["stated_minimum_flow_l_min"] == pytest.approx(800.0)
    assert at.session_state["plan"]["selected"]
    assert at.session_state["awaiting_decision"] is False


def test_decline_enters_curation_and_recompute_excludes():
    at = AppTest.from_file("app.py", default_timeout=120)
    at.run()

    _set_location(at)
    _send_and_accept(at, "We need 800 L/min")
    committed = at.session_state["plan"]

    _send(at, "Increase demand to 5000 L/min")
    assert at.session_state["awaiting_decision"] is True

    at.button(key="live_decline_btn").click()
    at.run()

    # Entered curation; the committed plan is untouched and no auto-decision runs.
    assert at.session_state["curating"] is True
    assert at.session_state["plan"] == committed
    assert at.session_state["awaiting_decision"] is False
    declined = at.session_state["declined_proposal"]
    assert declined is not None

    committed_ids = set(committed["selected"].keys())
    added = [h for h in declined["selected"].keys() if h not in committed_ids]
    assert added  # demand increase must have proposed additional hydrants

    # Exclude the added hydrants, then recompute.
    at.multiselect(key="exclude_selection").set_value(added)
    at.button(key="curate_recompute_btn").click()
    at.run()

    new_proposed = at.session_state["proposed_plan"]
    assert new_proposed is not None
    assert set(new_proposed["selected"]).isdisjoint(set(added))
    for h in committed_ids:
        assert h in new_proposed["selected"]

    # Accept the new recommendation.
    at.button(key="accept_new_btn").click()
    at.run()
    assert at.session_state["curating"] is False
    assert at.session_state["awaiting_decision"] is False
    assert set(at.session_state["plan"]["selected"]).isdisjoint(set(added))
    for h in committed_ids:
        assert h in at.session_state["plan"]["selected"]


def test_live_dialog_failure_accept_shares_state_with_scripts():
    at = AppTest.from_file("app.py", default_timeout=120)
    at.run()

    _set_location(at)
    _send_and_accept(at, "We need 800 L/min")

    failed = next(iter(at.session_state["plan"]["selected"]))

    _send(at, f"{failed} is out of service")
    assert at.session_state["awaiting_decision"] is True
    at.button(key="live_accept_btn").click()
    at.run()

    assert failed in at.session_state["plan"]["unavailable"]
    assert failed not in at.session_state["plan"]["selected"]

    # Shared state is visible after switching to Scripts.
    at.radio(key="app_mode").set_value("Scripts")
    at.run()
    assert not at.exception
    assert failed in at.session_state["plan"]["unavailable"]


# --- core analysis ----------------------------------------------------------

def test_initial_analysis_sets_plan(app):
    _initial_analysis(app)
    plan = app.session_state["plan"]
    assert plan["effective_demand"] == 800 * 1.5
    assert plan["selected"]
    assert plan["unavailable"] == []
    assert plan["objective"] is not None


def test_demand_increase(app):
    _initial_analysis(app)
    before = app.session_state["plan"]

    _send_and_accept(app, "Increase demand to 5000 L/min")

    after = app.session_state["plan"]
    assert after["effective_demand"] == 5000 * 1.5
    for h in before["selected"]:
        assert h in after["selected"]
    assert after["effective_demand"] > before["effective_demand"]


def test_hydrant_failure(app):
    _initial_analysis(app)
    plan = app.session_state["plan"]
    failed = next(iter(plan["selected"]))

    _send_and_accept(app, f"{failed} is out of service")

    after = app.session_state["plan"]
    assert failed in after["unavailable"]
    assert failed not in after["selected"]
    for h in plan["selected"]:
        if h != failed:
            assert h in after["selected"]


def test_unrecognized_update_does_not_change_state(app):
    _initial_analysis(app)
    before = app.session_state["plan"]

    _send(app, "The weather is sunny today")
    assert app.session_state["awaiting_decision"] is False
    assert app.session_state["plan"] == before


def test_initial_analysis_word_form(app):
    _initial_analysis(app, "We need eight hundred L/min")
    plan = app.session_state["plan"]
    assert plan["effective_demand"] == 800 * 1.5
    assert plan["selected"]


def test_demand_increase_word_form(app):
    _initial_analysis(app)
    _send_and_accept(app, "Increase demand to five thousand L/min")
    assert app.session_state["plan"]["effective_demand"] == 5000 * 1.5


def test_decimal_word_form(app):
    _initial_analysis(app, "We need two point five L/min")
    assert app.session_state["plan"]["effective_demand"] == 2.5 * 1.5


def test_objective_includes_connection_time(app):
    app.selectbox(key="model").set_value("B")
    app.run()
    _initial_analysis(app)
    plan = app.session_state["plan"]
    expected = sum(s.distance_m / 5.0 + 10.0 for s in plan["result"].selected)
    assert abs(plan["objective"] - expected) < 1e-6


def test_selected_capacity_covers_demand(app):
    _initial_analysis(app)
    plan = app.session_state["plan"]
    nominal = sum(info["capacity"] for info in plan["selected"].values())
    assert nominal >= plan["effective_demand"]


# --- distance methods -------------------------------------------------------

def test_network_is_default_radio(app):
    assert app.radio(key="distance_method").value == "network"


def test_network_distance_used_by_default(app):
    app.number_input(key="lat").set_value(55.664178)
    app.number_input(key="lon").set_value(12.607972)
    app.run()
    _send_and_accept(app, "We need 800 L/min")
    plan = app.session_state["plan"]
    assert plan["distance_method"] == "network"
    assert plan["selected"]


def test_gis_distance_method(app):
    _initial_analysis(app, method="gis")
    assert app.session_state["plan"]["distance_method"] == "gis"
    assert app.session_state["plan"]["selected"]


def test_manhattan_distance_method(app):
    _initial_analysis(app, method="manhattan")
    assert app.session_state["plan"]["distance_method"] == "manhattan"
    assert app.session_state["plan"]["selected"]


# --- dispatcher controls ----------------------------------------------------

def test_demand_buffer_control(app):
    app.number_input(key="planning_reserve").set_value(25.0)
    app.run()
    _initial_analysis(app)
    plan = app.session_state["plan"]
    assert plan["planning_reserve_percent"] == pytest.approx(25.0)
    assert plan["effective_demand"] == pytest.approx(800 * 1.25)


def test_location_extraction_fills_config():
    at = AppTest.from_file("app.py", default_timeout=120)
    at.run()
    at.radio(key="distance_method").set_value("gis")
    at.run()

    _send(at, "We need 800 L/min at 55.664178, 12.607972")
    at.button(key="live_accept_btn").click()
    at.run()

    plan = at.session_state["plan"]
    assert plan["location"] == (55.664178, 12.607972)
    assert at.session_state["lat"] == pytest.approx(55.664178)
    assert at.session_state["lon"] == pytest.approx(12.607972)


# --- scenario demo (Scripts-only, with pause for Accept/Decline) ------------

def test_scenario_load_and_step_through(app):
    app.radio(key="distance_method").set_value("gis")
    app.run()

    app.button(key="load_scenario_btn").click()
    app.run()
    assert not app.exception
    assert app.session_state["playback"]["index"] == 0
    assert app.session_state["plan"] is None

    # Step 1: initial request -> proposal.
    app.button(key="next_msg_btn").click()
    app.run()
    assert app.session_state["playback"]["index"] == 1
    assert app.session_state["awaiting_decision"] is True
    assert app.session_state["proposed_plan"] is not None

    # Accept the initial recommendation, then step through the live scenario.
    app.button(key="live_accept_btn").click()
    app.run()
    assert app.session_state["plan"] is not None
    assert app.session_state["awaiting_decision"] is False

    # Messages 2-3: chatter.
    app.button(key="next_msg_btn").click()
    app.run()
    assert app.session_state["playback"]["index"] == 2
    assert app.session_state["awaiting_decision"] is False

    app.button(key="next_msg_btn").click()
    app.run()
    assert app.session_state["playback"]["index"] == 3
    assert app.session_state["awaiting_decision"] is False

    # Message 4: relevant hydrant failure.
    app.button(key="next_msg_btn").click()
    app.run()
    assert app.session_state["playback"]["index"] == 4
    assert app.session_state["awaiting_decision"] is True

    app.button(key="live_accept_btn").click(); app.run()
    assert "H0479" in app.session_state["plan"]["unavailable"]

    # Message 5: irrelevant Force Majeure failure.
    app.button(key="next_msg_btn").click()
    app.run()
    assert app.session_state["playback"]["index"] == 5
    assert app.session_state["awaiting_decision"] is True
    app.button(key="live_accept_btn").click(); app.run()
    assert "H4643" in app.session_state["plan"]["unavailable"]

    # Message 6: absolute demand update.
    app.button(key="next_msg_btn").click(); app.run()
    assert app.session_state["playback"]["index"] == 6
    assert app.session_state["awaiting_decision"] is True
    app.button(key="live_accept_btn").click(); app.run()
    assert app.session_state["plan"]["stated_minimum_flow_l_min"] == pytest.approx(900.0)

    # Message 7: chatter.
    app.button(key="next_msg_btn").click(); app.run()
    assert app.session_state["playback"]["index"] == 7
    assert app.session_state["awaiting_decision"] is False

    # Message 8: location update stages a recommendation at the new point.
    app.button(key="next_msg_btn").click(); app.run()
    assert app.session_state["playback"]["index"] == 8
    assert app.session_state["awaiting_decision"] is True
    app.button(key="live_accept_btn").click(); app.run()
    assert app.session_state["plan"]["location"] == pytest.approx(
        (55.664913, 12.608513)
    )

    # Message 9: relevant Force Majeure failure.
    app.button(key="next_msg_btn").click(); app.run()
    assert app.session_state["playback"]["index"] == 9
    assert app.session_state["awaiting_decision"] is True
    app.button(key="live_accept_btn").click(); app.run()
    assert "H0484" in app.session_state["plan"]["unavailable"]

    # Message 10: incremental demand update (900 + 300 = 1200).
    app.button(key="next_msg_btn").click(); app.run()
    assert app.session_state["playback"]["index"] == 10
    assert app.session_state["awaiting_decision"] is True
    app.button(key="live_accept_btn").click(); app.run()
    assert app.session_state["plan"]["stated_minimum_flow_l_min"] == pytest.approx(1200.0)

    # Messages 11-12: chatter, then playback is complete.
    app.button(key="next_msg_btn").click(); app.run()
    assert app.session_state["playback"]["index"] == 11
    assert app.session_state["awaiting_decision"] is False
    app.button(key="next_msg_btn").click(); app.run()
    assert app.session_state["playback"]["index"] == 12
