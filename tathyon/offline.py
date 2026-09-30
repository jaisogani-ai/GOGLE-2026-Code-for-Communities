import uuid
from dataclasses import dataclass, field
from typing import Any, Optional
from enum import Enum
from tathyon.schema import now
from tathyon.cases import CaseManager, ResilienceCase

class SyncStatus(Enum):
    PENDING = "PENDING"
    SYNCED = "SYNCED"
    CONFLICT = "CONFLICT"
    FAILED = "FAILED"

@dataclass
class OfflineEvent:
    """An event captured offline to be synchronized later."""
    event_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    facility_id: str = ""
    event_type: str = "" # e.g. "RECEIVE_SHIPMENT", "PHYSICAL_COUNT"
    payload: dict[str, Any] = field(default_factory=dict)
    timestamp: str = field(default_factory=now)
    status: SyncStatus = SyncStatus.PENDING
    sync_message: str = ""

class OfflineQueueManager:
    """
    Manages offline operations for facilities with intermittent connectivity.
    Records events locally (in-memory simulation) and pushes them to the backend when reconnected.
    """
    def __init__(self, case_manager: CaseManager, shipments_manager: Any):
        self.queue: list[OfflineEvent] = []
        self.cases = case_manager
        self.shipments = shipments_manager
        
    def enqueue(self, event: OfflineEvent) -> None:
        """Queue an event while offline."""
        self.queue.append(event)
        
    def get_pending(self) -> list[OfflineEvent]:
        return [e for e in self.queue if e.status == SyncStatus.PENDING]
        
    def process_sync(self, facility_id: str) -> dict:
        """
        Simulates connection restored for a facility.
        Processes all pending events for that facility.
        """
        pending = [e for e in self.queue if e.status == SyncStatus.PENDING and e.facility_id == facility_id]
        results = {"success": 0, "conflicts": 0, "failed": 0}
        
        for event in pending:
            try:
                if event.event_type == "RECEIVE_SHIPMENT":
                    order_id = event.payload.get("order_id")
                    received_qty = event.payload.get("received_quantity", 0.0)
                    loss_qty = event.payload.get("loss_quantity", 0.0)
                    
                    # Call the actual backend shipments manager
                    self.shipments.record_delivery(
                        order_id=order_id,
                        received_quantity=received_qty,
                        actual_delivery_date=event.timestamp,
                        loss_quantity=loss_qty,
                        notes=f"Synced from offline queue. {event.payload.get('notes', '')}"
                    )
                    
                    if loss_qty > 0.0:
                        # This generates a conflict/discrepancy case because what was supposed to arrive didn't
                        self.cases.create_case(
                            facility=facility_id,
                            resource=event.payload.get("resource_id", "UNKNOWN"),
                            trigger="OFFLINE_SYNC_CONFLICT",
                            risk=1.0,
                            severity="CRITICAL",
                            evidence={"order_id": order_id, "loss": loss_qty, "event_id": event.event_id}
                        )
                        event.status = SyncStatus.CONFLICT
                        event.sync_message = "Synced with discrepancy (loss recorded). Case opened."
                        results["conflicts"] += 1
                    else:
                        event.status = SyncStatus.SYNCED
                        event.sync_message = "Synced successfully."
                        results["success"] += 1
                else:
                    event.status = SyncStatus.FAILED
                    event.sync_message = f"Unknown event type: {event.event_type}"
                    results["failed"] += 1
                    
            except Exception as e:
                event.status = SyncStatus.FAILED
                event.sync_message = f"Exception during sync: {str(e)}"
                results["failed"] += 1
                
        return results
