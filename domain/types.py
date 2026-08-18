"""Typed shared data structures for the hydrant recommender.

Everything here is pure data (stdlib ``dataclasses`` / ``typing`` only) so it
can be imported anywhere without pulling in pandas, Streamlit, OSMnx, or SciPy.

Extension points for future work are prepared here:
  * ``IncidentRequest``  -- input to the high-level ``workflow.analyse_incident``.
  * ``UpdateFacts``      -- what ``extraction.detect_update`` returns.
  * ``AvailabilityEvent``-- a hydrant failure/restoration signal.
  * ``DispatcherPreference`` -- future dispatcher settings (defined, not yet wired
     through the solver).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, TypedDict

from domain.constants import (
    CARRIED_PIECES,
    DEFAULT_GAMMA,
    DEFAULT_MAX_LINES_PER_HYDRANT,
    DEFAULT_Q,
    DEFAULT_V,
    HOSE_PIECE_M,
)


@dataclass(frozen=True)
class Params:
    """Model parameters shared across A/B/C-soft/C-hard (where applicable).

    ``gamma`` is an experimental hydraulic calibration parameter (NOT physically
    calibrated); ``max_lines_per_hydrant`` is the prototype-only configuration
    bound on parallel hose lines (not a physical limit).
    """

    v: float = DEFAULT_V
    q: float = DEFAULT_Q
    gamma: float = DEFAULT_GAMMA
    hose_piece_m: float = HOSE_PIECE_M
    carried_pieces: int = CARRIED_PIECES
    max_lines_per_hydrant: int = DEFAULT_MAX_LINES_PER_HYDRANT


@dataclass(frozen=True)
class IncidentRequest:
    """One incident to analyse: the raw transcript plus its location."""

    transcript: str
    location: tuple[float, float]  # (latitude, longitude)
    planning_reserve_percent: float


@dataclass(frozen=True)
class AvailabilityEvent:
    """A hydrant availability signal (e.g. a failure reported over radio)."""

    hydrant: str | None
    failure: bool


@dataclass(frozen=True)
class UpdateFacts:
    """Facts parsed from one radio message.

    A single message may carry both a failure and a new demand.
    """

    flow: float | None
    stated: bool
    demand_phrase: bool
    hydrant: str | None
    failure: bool


@dataclass(frozen=True)
class DispatcherPreference:
    """Future dispatcher settings (prepared interface; not yet wired in)."""

    model: str = "B"
    distance_method: str = "gis"
    planning_reserve_percent: float = 50.0


@dataclass
class HydrantLine:
    """One selected hydrant's contribution to a plan.

    ``lines`` is the number of parallel hose lines selected (1 for A/B);
    ``hose_pieces`` is the whole pieces required for ONE line; ``hose_pieces_total``
    is the total across all selected lines (``lines * hose_pieces``).
    """

    hydrant: str
    latitude: float
    longitude: float
    distance_m: float
    nominal_capacity: float
    effective_capacity: float  # usable capacity under the current model/gamma
    hose_pieces: int | None  # pieces for ONE line; None only when no hydrants selected
    lines: int = 1
    hose_pieces_total: int | None = None  # lines * hose_pieces


@dataclass
class ModelResult:
    """Common, readable output of any model."""

    model: str
    demand: float
    demand_served: float
    unmet_demand: float
    demand_met: bool
    total_nominal_capacity: float
    total_effective_capacity: float
    deployment_time: float
    hose_pieces_used: int | None  # total pieces; None -> no candidates
    carried_pieces_used: int | None  # pieces from carried stock; None -> no candidates
    extra_hose_pieces: int | None  # reinforcement pieces; None -> no candidates or C-hard
    radius: float | None
    distance_method: str
    selected: list = field(default_factory=list)
    recommendation: str = ""


@dataclass(frozen=True)
class ScenarioMessage:
    """One scripted talk-group message for demo playback.

    ``text`` is fed to ``extraction.detect_update``; the remaining fields carry
    the metadata a future classifier or playback UI needs. ``kind`` is an
    optional, classification-ready hint (e.g. "chatter" / "request" / "update").
    """

    timestamp: str  # realistic ISO-8601, e.g. "2026-08-16T09:14:32"
    speaker: str    # role or call-sign
    text: str
    offset_seconds: float = 0.0          # simulated-time offset from scenario start
    location: tuple[float, float] | None = None  # optional (latitude, longitude)
    kind: str | None = None


@dataclass(frozen=True)
class Scenario:
    """A deterministic, scripted talk-group scenario."""

    id: str
    title: str
    messages: list[ScenarioMessage]


class HydrantCandidate(TypedDict):
    """Shape of one candidate row (used internally as a pandas DataFrame)."""

    Distance_m: float
    Capacity_L_min: float


class FlowStatus(TypedDict):
    """Separated flow fields and met/shortfall status for a plan."""

    stated_minimum_flow_l_min: float
    planning_reserve_percent: float
    planning_reserve_l_min: float
    planning_target_flow_l_min: float
    delivered_flow_l_min: float
    minimum_met: bool
    target_met: bool
    planning_target_shortfall_l_min: float
    operational_shortfall_l_min: float


class Plan(TypedDict, total=False):
    """The workflow plan dict. Keys are optional (``total=False``) because the
    plan carries a different subset depending on whether a flow was stated."""

    location: tuple[float, float]
    effective_demand: float | None
    model: str
    result: Any  # ModelResult | None
    selected: dict  # {hydrant: {"capacity": float, "distance": float}}
    unavailable: list
    declined: list
    objective: float | None
    radius: float | None
    insufficient: bool
    distance_method: str
    committed_pieces: int | None
    params: Params
    candidates: Any  # pandas DataFrame
    transcript: str
    stated_minimum_flow_l_min: float
    planning_reserve_percent: float
    planning_reserve_l_min: float
    planning_target_flow_l_min: float
    delivered_flow_l_min: float
    minimum_met: bool
    target_met: bool
    planning_target_shortfall_l_min: float
    operational_shortfall_l_min: float
