from dataclasses import dataclass
from typing import Optional, Any
from tathyon.schema import now
from tathyon.graph import HealthcareResourceGraph, ResourceState

@dataclass
class DVDMSSyncEvent:
    """Represents a baseline inventory state pulled from the DVDMS system of record."""
    sync_id: str
    facility_id: str
    resource_id: str
    reported_quantity: float
    timestamp: str
    metadata: dict[str, Any]

class DVDMSAdapter:
    """
    Simulates integration with the government system of record (DVDMS/e-Aushadhi).
    Tathyon uses this to establish the 'claimed' baseline before reconciling against physical reality.
    """
    def __init__(self, graph: HealthcareResourceGraph):
        self.graph = graph
        self.sync_history: list[DVDMSSyncEvent] = []

    def sync_facility_inventory(self, facility_id: str, dvdms_payload: list[dict]) -> None:
        """
        Takes a raw payload from DVDMS and updates Tathyon's ResourceState `claimed_quantity`.
        
        Expected payload format:
        [
            {"resource_id": "MED_123", "quantity": 500.0, "last_updated": "2026-09-20T10:00:00Z"},
            ...
        ]
        """
        sync_time = now()
        for item in dvdms_payload:
            resource_id = item["resource_id"]
            reported_qty = float(item["quantity"])
            
            event = DVDMSSyncEvent(
                sync_id=f"SYNC-{facility_id}-{sync_time}",
                facility_id=facility_id,
                resource_id=resource_id,
                reported_quantity=reported_qty,
                timestamp=item.get("last_updated", sync_time),
                metadata=item
            )
            self.sync_history.append(event)
            
            # Update the graph's baseline claimed quantity.
            state = self.graph.get_resource_state(facility_id, resource_id)
            if state:
                state.claimed_quantity = reported_qty
                # Re-run reconciliation just in case observed is already set
                state.reconcile_usable_state()
            else:
                # Create a new state if it doesn't exist
                new_state = ResourceState(
                    facility_id=facility_id,
                    resource_id=resource_id,
                    claimed_quantity=reported_qty,
                    observed_quantity=0.0, # Not yet physically observed
                    usable_quantity=0.0
                )
                self.graph.set_resource_state(new_state)
