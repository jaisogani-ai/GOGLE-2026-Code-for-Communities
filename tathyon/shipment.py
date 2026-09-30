"""
TATHYON Shipment Intelligence & Supplier/Route Reliability.

Provides three interconnected capabilities:

1. SHIPMENT INTELLIGENCE: Models the lifecycle order → shipment → ETA → route →
   received_quantity → delay → loss. The critical insight is:
   
   SHIPMENT ARRIVING AFTER STOCKOUT = REPLENISHMENT FAILURE
   
   The system must escalate BEFORE the shipment arrives too late.

2. SUPPLIER RELIABILITY: Computes fill_rate, delivery_variance, and loss rate
   from actual outcome events. NOT fake historical reliability. Uses real
   observation data.

3. ROUTE RELIABILITY: Historical delay profile per route pair, computed from
   actual shipment outcomes.

CRITICAL RULE: All reliability metrics are derived from actual recorded events.
If insufficient events exist, the system reports "INSUFFICIENT_DATA" and uses
configurable defaults with explicit labeling.
"""
from __future__ import annotations

import math
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from .schema import now


# --------------------------------------------------------------------------
# Shipment Lifecycle
# --------------------------------------------------------------------------

@dataclass
class ShipmentOrder:
    """A complete shipment lifecycle record."""
    order_id: str
    supplier_id: str
    supplier_name: str
    source_facility_id: str
    dest_facility_id: str
    resource_id: str
    ordered_quantity: float
    expected_delivery_date: str     # ISO date
    actual_delivery_date: Optional[str] = None
    received_quantity: float = 0.0
    damaged_quantity: float = 0.0
    status: str = "ORDERED"         # ORDERED, DISPATCHED, IN_TRANSIT, DELIVERED, DELAYED, FAILED
    route_id: Optional[str] = None
    delay_days: float = 0.0
    loss_quantity: float = 0.0
    tracking_mode: str = "MANUAL_CHECKPOINT"  # LIVE_GPS, SIMULATED, MANUAL_CHECKPOINT
    stockout_avoided: bool = False
    notes: str = ""
    created_at: str = field(default_factory=now)
    indent_date: Optional[str] = None       # when the indent/order was raised (defaults to created_at)
    dispatched_quantity: float = 0.0
    dispatched_at: Optional[str] = None     # when the vehicle actually left

    @property
    def usable_quantity(self) -> float:
        """Physically received and not damaged."""
        return max(self.received_quantity - self.damaged_quantity, 0.0)

    @property
    def fill_rate(self) -> float:
        """Actual delivery vs ordered quantity."""
        if self.ordered_quantity <= 0:
            return 1.0
        return min(self.received_quantity / self.ordered_quantity, 1.0)

    @property
    def is_delayed(self) -> bool:
        return self.delay_days > 0

    @property
    def is_short(self) -> bool:
        return self.received_quantity < self.ordered_quantity * 0.95

    def to_dict(self) -> dict:
        d = asdict(self)
        d["fill_rate"] = round(self.fill_rate, 3)
        d["is_delayed"] = self.is_delayed
        d["is_short"] = self.is_short
        return d


# Legal shipment status transitions. DELAYED is reserved for external feeds.
SHIPMENT_TRANSITIONS: dict[str, set[str]] = {
    "ORDERED": {"DISPATCHED", "FAILED"},
    "DISPATCHED": {"IN_TRANSIT", "FAILED"},
    "IN_TRANSIT": {"DELIVERED", "FAILED"},
    "DELAYED": {"IN_TRANSIT", "DELIVERED", "FAILED"},
    "DELIVERED": set(),
    "FAILED": set(),
}


# --------------------------------------------------------------------------
# Shipment Intelligence Engine
# --------------------------------------------------------------------------

