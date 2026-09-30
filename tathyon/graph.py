"""
Healthcare Resource Graph — Slim Typed Network Model.

Nodes:
  - Facility (delivery points: PHC, CHC, District Hospital)
  - Warehouse (distribution hubs: District Warehouse, State Warehouse)
  - Supplier (manufacturing / procurement vendors)
  - SKU (batch-aware catalog with expiration)

Edges:
  - STOCKS: claimed vs verified quantity, batches, consumption velocity
  - CAN_TRANSFER: ETA + risk multiplier, distance, cold-chain compatibility
  - SUPPLIES: supplier-to-SKU reliability, lead time, allocation share

EXACTLY THREE NAMED QUERIES:
  1. donor_reachability(recipient, sku, deadline)
  2. supplier_concentration(sku)
  3. failure_cascade(node)

Implementation uses explicit adjacency tables and traversal (no external graph DB).
Anything expressible as a relational join is implemented as one.
"""
from __future__ import annotations

import math
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from .schema import (
    FacilityType,
    Provenance,
    ResourceType,
    VerificationState,
    new_id,
    now,
    sha256,
)
from .store import EventStore
from .verify import VerificationEngine


# --------------------------------------------------------------------------
# Geographic Helpers
# --------------------------------------------------------------------------

def haversine_distance_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance in kilometers between two geographic coordinates."""
    r = 6371.0
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = (
        math.sin(dlat / 2.0) ** 2
        + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlon / 2.0) ** 2
    )
    c = 2.0 * math.atan2(math.sqrt(a), math.sqrt(1.0 - a))
    return round(r * c, 2)


# --------------------------------------------------------------------------
# Typed Nodes
# --------------------------------------------------------------------------

@dataclass
class Facility:
    """A public healthcare delivery node (PHC, CHC, District Hospital)."""
    facility_id: str
    name: str
    facility_type: FacilityType
    district: str
    state: str
    lat: float
    lon: float
    catchment_population: int = 0
    storage_capacity_m3: float = 100.0
    cold_chain_capacity_l: float = 50.0
    is_active: bool = True
    metadata: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        d = asdict(self)
        d["facility_type"] = self.facility_type.value
        return d


@dataclass
class Warehouse(Facility):
    """A public healthcare storage and distribution hub node."""
    storage_capacity_m3: float = 1200.0
    cold_chain_capacity_l: float = 800.0


@dataclass
class Supplier:
    """A pharmaceutical or medical supply vendor node."""
    supplier_id: str
    name: str
    tier: str = "STATE_EMPANELLED"              # CENTRAL_PSU | STATE_EMPANELLED | LOCAL
    default_lead_time_days: float = 7.0
    reliability_score: float = 0.95
    metadata: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class MedicineBatch:
    """A specific manufactured batch of medicine or vaccine."""
    batch_number: str
    expiry_date: str                            # ISO YYYY-MM-DD
    manufacturing_date: Optional[str] = None
    claimed_qty: float = 0.0
    usable_qty: float = 0.0
    is_expired: bool = False
    unit_price_inr: float = 0.0

    def to_dict(self) -> dict:
        return asdict(self)


SKUBatch = MedicineBatch


@dataclass
class Resource:
    """A master SKU catalog entry (batch-aware)."""
    resource_id: str
    name: str
    resource_type: ResourceType
    unit: str = "unit"                          # vial, tablet, unit
    criticality: float = 1.0                    # 1.0 (routine) to 10.0 (life-critical)
    cold_chain_required: bool = False
    default_safety_stock_days: float = 14.0
    batches: list[MedicineBatch] = field(default_factory=list)
    metadata: dict = field(default_factory=dict)

    @property
    def sku_id(self) -> str:
        return self.resource_id

    def to_dict(self) -> dict:
        d = asdict(self)
        d["resource_type"] = self.resource_type.value
        d["batches"] = [b.to_dict() if hasattr(b, "to_dict") else asdict(b) for b in self.batches]
        return d


SKU = Resource


@dataclass
class Shipment:
    """A planned or in-transit stock movement between facilities."""
    shipment_id: str
    source_facility_id: str
    dest_facility_id: str
    resource_id: str
    quantity: float
    status: str = "PENDING_APPROVAL"            # PENDING_APPROVAL | DISPATCHED | DELIVERED
    dispatched_at: Optional[str] = None
    expected_delivery_at: Optional[str] = None
    driver_phone: Optional[str] = None


# --------------------------------------------------------------------------
# Typed Edges (Adjacency Models)
# --------------------------------------------------------------------------

@dataclass
class CanTransferEdge:
    """CAN_TRANSFER edge connecting facility A -> facility B.
    
    Attributes:
      from_facility: Donor facility ID
      to_facility: Recipient facility ID
      eta_hours: Baseline road or haversine transit hours
      risk_multiplier: Access disruption multiplier (e.g. 1.5 during monsoon/disruption)
      distance_km: Transit road/geographic distance in km
      cold_chain_compatible: Whether cold-chain transport is verified along this route
    """
    from_facility: str
    to_facility: str
    eta_hours: float
    risk_multiplier: float = 1.0
    distance_km: float = 25.0
    cold_chain_compatible: bool = True

    @property
    def effective_eta_hours(self) -> float:
        return round(self.eta_hours * self.risk_multiplier, 2)


@dataclass
class SuppliesEdge:
    """SUPPLIES edge connecting Supplier -> SKU.
    
    Attributes:
      supplier_id: Supplier ID
      sku: Resource SKU ID
      reliability: Empirical or contractual supplier fulfillment rate (0.0 to 1.0)
      lead_time_days: Expected replenishment lead time in days
      share_pct: Supplier's contractual share of district volume (0.0 to 1.0)
    """
    supplier_id: str
    sku: str
    reliability: float = 0.95
    lead_time_days: float = 7.0
    share_pct: float = 1.0


@dataclass
class ResourceState:
    """STOCKS edge connecting Facility -> SKU.
    
    Distinguishes:
      - claimed_quantity (digital assertion from e-Aushadhi / CMMS)
      - usable_quantity (physically verified, unexpired stock)
    """
    facility_id: str
    resource_id: str
    claimed_quantity: float = 0.0
    observed_quantity: float = 0.0
    expired_quantity: float = 0.0
    quarantined_quantity: float = 0.0
    usable_quantity: float = 0.0
    reserved_quantity: float = 0.0
    incoming_quantity: float = 0.0
    consumption_velocity: float = 0.0           # units per day
    forecast_demand: float = 0.0
    lead_time: float = 7.0                      # replenishment days
    expiry: Optional[str] = None
    capacity: Optional[float] = None
    utilisation: Optional[float] = None
    risk: str = "LOW"                           # LOW | MEDIUM | HIGH | CRITICAL
    forecast: list[float] = field(default_factory=list)
    confidence: float = 1.0
    source: str = "e-Aushadhi"
    last_updated: str = field(default_factory=now)
    last_attested_at: Optional[str] = field(default_factory=now)
    safety_floor_days: float = 14.0
    uncertainty: float = 0.0
    batches: list[MedicineBatch] = field(default_factory=list)

    def __post_init__(self):
        if self.observed_quantity > 0.0 and self.usable_quantity == 0.0:
            self.reconcile_usable_state()
        elif self.usable_quantity > 0.0 and self.observed_quantity == 0.0:
            self.observed_quantity = self.usable_quantity

    def reconcile_usable_state(self) -> float:
        base = min(self.observed_quantity, self.claimed_quantity) if self.claimed_quantity > 0 else self.observed_quantity
        reconciled = max(base - self.expired_quantity - self.quarantined_quantity - self.reserved_quantity, 0.0)
        self.usable_quantity = round(reconciled, 1)
        return self.usable_quantity

    def is_attestation_fresh(self, max_age_hours: float = 48.0, as_of: Optional[datetime] = None) -> bool:
        if not self.last_attested_at:
            return False
        try:
            att_time = datetime.fromisoformat(self.last_attested_at.replace("Z", "+00:00"))
            if att_time.tzinfo is None:
                att_time = att_time.replace(tzinfo=timezone.utc)
            now_time = as_of or datetime.now(timezone.utc)
            age_s = (now_time - att_time).total_seconds()
            return age_s <= (max_age_hours * 3600.0)
        except Exception:
            return False

    def get_transferable_donor_qty(
        self,
        min_safety_days: float = 7.0,
        max_age_hours: float = 48.0,
        as_of: Optional[datetime] = None,
    ) -> float:
        """Returns verified surplus stock that can safely donate above safety floor."""
        if self.usable_quantity <= 0.0:
            return 0.0
        if not self.is_attestation_fresh(max_age_hours=max_age_hours, as_of=as_of):
            return 0.0
        safety_floor = self.consumption_velocity * min_safety_days
        return max(self.usable_quantity - safety_floor - self.reserved_quantity, 0.0)

    @property
    def safety_floor_quantity(self) -> float:
        return round(self.consumption_velocity * self.safety_floor_days, 1)

    @property
    def days_of_usable_stock(self) -> float:
        if self.consumption_velocity <= 0:
            return 999.0 if self.usable_quantity > 0 else 0.0
        return round(self.usable_quantity / self.consumption_velocity, 1)

    @property
    def phantom_inventory(self) -> float:
        return max(self.claimed_quantity - self.usable_quantity, 0.0)

    @property
    def phantom_quantity(self) -> float:
        return self.phantom_inventory

    @property
    def reconciliation_gap(self) -> float:
        return round(self.claimed_quantity - self.usable_quantity, 1)

    @property
    def usable_ratio(self) -> float:
        if self.claimed_quantity <= 0:
            return 1.0 if self.usable_quantity >= 0 else 0.0
        return round(min(max(self.usable_quantity / self.claimed_quantity, 0.0), 1.0), 3)

    @property
    def is_stale(self) -> bool:
        return not self.is_attestation_fresh()

    @property
    def freshness(self) -> str:
        if not self.last_attested_at:
            return "UNVERIFIED"
        return "FRESH" if self.is_attestation_fresh() else "STALE"

    def to_dict(self) -> dict:
        d = asdict(self)
        d["days_of_usable_stock"] = self.days_of_usable_stock
        d["phantom_inventory"] = self.phantom_inventory
        d["phantom_quantity"] = self.phantom_quantity
        d["reconciliation_gap"] = self.reconciliation_gap
        d["usable_ratio"] = self.usable_ratio
        d["freshness"] = self.freshness
        d["is_stale"] = self.is_stale
        d["confidence"] = self.confidence
        d["safety_floor_quantity"] = self.safety_floor_quantity
        d["is_attestation_fresh"] = self.is_attestation_fresh()
        d["reconciliation_breakdown"] = {
            "claimed": self.claimed_quantity,
            "observed": self.observed_quantity,
            "expired": self.expired_quantity,
            "quarantined": self.quarantined_quantity,
            "reserved": self.reserved_quantity,
            "incoming": self.incoming_quantity,
            "safety_floor": self.safety_floor_quantity,
            "usable": self.usable_quantity,
        }
        return d


# --------------------------------------------------------------------------
# Typed Network Model: HealthcareResourceGraph
# --------------------------------------------------------------------------

class HealthcareResourceGraph:
    """Slim typed network model maintaining nodes, edges, and 3 named queries.
    
    Nodes:
      - facilities: dict[str, Facility]
      - warehouses: dict[str, Warehouse]
      - suppliers: dict[str, Supplier]
      - resources: dict[str, Resource] (SKUs)

    Edges:
      - stocks_table: dict[(node_id, sku), ResourceState]  (STOCKS)
      - can_transfer_table: dict[(from_id, to_id), CanTransferEdge] (CAN_TRANSFER)
      - supplies_table: dict[(supplier_id, sku), SuppliesEdge] (SUPPLIES)
    """

    def __init__(self, store: Optional[EventStore] = None, engine: Optional[VerificationEngine] = None):
        self.store = store or EventStore()
        self.engine = engine or VerificationEngine(self.store)

        # Nodes
        self.facilities: dict[str, Facility] = {}
        self.warehouses: dict[str, Warehouse] = {}
        self.suppliers: dict[str, Supplier] = {}
        self.resources: dict[str, Resource] = {}

        # Edges
        self.stocks_table: dict[tuple[str, str], ResourceState] = {}
        self.can_transfer_table: dict[tuple[str, str], CanTransferEdge] = {}
        self.supplies_table: dict[tuple[str, str], SuppliesEdge] = {}

        # Backward compatibility aliases
        self.states = self.stocks_table
        self.shipments: dict[str, Shipment] = {}
        self.transit_matrix_km: dict[tuple[str, str], float] = {}
        self.demand_series: dict[tuple[str, str], Any] = {}

    # -- Node Management ---------------------------------------------------

    def add_facility(self, facility: Facility) -> None:
        self.facilities[facility.facility_id] = facility
        if facility.facility_type in (FacilityType.DISTRICT_WAREHOUSE, FacilityType.STATE_WAREHOUSE):
            self.warehouses[facility.facility_id] = Warehouse(
                facility_id=facility.facility_id,
                name=facility.name,
                facility_type=facility.facility_type,
                district=facility.district,
                state=facility.state,
                lat=facility.lat,
                lon=facility.lon,
                storage_capacity_m3=facility.storage_capacity_m3,
                cold_chain_capacity_l=facility.cold_chain_capacity_l,
                is_active=facility.is_active,
                metadata=facility.metadata,
            )
        self._sync_transfer_edges_for(facility.facility_id)

    def add_warehouse(self, warehouse: Warehouse) -> None:
        self.warehouses[warehouse.facility_id] = warehouse
        self.facilities[warehouse.facility_id] = warehouse
        self._sync_transfer_edges_for(warehouse.facility_id)

    def add_supplier(self, supplier: Supplier) -> None:
        self.suppliers[supplier.supplier_id] = supplier

    def add_resource(self, resource: Resource) -> None:
        self.resources[resource.resource_id] = resource

    def set_resource_state(self, state: ResourceState) -> None:
        key = (state.facility_id, state.resource_id)
        self.stocks_table[key] = state

    def get_resource_state(self, facility_id: str, resource_id: str) -> Optional[ResourceState]:
        return self.stocks_table.get((facility_id, resource_id))

    def record_shipment(self, shipment: Shipment) -> None:
        self.shipments[shipment.shipment_id] = shipment
        key = (shipment.dest_facility_id, shipment.resource_id)
        if key in self.stocks_table and shipment.status in ("PENDING_APPROVAL", "DISPATCHED", "IN_TRANSIT"):
            self.stocks_table[key].incoming_quantity += shipment.quantity

    # -- Edge Management ---------------------------------------------------

    def set_can_transfer(self, from_id: str, to_id: str, eta_hours: float,
                         risk_multiplier: float = 1.0, distance_km: Optional[float] = None,
                         cold_chain_compatible: bool = True) -> CanTransferEdge:
        if distance_km is None:
            distance_km = self.distance_km(from_id, to_id)
        edge = CanTransferEdge(
            from_facility=from_id,
            to_facility=to_id,
            eta_hours=eta_hours,
            risk_multiplier=risk_multiplier,
            distance_km=distance_km,
            cold_chain_compatible=cold_chain_compatible,
        )
        self.can_transfer_table[(from_id, to_id)] = edge
        return edge

    def set_supplies(self, supplier_id: str, sku: str, reliability: float = 0.95,
                     lead_time_days: float = 7.0, share_pct: float = 1.0) -> SuppliesEdge:
        edge = SuppliesEdge(
            supplier_id=supplier_id,
            sku=sku,
            reliability=reliability,
            lead_time_days=lead_time_days,
            share_pct=share_pct,
        )
        self.supplies_table[(supplier_id, sku)] = edge
        return edge

    def _sync_transfer_edges_for(self, new_fid: str) -> None:
        f1 = self.facilities[new_fid]
        for fid, f2 in self.facilities.items():
            if fid == new_fid:
                self.transit_matrix_km[(new_fid, fid)] = 0.0
                continue
            dist = haversine_distance_km(f1.lat, f1.lon, f2.lat, f2.lon)
            self.transit_matrix_km[(new_fid, fid)] = dist
            self.transit_matrix_km[(fid, new_fid)] = dist
            # Default ETA at 38 km/h road speed with 1.35 road factor
            road_km = dist * 1.35
            eta = round(road_km / 38.0, 2)
            if (new_fid, fid) not in self.can_transfer_table:
                self.can_transfer_table[(new_fid, fid)] = CanTransferEdge(
                    from_facility=new_fid, to_facility=fid, eta_hours=eta, distance_km=dist
                )
            if (fid, new_fid) not in self.can_transfer_table:
                self.can_transfer_table[(fid, new_fid)] = CanTransferEdge(
                    from_facility=fid, to_facility=new_fid, eta_hours=eta, distance_km=dist
                )

    def distance_km(self, source_id: str, dest_id: str) -> float:
        if (source_id, dest_id) in self.transit_matrix_km:
            return self.transit_matrix_km[(source_id, dest_id)]
        f1 = self.facilities.get(source_id)
        f2 = self.facilities.get(dest_id)
        if f1 and f2:
            d = haversine_distance_km(f1.lat, f1.lon, f2.lat, f2.lon)
            self.transit_matrix_km[(source_id, dest_id)] = d
            return d
        return 25.0

    # ======================================================================
    # EXACTLY THREE NAMED QUERIES
    # ======================================================================

    def donor_reachability(self, recipient: str, sku: str, deadline: float) -> list[dict]:
        """NAMED QUERY 1: donor_reachability(recipient, sku, deadline).
        
        Relational join:
          STOCKS(facility, sku) JOIN CAN_TRANSFER(donor, recipient)
        where:
          STOCKS.verified_usable_stock > safety_floor
          AND CAN_TRANSFER.effective_eta <= deadline
        
        Returns all verified donors reachable before the deadline, ranked by
        effective arrival time then surplus quantity.
        """
        results = []
        rec_fac = self.facilities.get(recipient)
        res = self.resources.get(sku)
        cold_required = res.cold_chain_required if res else False

        for (fid, r_sku), st in self.stocks_table.items():
            if r_sku != sku or fid == recipient:
                continue
            fac = self.facilities.get(fid)
            if not fac or not fac.is_active:
                continue

            # Verified usable surplus above safety floor
            surplus = st.get_transferable_donor_qty(min_safety_days=st.safety_floor_days / 2.0)
            if surplus <= 0:
                continue

            # Look up CAN_TRANSFER edge
            edge = self.can_transfer_table.get((fid, recipient))
            if not edge:
                d = self.distance_km(fid, recipient)
                edge = CanTransferEdge(from_facility=fid, to_facility=recipient,
                                       eta_hours=round((d * 1.35) / 38.0, 2), distance_km=d)

            if cold_required and not edge.cold_chain_compatible:
                continue

            eff_eta = edge.effective_eta_hours
            if eff_eta <= deadline:
                results.append({
                    "donor_facility_id": fid,
                    "donor_name": fac.name,
                    "donor_type": fac.facility_type.value,
                    "sku": sku,
                    "verified_usable_stock": st.usable_quantity,
                    "transferable_surplus": round(surplus, 1),
                    "base_eta_hours": edge.eta_hours,
                    "risk_multiplier": edge.risk_multiplier,
                    "effective_eta_hours": eff_eta,
                    "distance_km": edge.distance_km,
                    "rescue_margin_hours": round(deadline - eff_eta, 2),
                    "cold_chain_compatible": edge.cold_chain_compatible,
                })

        # Rank: fastest arrival first, largest surplus second
        results.sort(key=lambda x: (x["effective_eta_hours"], -x["transferable_surplus"]))
        return results

    def supplier_concentration(self, sku: str) -> dict[str, Any]:
        """NAMED QUERY 2: supplier_concentration(sku).
        
        Relational aggregation over SUPPLIES(supplier, sku):
          Calculates supplier volume shares and Herfindahl-Hirschman Index (HHI):
            HHI = sum((share_pct * 100) ^ 2)
          Range: 0 to 10,000 (US DOJ / Indian CCI benchmark: >2500 is highly concentrated).
        """
        suppliers_for_sku = [
            (supp_id, edge) for (supp_id, r_sku), edge in self.supplies_table.items()
            if r_sku == sku
        ]

        if not suppliers_for_sku:
            # Check master supplier list as default pool
            if self.suppliers:
                equal_share = 1.0 / len(self.suppliers)
                suppliers_for_sku = [
                    (sid, SuppliesEdge(supplier_id=sid, sku=sku, reliability=s.reliability_score,
                                       lead_time_days=s.default_lead_time_days, share_pct=equal_share))
                    for sid, s in self.suppliers.items()
                ]

        if not suppliers_for_sku:
            return {
                "sku": sku,
                "supplier_count": 0,
                "hhi": 0.0,
                "concentration_level": "NO_SUPPLIERS",
                "single_source_risk": True,
                "suppliers": [],
            }

        total_weight = sum(e.share_pct for _, e in suppliers_for_sku) or 1.0
        normalized = []
        hhi = 0.0

        for sid, edge in suppliers_for_sku:
            supp = self.suppliers.get(sid)
            share = edge.share_pct / total_weight
            hhi += (share * 100.0) ** 2
            normalized.append({
                "supplier_id": sid,
                "supplier_name": supp.name if supp else sid,
                "tier": supp.tier if supp else "STATE_EMPANELLED",
                "share_pct": round(share * 100.0, 1),
                "reliability_score": edge.reliability,
                "lead_time_days": edge.lead_time_days,
            })

        hhi = round(hhi, 1)
        if hhi >= 2500 or len(suppliers_for_sku) == 1:
            level = "HIGH"
        elif hhi >= 1500:
            level = "MODERATE"
        else:
            level = "LOW"

        return {
            "sku": sku,
            "supplier_count": len(suppliers_for_sku),
            "hhi": hhi,
            "concentration_level": level,
            "single_source_risk": bool(len(suppliers_for_sku) == 1 or hhi >= 5000),
            "dominant_supplier": max(normalized, key=lambda s: s["share_pct"])["supplier_id"],
            "suppliers": sorted(normalized, key=lambda s: s["share_pct"], reverse=True),
        }

    def failure_cascade(self, node: str, resource_id: Any = None, diversion_rate: float = 0.80) -> dict[str, Any]:
        """NAMED QUERY 3: failure_cascade(node).
        
        Evaluates systemic impact when `node` (facility or warehouse) suffers complete outage.
        Traverses CAN_TRANSFER edges to identify patient / demand diversion to adjacent
        facilities, secondary stockout risks where runway drops below lead time,
        referral hub stress, and network burden ratio.
        """
        if isinstance(resource_id, (int, float)):
            diversion_rate = float(resource_id)
            resource_id = None

        fac = self.facilities.get(node)
        if resource_id:
            primary_sku = str(resource_id)
        else:
            all_skus = [sku for (f, sku) in self.stocks_table.keys() if f == node]
            primary_sku = all_skus[0] if all_skus else "MED_ESSENTIAL"
        st = self.get_resource_state(node, primary_sku)
        velocity = st.consumption_velocity if st else 10.0

        unmet_patients_daily = round(velocity, 1)
        total_diverted = unmet_patients_daily * diversion_rate

        other_facilities = [
            (fid, f) for fid, f in self.facilities.items()
            if fid != node and f.is_active
        ]

        if not other_facilities:
            return {
                "failed_facility_id": node,
                "failed_facility_name": fac.name if fac else node,
                "direct_impact": {"unmet_patients_daily": unmet_patients_daily, "units": "patients/day"},
                "redistributed_demand": [],
                "secondary_shortages": [],
                "referral_pressure": {"hub_id": None, "diverted_volume": 0.0, "pressure_ratio": 0.0},
                "cascade_burden_score": 0.0,
                "interpretation": "Nominal: No neighbors available to absorb load",
            }

        # Weight by inverse distance / travel time
        inv_weights = {}
        for fid, f in other_facilities:
            edge = self.can_transfer_table.get((node, fid))
            d = edge.distance_km if edge else self.distance_km(node, fid)
            d = max(d, 1.0)
            if d <= 250.0:
                inv_weights[fid] = 1.0 / d

        sum_inv = sum(inv_weights.values()) or 1.0
        redistributed = []
        secondary_shortages = []
        total_neighbor_cap = 0.0
        hub_diverted = 0.0
        primary_hub_id = None

        for fid, f in other_facilities:
            edge = self.can_transfer_table.get((node, fid))
            d = edge.distance_km if edge else self.distance_km(node, fid)
            w = (inv_weights.get(fid, 0.0) / sum_inv) if fid in inv_weights else 0.0
            delta_d = round(total_diverted * w, 2)

            n_st = self.get_resource_state(fid, primary_sku)
            n_usable = n_st.usable_quantity if n_st else 0.0
            n_vel = n_st.consumption_velocity if n_st else 1.0
            n_lead = n_st.lead_time if n_st else 7.0
            total_neighbor_cap += max(n_usable, 0.0)

            new_vel = n_vel + delta_d
            new_runway = round(n_usable / new_vel, 1) if new_vel > 0 else 999.0
            old_runway = round(n_usable / n_vel, 1) if n_vel > 0 else 999.0

            if (new_runway < n_lead) and (old_runway >= n_lead):
                secondary_shortages.append({
                    "facility_id": fid,
                    "facility_name": f.name,
                    "facility_type": f.facility_type.value,
                    "usable_stock": n_usable,
                    "previous_runway_days": old_runway,
                    "diverted_demand_daily": delta_d,
                    "new_runway_days": new_runway,
                    "replenishment_lead_time_days": n_lead,
                    "status": "SECONDARY_SHORTAGE_IMMINENT",
                })

            if f.facility_type in (FacilityType.DISTRICT_HOSPITAL, FacilityType.CHC, FacilityType.DISTRICT_WAREHOUSE):
                if primary_hub_id is None or d < self.distance_km(node, primary_hub_id):
                    primary_hub_id = fid
                hub_diverted += delta_d

            redistributed.append({
                "facility_id": fid,
                "facility_name": f.name,
                "distance_km": round(d, 1),
                "diverted_patients_daily": delta_d,
                "new_daily_demand": round(new_vel, 2),
                "new_runway_days": new_runway,
            })

        surge_cap = max(total_neighbor_cap / max(len(other_facilities), 1), 10.0)
        burden_score = round(min(total_diverted / surge_cap, 1.0), 3)

        hub_fac = self.facilities.get(primary_hub_id) if primary_hub_id else None
        hub_cap = (self.get_resource_state(primary_hub_id, primary_sku).usable_quantity
                   if primary_hub_id and self.get_resource_state(primary_hub_id, primary_sku) else 50.0)
        hub_ratio = round(hub_diverted / max(hub_cap, 1.0), 2)

        interpretation = (
            "Severe: Secondary stockouts imminent; hospital emergency pressure high"
            if burden_score >= 0.5 or secondary_shortages
            else (
                "Elevated: Neighbor facilities experiencing runway compression"
                if burden_score >= 0.2
                else "Nominal: Absorbed by adjacent facilities without secondary failure"
            )
        )

        return {
            "failed_facility_id": node,
            "failed_facility_name": fac.name if fac else node,
            "resource_id": primary_sku,
            "direct_impact": {
                "unmet_patients_daily": unmet_patients_daily,
                "direct_shortfall_daily": unmet_patients_daily,
                "units": "patients/day and units/day",
            },
            "redistributed_demand": sorted(redistributed, key=lambda x: x["diverted_patients_daily"], reverse=True),
            "secondary_shortages": secondary_shortages,
            "referral_pressure": {
                "hub_id": primary_hub_id,
                "hub_name": hub_fac.name if hub_fac else "District Referral Hub",
                "diverted_volume_daily": round(hub_diverted, 1),
                "pressure_ratio": hub_ratio,
            },
            "cascade_burden_score": burden_score,
            "cascade_burden_formula": "sum(diverted_demand_daily) / network_surge_capacity",
            "units": "dimensionless ratio [0.0, 1.0]",
            "interpretation": interpretation,
        }

    # Compatibility alias for existing callers
    evaluate_network_cascade = failure_cascade

    # -- Operations & Donor Selection Compatibility -----------------------

    def get_usable_stock(self, facility_id: str, resource_id: str) -> float:
        st = self.get_resource_state(facility_id, resource_id)
        return st.usable_quantity if st else 0.0

    def get_claimed_stock(self, facility_id: str, resource_id: str) -> float:
        st = self.get_resource_state(facility_id, resource_id)
        return st.claimed_quantity if st else 0.0

    def get_donor_inventory(
        self,
        resource_id: str,
        min_safety_days: float = 7.0,
        max_attestation_age_hours: float = 48.0,
        exclude_facility_ids: Optional[list[str]] = None,
        as_of: Optional[datetime] = None,
    ) -> list[dict]:
        exclude = set(exclude_facility_ids or [])
        donors = []

        for (fid, rid), st in self.stocks_table.items():
            if rid != resource_id or fid in exclude:
                continue
            fac = self.facilities.get(fid)
            if not fac or not fac.is_active:
                continue

            safety_floor = st.consumption_velocity * min_safety_days
            surplus = st.get_transferable_donor_qty(
                min_safety_days=min_safety_days,
                max_age_hours=max_attestation_age_hours,
                as_of=as_of,
            )

            if surplus > 0:
                donors.append({
                    "facility_id": fid,
                    "facility_name": fac.name,
                    "facility_type": fac.facility_type.value,
                    "district": fac.district,
                    "usable_quantity": st.usable_quantity,
                    "claimed_quantity": st.claimed_quantity,
                    "safety_floor": round(safety_floor, 1),
                    "surplus_transferable": round(surplus, 1),
                    "days_of_stock": st.days_of_usable_stock,
                    "last_attested_at": st.last_attested_at,
                    "lat": fac.lat,
                    "lon": fac.lon,
                })

        donors.sort(key=lambda x: x["surplus_transferable"], reverse=True)
        return donors

    def get_shortage_facilities(
        self,
        resource_id: str,
        lead_time_days: float = 7.0,
        overrides: Optional[dict[str, dict]] = None,
    ) -> list[dict]:
        shortages = []
        for (fid, rid), st in self.stocks_table.items():
            if rid != resource_id:
                continue
            if overrides is not None and fid not in overrides:
                continue
            fac = self.facilities.get(fid)
            if not fac or not fac.is_active:
                continue

            ov = (overrides or {}).get(fid, {})
            lead = ov.get("lead_time_days", lead_time_days)
            usable = ov.get("usable_quantity", st.usable_quantity)
            incoming = ov.get("incoming_quantity", st.incoming_quantity)
            days_left = ov["runway_hours"] / 24.0 if "runway_hours" in ov else st.days_of_usable_stock

            needed_over_lead = st.consumption_velocity * lead
            available = usable + incoming
            shortfall = max(needed_over_lead - available, 0.0)

            if shortfall > 0 or days_left < lead:
                shortages.append({
                    "facility_id": fid,
                    "facility_name": fac.name,
                    "facility_type": fac.facility_type.value,
                    "district": fac.district,
                    "usable_quantity": usable,
                    "claimed_quantity": st.claimed_quantity,
                    "phantom_inventory": st.phantom_inventory,
                    "daily_velocity": st.consumption_velocity,
                    "days_left": days_left,
                    "shortfall_quantity": round(shortfall, 1),
                    "risk": st.risk,
                    "lat": fac.lat,
                    "lon": fac.lon,
                })

        shortages.sort(key=lambda x: x["days_left"])
        return shortages

    def sync_from_event_store(self, as_of: Optional[str] = None) -> int:
        eval_time = as_of or now()
        synced_count = 0

        for (fid, rid), st in self.stocks_table.items():
            res = self.resources.get(rid)
            rtype = res.resource_type if res else ResourceType.MEDICINE

            vs = self.engine.state_for(
                facility_id=fid,
                resource_type=rtype,
                resource_key=rid,
                as_of=eval_time,
            )

            st.claimed_quantity = vs.reported_qty or getattr(vs, "claim_stock", 0.0) or 0.0
            st.usable_quantity = (vs.verified_usable_qty
                                  if (vs.state == VerificationState.VERIFIED and vs.verified_usable_qty is not None)
                                  else 0.0)
            if vs.anchor and "observed_at" in vs.anchor:
                st.last_attested_at = vs.anchor["observed_at"]
            st.last_updated = eval_time

            if st.usable_quantity <= 0 and st.consumption_velocity > 0:
                st.risk = "CRITICAL"
            elif st.days_of_usable_stock <= 3.0:
                st.risk = "HIGH"
            elif st.days_of_usable_stock <= 7.0:
                st.risk = "MEDIUM"
            else:
                st.risk = "LOW"

            synced_count += 1

        return synced_count

    def export_graph_summary(self) -> dict:
        total_facilities = len(self.facilities)
        total_resources = len(self.resources)
        total_claimed_stock = sum(s.claimed_quantity for s in self.stocks_table.values())
        total_usable_stock = sum(s.usable_quantity for s in self.stocks_table.values())
        total_phantom = sum(s.phantom_inventory for s in self.stocks_table.values())

        risk_counts = {"LOW": 0, "MEDIUM": 0, "HIGH": 0, "CRITICAL": 0}
        for s in self.stocks_table.values():
            risk_counts[s.risk] = risk_counts.get(s.risk, 0) + 1

        return {
            "total_facilities": total_facilities,
            "total_resources": total_resources,
            "total_states": len(self.stocks_table),
            "total_claimed_stock": round(total_claimed_stock, 1),
            "total_usable_stock": round(total_usable_stock, 1),
            "total_phantom_inventory": round(total_phantom, 1),
            "risk_distribution": risk_counts,
            "provenance": Provenance.SYNTHETIC.value,
        }

    def to_dict(self) -> dict:
        return {
            "summary": self.export_graph_summary(),
            "facilities": [f.to_dict() for f in self.facilities.values()],
            "resources": [r.to_dict() for r in self.resources.values()],
            "states": [s.to_dict() for s in self.stocks_table.values()],
            "shipments": [asdict(s) for s in self.shipments.values()],
        }


ResourceGraph = HealthcareResourceGraph


# --------------------------------------------------------------------------
# Default Reference Graph
# --------------------------------------------------------------------------

def create_default_resource_graph(
    store: Optional[EventStore] = None,
    engine: Optional[VerificationEngine] = None,
) -> HealthcareResourceGraph:
    """Creates reference graph with sovereign facilities, SKUs, and default edges."""
    graph = HealthcareResourceGraph(store=store, engine=engine)

    # 1. Facilities & Warehouses
    facilities = [
        Warehouse(
            facility_id="DWH_DISTRICT_DEPOT_01",
            name="District Central Medical Depot (Apex Hub)",
            facility_type=FacilityType.DISTRICT_WAREHOUSE,
            district="Bastar",
            state="Chhattisgarh",
            lat=19.0750,
            lon=82.0150,
            catchment_population=1_400_000,
            storage_capacity_m3=1200.0,
            cold_chain_capacity_l=800.0,
        ),
        Facility(
            facility_id="DH_CENTRAL_HOSPITAL",
            name="District Civil Hospital & Trauma Centre",
            facility_type=FacilityType.DISTRICT_HOSPITAL,
            district="Bastar",
            state="Chhattisgarh",
            lat=19.0820,
            lon=82.0280,
            catchment_population=350_000,
            storage_capacity_m3=300.0,
            cold_chain_capacity_l=150.0,
        ),
        Facility(
            facility_id="CHC_RURAL_NORTH",
            name="Community Health Centre Rural North",
            facility_type=FacilityType.CHC,
            district="Bastar",
            state="Chhattisgarh",
            lat=19.3450,
            lon=81.9540,
            catchment_population=120_000,
            storage_capacity_m3=150.0,
            cold_chain_capacity_l=80.0,
        ),
        Facility(
            facility_id="PHC_REMOTE_EAST",
            name="Primary Health Centre Remote East",
            facility_type=FacilityType.PHC,
            district="Bastar",
            state="Chhattisgarh",
            lat=19.2100,
            lon=82.3400,
            catchment_population=30_000,
            storage_capacity_m3=50.0,
            cold_chain_capacity_l=20.0,
        ),
        Facility(
            facility_id="PHC_VALLEY_WEST",
            name="Primary Health Centre Valley West",
            facility_type=FacilityType.PHC,
            district="Bastar",
            state="Chhattisgarh",
            lat=18.9200,
            lon=81.7800,
            catchment_population=25_000,
            storage_capacity_m3=50.0,
            cold_chain_capacity_l=20.0,
        ),
    ]
    for fac in facilities:
        graph.add_facility(fac)

    # 2. Master Catalog Resources (SKUs)
    resources = [
        Resource(
            resource_id="MED_ANTI_RABIES_VACCINE",
            name="Anti-Rabies Vaccine (ARV) IP",
            resource_type=ResourceType.VACCINE,
            unit="vial",
            criticality=9.8,
            cold_chain_required=True,
            default_safety_stock_days=14.0,
        ),
        Resource(
            resource_id="MED_OXYTOCIN_INJ",
            name="Oxytocin Injection IP (10 IU)",
            resource_type=ResourceType.MEDICINE,
            unit="ampoule",
            criticality=9.5,
            cold_chain_required=True,
            default_safety_stock_days=14.0,
        ),
        Resource(
            resource_id="MED_ANTISNAKE_VENOM",
            name="Anti-Snake Venom Serum Polyvalent",
            resource_type=ResourceType.MEDICINE,
            unit="vial",
            criticality=10.0,
            cold_chain_required=True,
            default_safety_stock_days=21.0,
        ),
        Resource(
            resource_id="MED_AMOXICILLIN_500",
            name="Amoxicillin Trihydrate 500mg",
            resource_type=ResourceType.MEDICINE,
            unit="tablet",
            criticality=6.0,
            cold_chain_required=False,
            default_safety_stock_days=30.0,
        ),
        Resource(
            resource_id="MED_PARACETAMOL_500",
            name="Paracetamol 500mg IP",
            resource_type=ResourceType.MEDICINE,
            unit="tablet",
            criticality=4.0,
            cold_chain_required=False,
            default_safety_stock_days=30.0,
        ),
    ]
    for r in resources:
        graph.add_resource(r)

    # 3. Suppliers
    suppliers = [
        Supplier(
            supplier_id="SUPP_CENTRAL_PSU",
            name="Haffkine Bio-Pharmaceuticals (Central PSU)",
            tier="CENTRAL_PSU",
            default_lead_time_days=10.0,
            reliability_score=0.96,
        ),
        Supplier(
            supplier_id="SUPP_STATE_CORP",
            name="Chhattisgarh Medical Services Corp (CGMSC)",
            tier="STATE_EMPANELLED",
            default_lead_time_days=6.0,
            reliability_score=0.92,
        ),
    ]
    for s in suppliers:
        graph.add_supplier(s)

    # 4. SUPPLIES edges
    for r in resources:
        graph.set_supplies("SUPP_CENTRAL_PSU", r.resource_id, reliability=0.96,
                           lead_time_days=10.0, share_pct=0.6)
        graph.set_supplies("SUPP_STATE_CORP", r.resource_id, reliability=0.92,
                           lead_time_days=6.0, share_pct=0.4)

    # 5. Baseline STOCKS edges
    # Default state setup
    # DWH: large verified usable stock
    graph.set_resource_state(ResourceState(
        facility_id="DWH_DISTRICT_DEPOT_01",
        resource_id="MED_ANTI_RABIES_VACCINE",
        claimed_quantity=500.0,
        observed_quantity=500.0,
        usable_quantity=500.0,
        consumption_velocity=12.0,
        lead_time=5.0,
        safety_floor_days=7.0,
    ))
    # CHC_RURAL_NORTH: expired phantom inventory (claims 200, usable 0)
    graph.set_resource_state(ResourceState(
        facility_id="CHC_RURAL_NORTH",
        resource_id="MED_ANTI_RABIES_VACCINE",
        claimed_quantity=200.0,
        observed_quantity=0.0,
        expired_quantity=200.0,
        usable_quantity=0.0,
        consumption_velocity=8.0,
        lead_time=7.0,
        safety_floor_days=7.0,
    ))
    # PHC_REMOTE_EAST: shortage / surge
    graph.set_resource_state(ResourceState(
        facility_id="PHC_REMOTE_EAST",
        resource_id="MED_ANTI_RABIES_VACCINE",
        claimed_quantity=4.0,
        observed_quantity=4.0,
        usable_quantity=4.0,
        consumption_velocity=6.0,
        lead_time=6.0,
        safety_floor_days=7.0,
    ))
    # PHC_VALLEY_WEST: small buffer
    graph.set_resource_state(ResourceState(
        facility_id="PHC_VALLEY_WEST",
        resource_id="MED_ANTI_RABIES_VACCINE",
        claimed_quantity=45.0,
        observed_quantity=45.0,
        usable_quantity=45.0,
        consumption_velocity=3.0,
        lead_time=6.0,
        safety_floor_days=7.0,
    ))
    # DH_CENTRAL_HOSPITAL
    graph.set_resource_state(ResourceState(
        facility_id="DH_CENTRAL_HOSPITAL",
        resource_id="MED_ANTI_RABIES_VACCINE",
        claimed_quantity=120.0,
        observed_quantity=120.0,
        usable_quantity=120.0,
        consumption_velocity=10.0,
        lead_time=4.0,
        safety_floor_days=7.0,
    ))

    return graph
