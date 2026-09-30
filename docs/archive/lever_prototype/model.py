"""Immutable data model + every published assumption for the Lever decision engine."""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Optional


class Mode(str, Enum):
    TRUST = "TRUST_REPORTED"              # baseline 1: reported stock is treated as true
    ALWAYS_VERIFY = "ALWAYS_VERIFY"       # baseline 2: verify before any intervention
    TATHYON = "TATHYON_LEVER"             # decision under uncertainty, VERIFY is an option
    DO_NOTHING = "DO_NOTHING"             # reference


@dataclass(frozen=True)
class Params:
    """SYNTHETIC assumptions. All are published in the evaluation report."""
    horizon_h: float = 168.0              # planning horizon: stock-out hours are counted inside it
    cover_h: float = 96.0                 # a transfer/procurement is sized to cover this many hours
    samples: int = 256                    # scenario samples per node (common random numbers)
    speed_kmh: float = 35.0               # goods vehicle
    verifier_speed_kmh: float = 30.0
    verify_base_h: float = 4.0            # time to do a physical count once on site
    expedite_gain: float = 0.4            # expediting removes this share of the remaining lead time
    procure_lead_h: float = 120.0
    procure_sigma: float = 0.4
    lead_sigma: float = 0.3               # lognormal sigma on pending-order arrival
    cons_sigma: float = 0.2               # lognormal sigma on consumption rate estimate
    reserve_h: float = 48.0               # donor must keep this many hours of its own consumption
    risk_threshold: float = 0.05          # a facility is "at risk" if P(stock-out | WAIT) >= this
    donor_conf: float = 0.8               # donor transferable = 20th percentile of (physical - reserve)
    pi_prior: tuple = (1.0, 4.0)          # Beta prior on P(record is wrong); mean 0.2. Fit on DEV seeds only.
    pi_saturation_h: float = 720.0        # error probability grows with time since last count, up to pi
    under_share: float = 0.25             # share of wrong records that are UNDER-reports
    inflate: tuple = (2.0, 3.0, 0.9)      # Beta(a,b)*scale: phantom fraction of an over-report
    under: tuple = (2.0, 6.0, 0.3)        # Beta(a,b)*scale: extra stock beyond the record
    seed: int = 0


@dataclass(frozen=True)
class NodeState:
    """What the system of record says. Nothing here is physical truth."""
    node_id: str
    reported: float                       # reported stock at observation time
    age_h: float                          # hours since that observation
    consumption_hat: float                # estimated units / hour
    checks_total: int = 0                 # past physical counts (history)
    checks_bad: int = 0                   # past counts that found a materially wrong record
    verified_ago_h: float = 1e9           # hours since the last physical count
    pending_qty: float = 0.0
    pending_eta_h: Optional[float] = None  # expected hours from now
    hub_km: float = 20.0                  # distance from the district verifier base
    prep_h: float = 4.0                   # time to prepare a dispatch if this node is a donor
    known_physical: Optional[float] = None  # set after a physical count (stock NOW)


@dataclass(frozen=True)
class Capacity:
    verifiers: int = 2
    vehicles: int = 2
    expedites: int = 1


@dataclass(frozen=True)
class Network:
    nodes: tuple                          # tuple[NodeState]
    dist_km: tuple                        # symmetric matrix, same order as nodes
    capacity: Capacity = field(default_factory=Capacity)

    def index(self, node_id: str) -> int:
        return next(i for i, n in enumerate(self.nodes) if n.node_id == node_id)

    def distance(self, a: str, b: str) -> float:
        return self.dist_km[self.index(a)][self.index(b)]

    def node(self, node_id: str) -> NodeState:
        return self.nodes[self.index(node_id)]


class Kind(str, Enum):
    WAIT = "WAIT"
    NO_ACTION = "NO_ACTION"
    EXPEDITE = "EXPEDITE"
    PROCURE = "PROCURE"
    TRANSFER = "TRANSFER"
    TRANSFER_EXPEDITE = "TRANSFER+EXPEDITE"
    VERIFY_R = "VERIFY"                   # count the recipient, then act on the truth
    VERIFY_RD = "VERIFY_BOTH"             # count recipient and one donor, then act


@dataclass(frozen=True)
class Option:
    kind: Kind
    donor: Optional[str] = None
    qty: float = 0.0
    verify_nodes: tuple = ()

    @property
    def verifier_slots(self) -> int:
        return len(self.verify_nodes)

    @property
    def vehicles(self) -> int:
        return 1 if self.kind in (Kind.TRANSFER, Kind.TRANSFER_EXPEDITE) else 0

    @property
    def expedites(self) -> int:
        return 1 if self.kind in (Kind.EXPEDITE, Kind.TRANSFER_EXPEDITE) else 0

    @property
    def is_verify(self) -> bool:
        return self.kind in (Kind.VERIFY_R, Kind.VERIFY_RD)

    def label(self) -> str:
        if self.kind in (Kind.TRANSFER, Kind.TRANSFER_EXPEDITE):
            return f"{self.kind.value} {self.qty:.0f} from {self.donor}"
        if self.kind == Kind.VERIFY_RD:
            return f"VERIFY recipient + {self.donor}, then act"
        if self.kind == Kind.VERIFY_R:
            return "VERIFY recipient, then act"
        return self.kind.value