class ShipmentIntelligence:
    """Tracks shipments and detects replenishment failures BEFORE they happen.
    
    The critical logic: if a facility's predicted stockout date is BEFORE the
    shipment's ETA, the shipment is a REPLENISHMENT FAILURE even before it
    arrives.
    """

    def __init__(self):
        self.orders: list[ShipmentOrder] = []

    def record_order(self, order: ShipmentOrder) -> None:
        self.orders.append(order)

    def get_order(self, order_id: str) -> Optional[ShipmentOrder]:
        return next((o for o in self.orders if o.order_id == order_id), None)

    def advance_status(
        self,
        order_id: str,
        new_status: str,
        dispatched_quantity: Optional[float] = None,
        notes: str = "",
    ) -> ShipmentOrder:
        """Validated lifecycle transition ORDERED -> DISPATCHED -> IN_TRANSIT -> DELIVERED | FAILED."""
        order = self.get_order(order_id)
        if order is None:
            raise KeyError(f"SHIPMENT_NOT_FOUND: {order_id}")
        if new_status not in SHIPMENT_TRANSITIONS.get(order.status, set()):
            raise ValueError(
                f"INVALID_SHIPMENT_TRANSITION: {order.status} -> {new_status}. "
                f"Allowed: {sorted(SHIPMENT_TRANSITIONS.get(order.status, set()))}"
            )
        if dispatched_quantity is not None:
            if dispatched_quantity < 0:
                raise ValueError("dispatched_quantity must be >= 0")
            order.dispatched_quantity = dispatched_quantity
        if notes:
            order.notes = notes
        order.status = new_status
        return order

    def record_delivery(
        self,
        order_id: str,
        received_quantity: float,
        actual_delivery_date: Optional[str] = None,
        loss_quantity: float = 0.0,
        damaged_quantity: float = 0.0,
        stockout_avoided: bool = False,
        notes: str = "",
    ) -> Optional[ShipmentOrder]:
        """Records actual delivery outcome."""
        for order in self.orders:
            if order.order_id == order_id:
                order.received_quantity = received_quantity
                order.damaged_quantity = damaged_quantity
                order.stockout_avoided = stockout_avoided
                order.actual_delivery_date = actual_delivery_date or now()
                order.loss_quantity = loss_quantity
                order.notes = notes
                order.status = "DELIVERED"

                # Calculate delay
                try:
                    expected = datetime.fromisoformat(order.expected_delivery_date.replace("Z", "+00:00"))
                    actual = datetime.fromisoformat(order.actual_delivery_date.replace("Z", "+00:00"))
                    if expected.tzinfo is None:
                        expected = expected.replace(tzinfo=timezone.utc)
                    if actual.tzinfo is None:
                        actual = actual.replace(tzinfo=timezone.utc)
                    order.delay_days = max((actual - expected).total_seconds() / 86400.0, 0.0)
                except Exception:
                    order.delay_days = 0.0

                return order
        return None

    def check_eta_risk(self, order_id: str, eta_hours: float, recipient_runway_hours: float) -> dict:
        """
        Feature 7: DELIVERY ETA RISK.
        If shipment ETA exceeds recipient's remaining safe runway, trigger escalation.
        """
        for order in self.orders:
            if order.order_id == order_id:
                if eta_hours > recipient_runway_hours:
                    return {
                        "status": "ESCALATED",
                        "reason": "DELIVERY WILL ARRIVE AFTER PROJECTED SHORTAGE",
                        "eta_hours": eta_hours,
                        "runway_hours": recipient_runway_hours,
                        "action": "Trigger ALTERNATIVE PLAN SEARCH"
                    }
                return {"status": "SAFE", "eta_hours": eta_hours, "runway_hours": recipient_runway_hours}
        return {"status": "NOT_FOUND"}

    def check_replenishment_risk(
        self,
        facility_id: str,
        resource_id: str,
        days_to_stockout: float,
    ) -> dict:
        """Checks if pending shipments will arrive BEFORE stockout.
        
        CRITICAL INSIGHT: SHIPMENT_ARRIVING_AFTER_STOCKOUT = REPLENISHMENT_FAILURE
        """
        pending = [
            o for o in self.orders
            if o.dest_facility_id == facility_id
            and o.resource_id == resource_id
            and o.status in ("ORDERED", "DISPATCHED", "IN_TRANSIT")
        ]

        if not pending:
            return {
                "facility_id": facility_id,
                "resource_id": resource_id,
                "days_to_stockout": round(days_to_stockout, 1),
                "pending_shipments": 0,
                "status": "NO_PENDING_SHIPMENT",
                "risk": "HIGH" if days_to_stockout < 7 else "MEDIUM",
                "action": "No shipment in pipeline. Verified redistribution or state escalation required."
                          if days_to_stockout < 7 else "Monitor and place order if needed.",
            }

        results = []
        for order in pending:
            try:
                eta = datetime.fromisoformat(order.expected_delivery_date.replace("Z", "+00:00"))
                if eta.tzinfo is None:
                    eta = eta.replace(tzinfo=timezone.utc)
                days_to_eta = max((eta - datetime.now(timezone.utc)).total_seconds() / 86400.0, 0.0)
            except Exception:
                days_to_eta = order.delay_days + 7.0

            arrives_before_stockout = days_to_eta < days_to_stockout

            results.append({
                "order_id": order.order_id,
                "supplier": order.supplier_name,
                "ordered_quantity": order.ordered_quantity,
                "days_to_eta": round(days_to_eta, 1),
                "arrives_before_stockout": arrives_before_stockout,
                "status": "ON_TRACK" if arrives_before_stockout else "REPLENISHMENT_FAILURE",
                "gap_days": round(days_to_eta - days_to_stockout, 1),
            })

        any_on_track = any(r["arrives_before_stockout"] for r in results)
        total_incoming = sum(o.ordered_quantity for o in pending)

        return {
            "facility_id": facility_id,
            "resource_id": resource_id,
            "days_to_stockout": round(days_to_stockout, 1),
            "pending_shipments": len(pending),
            "total_incoming_quantity": total_incoming,
            "shipment_details": results,
            "status": "COVERED" if any_on_track else "REPLENISHMENT_FAILURE",
            "risk": "LOW" if any_on_track else "CRITICAL",
            "action": "Shipment on track to arrive before stockout." if any_on_track
                      else f"ESCALATE: All {len(pending)} pending shipments arrive AFTER predicted "
                           f"stockout in {days_to_stockout:.1f} days. Redistribution required.",
        }

    def get_facility_shipment_history(
        self,
        facility_id: str,
        resource_id: Optional[str] = None,
    ) -> list[dict]:
        """Returns shipment history for a facility."""
        history = [
            o.to_dict() for o in self.orders
            if o.dest_facility_id == facility_id
            and (resource_id is None or o.resource_id == resource_id)
            and o.status == "DELIVERED"
        ]
        return sorted(history, key=lambda x: x.get("actual_delivery_date", ""), reverse=True)


# --------------------------------------------------------------------------
# Supplier Reliability
# --------------------------------------------------------------------------

class SupplierReliability:
    """Computes supplier performance from actual delivery outcomes.
    
    All metrics derived from recorded events. NOT fake historical scores.
    """

    def __init__(self, shipment_intelligence: ShipmentIntelligence):
        self.si = shipment_intelligence

    def compute_reliability(self, supplier_id: str) -> dict:
        """Computes supplier reliability metrics from actual deliveries."""
        deliveries = [
            o for o in self.si.orders
            if o.supplier_id == supplier_id and o.status == "DELIVERED"
        ]

        if len(deliveries) < 3:
            return {
                "supplier_id": supplier_id,
                "status": "INSUFFICIENT_DATA",
                "deliveries_count": len(deliveries),
                "message": f"Only {len(deliveries)} completed deliveries. "
                           f"Minimum 3 required for reliability calculation.",
                "fill_rate": None,
                "on_time_rate": None,
                "avg_delay_days": None,
                "loss_rate": None,
            }

        fill_rates = [d.fill_rate for d in deliveries]
        delays = [d.delay_days for d in deliveries]
        on_time = sum(1 for d in deliveries if d.delay_days <= 1.0) / len(deliveries)
        total_ordered = sum(d.ordered_quantity for d in deliveries)
        total_lost = sum(d.loss_quantity for d in deliveries)

        avg_fill = sum(fill_rates) / len(fill_rates)
        avg_delay = sum(delays) / len(delays)
        delay_std = math.sqrt(sum((d - avg_delay) ** 2 for d in delays) / len(delays))
        loss_rate = total_lost / max(total_ordered, 1.0)

        # Composite reliability score (geometric mean of factors)
        factors = [avg_fill, on_time, max(1.0 - loss_rate, 0.01)]
        composite = math.exp(sum(math.log(f) for f in factors) / len(factors))

        return {
            "supplier_id": supplier_id,
            "status": "COMPUTED",
            "deliveries_count": len(deliveries),
            "fill_rate": round(avg_fill, 3),
            "on_time_rate": round(on_time, 3),
            "avg_delay_days": round(avg_delay, 1),
            "delay_std_days": round(delay_std, 1),
            "loss_rate": round(loss_rate, 4),
            "composite_reliability": round(composite, 3),
            "interpretation": (
                "RELIABLE: Consistent fill rate and on-time delivery."
                if composite >= 0.85 else (
                    "ACCEPTABLE: Minor fill rate or timing gaps."
                    if composite >= 0.70 else
                    "UNRELIABLE: Significant delivery shortfalls or delays."
                )
            ),
            "methodology": "Geometric mean of fill rate, on-time rate (≤1 day tolerance), "
                           "and (1 - loss rate).",
        }


# --------------------------------------------------------------------------
# Route Reliability
# --------------------------------------------------------------------------

class RouteReliability:
    """Computes route performance from actual shipment outcomes."""

    def __init__(self, shipment_intelligence: ShipmentIntelligence):
        self.si = shipment_intelligence

    def compute_route_reliability(self, source_id: str, dest_id: str) -> dict:
        """Computes route reliability between two facilities."""
        deliveries = [
            o for o in self.si.orders
            if o.source_facility_id == source_id
            and o.dest_facility_id == dest_id
            and o.status == "DELIVERED"
        ]

        if len(deliveries) < 2:
            return {
                "source_facility_id": source_id,
                "dest_facility_id": dest_id,
                "status": "INSUFFICIENT_DATA",
                "deliveries_count": len(deliveries),
                "avg_delay_days": None,
                "reliability": None,
            }

        delays = [d.delay_days for d in deliveries]
        avg_delay = sum(delays) / len(delays)
        on_time = sum(1 for d in delays if d <= 1.0) / len(delays)
        losses = [d.loss_quantity for d in deliveries]
        avg_loss = sum(losses) / max(len(losses), 1)

        return {
            "source_facility_id": source_id,
            "dest_facility_id": dest_id,
            "status": "COMPUTED",
            "deliveries_count": len(deliveries),
            "avg_delay_days": round(avg_delay, 1),
            "on_time_rate": round(on_time, 3),
            "avg_loss_quantity": round(avg_loss, 1),
            "interpretation": (
                "RELIABLE route" if on_time >= 0.85 else
                "DEGRADED route — consider alternatives" if on_time >= 0.60 else
                "UNRELIABLE route — high delay/loss risk"
            ),
        }
