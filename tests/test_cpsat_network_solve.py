"""
Regression and property tests for ONE CP-SAT network solve per planning cycle.

Verifies:
1. Hand-worked 3-facility fixture with competing shortages:
   Prioritizes high-essentiality/urgent recipient, partial fulfillment shows
   fulfilled_qty/shortfall_qty and sets REPLAN_REQUIRED.
2. Cold-chain constraint feasibility.
3. Folded rescue-margin deadline constraints.
4. Per-cycle transport (truck) capacity constraint.
5. IntAffine floordiv bug class regression (Python int coercion).
6. Infeasible instances return NO_FEASIBLE_PLAN with machine-readable reasons.
7. Property test on randomized instances: unverified stock NEVER appears in any solution.
"""
import random
import pytest
from tathyon.optimize import (
    Decision,
    Facility,
    Need,
    SourceStock,
    Transfer,
    counterfactual,
    naive_optimise,
    optimise,
)
from tathyon.schema import VerificationState


def test_hand_worked_3_facility_fixture_competing_shortages():
    """HAND-WORKED 3-FACILITY FIXTURE: Competing shortages under scarce verified supply.

    Topology:
      - Facility A (Donor): holds 40 units verified transferable stock.
      - Facility B (Recipient 1 - Urgent/Vital): shortage = 30, days_to_stockout = 1.0, essentiality = 3.0.
      - Facility C (Recipient 2 - Routine/Normal): shortage = 30, days_to_stockout = 4.0, essentiality = 1.0.

    Total need = 60 units. Total donor capacity = 40 units (with max_donor_fraction=1.0).
    Expected:
      - One network solve resolves competing shortages globally.
      - Vital/urgent recipient B receives full 30 units.
      - Routine recipient C receives remaining 10 units.
      - fulfilled_qty = 40.0, shortfall_qty = 20.0.
      - replan_required is TRUE.
      - status is ALLOWED (not NO_FEASIBLE_PLAN, since 40 units can safely move).
    """
    facilities = {
        "FAC_A": Facility("FAC_A", tier="DH", x=0.0, y=0.0),
        "FAC_B": Facility("FAC_B", tier="PHC", x=0.1, y=0.1),
        "FAC_C": Facility("FAC_C", tier="PHC", x=0.1, y=-0.1),
    }

    sources = [
        SourceStock(
            facility_id="FAC_A",
            resource_key="MED_ARV",
            reported_qty=100.0,
            verified_state=VerificationState.VERIFIED,
            q_alpha=40.0,
            safety_stock=0.0,
        )
    ]

    needs = [
        Need(
            facility_id="FAC_B",
            resource_key="MED_ARV",
            shortfall=30.0,
            days_to_stockout=1.0,
            essentiality=3.0,
        ),
        Need(
            facility_id="FAC_C",
            resource_key="MED_ARV",
            shortfall=30.0,
            days_to_stockout=4.0,
            essentiality=1.0,
        ),
    ]

    decision = optimise(facilities, sources, needs, max_donor_fraction=1.0)

    assert decision.allowed is True
    assert decision.status == "ALLOWED"
    assert decision.fulfilled_qty == 40.0
    assert decision.shortfall_qty == 20.0
    assert decision.replan_required is True

    # B gets full 30 units, C gets 10 units
    allocations = {t.to_facility: t.qty for t in decision.transfers}
    assert allocations.get("FAC_B") == 30.0
    assert allocations.get("FAC_C") == 10.0


def test_intaffine_floordiv_coercion_regression():
    """Ensure integer reduction occurs BEFORE CP-SAT variable multiplication."""
    facilities = {
        "D": Facility("D", tier="DH", x=0.0, y=0.0),
        "R": Facility("R", tier="PHC", x=0.2, y=0.2),
    }
    sources = [
        SourceStock("D", "SKU1", 100.0, VerificationState.VERIFIED, q_alpha=50.0,
                    days_to_expiry=15.5)
    ]
    needs = [
        Need("R", "SKU1", shortfall=25.7, days_to_stockout=1.333, essentiality=2.75)
    ]
    # This must not raise TypeError (IntAffine has no __floordiv__)
    dec = optimise(facilities, sources, needs, max_donor_fraction=1.0)
    assert dec.allowed is True
    assert dec.fulfilled_qty > 0


def test_infeasible_instance_returns_no_feasible_plan_with_reasons():
    """When all donors cannot reach recipient within constraints, returns NO_FEASIBLE_PLAN."""
    facilities = {
        "D": Facility("D", tier="DH", x=0.0, y=0.0),
        "R": Facility("R", tier="PHC", x=50.0, y=50.0),  # Far away
    }
    # Available stock exists, but travel time exceeds limit
    sources = [
        SourceStock("D", "SKU1", 100.0, VerificationState.VERIFIED, q_alpha=50.0)
    ]
    needs = [
        Need("R", "SKU1", shortfall=20.0, days_to_stockout=2.0)
    ]
    dec = optimise(facilities, sources, needs, max_travel_hours=1.0)
    assert dec.allowed is False
    assert dec.status == "NO_FEASIBLE_PLAN"
    assert dec.fulfilled_qty == 0.0
    assert dec.shortfall_qty == 20.0
    assert dec.replan_required is True
    assert len(dec.rejected_donors) > 0
    assert any("EXCEEDS_MAX_TRAVEL_HOURS" in str(r.get("reason")) for r in dec.rejected_donors)


def test_cold_chain_feasibility_enforcement():
    """Cold-chain requiring SKU cannot move to or from non-cold-chain facility."""
    facilities = {
        "D_NO_COLD": Facility("D_NO_COLD", tier="DH", x=0.0, y=0.0, has_cold_chain=False),
        "R_COLD": Facility("R_COLD", tier="PHC", x=0.1, y=0.1, has_cold_chain=True),
    }
    sources = [
        SourceStock("D_NO_COLD", "VACCINE", 100.0, VerificationState.VERIFIED, q_alpha=50.0)
    ]
    needs = [
        Need("R_COLD", "VACCINE", shortfall=20.0, days_to_stockout=2.0, cold_chain_required=True)
    ]
    dec = optimise(facilities, sources, needs)
    assert dec.allowed is False
    assert dec.status == "NO_FEASIBLE_PLAN"
    assert any("COLD_CHAIN" in str(r.get("reason")) for r in dec.rejected_donors)


def test_folded_rescue_margin_deadline_constraint():
    """Folded rescue margin: route arriving after deadline is rejected."""
    facilities = {
        "D": Facility("D", tier="DH", x=0.0, y=0.0),
        "R": Facility("R", tier="PHC", x=1.0, y=1.0),
    }
    sources = [
        SourceStock("D", "SKU1", 100.0, VerificationState.VERIFIED, q_alpha=50.0,
                    handling_hours=1.0, access_risk_multiplier=1.5)  # effective = 1.08h
    ]
    needs = [
        # Deadline is 0.5 hours -> transit takes 1.08 hours -> arrives after failure
        Need("R", "SKU1", shortfall=20.0, days_to_stockout=0.1, deadline_hours=0.5)
    ]
    dec = optimise(facilities, sources, needs)
    assert dec.allowed is False
    assert dec.status == "NO_FEASIBLE_PLAN"
    assert any("ARRIVES_AFTER_DEADLINE" in str(r.get("reason")) for r in dec.rejected_donors)


def test_truck_capacity_constraint():
    """Transport capacity limit caps total units moved across the network in one cycle."""
    facilities = {
        "D": Facility("D", tier="DH", x=0.0, y=0.0),
        "R": Facility("R", tier="PHC", x=0.1, y=0.1),
    }
    sources = [
        SourceStock("D", "SKU1", 500.0, VerificationState.VERIFIED, q_alpha=500.0)
    ]
    needs = [
        Need("R", "SKU1", shortfall=300.0, days_to_stockout=2.0)
    ]
    dec = optimise(facilities, sources, needs, max_donor_fraction=1.0, max_cycle_truck_capacity=100.0)
    assert dec.allowed is True
    assert dec.fulfilled_qty == 100.0
    assert dec.shortfall_qty == 200.0
    assert dec.replan_required is True


def test_property_unverified_stock_never_appears_in_any_solution():
    """PROPERTY TEST: On 50 randomized instances with mixed verified and unverified stock,
    unverified stock NEVER appears in any transfer."""
    rng = random.Random(42)

    for run_idx in range(50):
        n_fac = rng.randint(3, 8)
        facilities = {
            f"F_{i}": Facility(f"F_{i}", tier=rng.choice(["PHC", "CHC", "DH"]),
                               x=rng.uniform(-1.0, 1.0), y=rng.uniform(-1.0, 1.0))
            for i in range(n_fac)
        }

        sources = []
        verified_fids = set()
        for i in range(n_fac):
            is_verified = rng.choice([True, False, False])  # ~33% verified
            reported = rng.uniform(10.0, 200.0)
            q_alpha = reported * rng.uniform(0.5, 1.0) if is_verified else None
            state = VerificationState.VERIFIED if is_verified else rng.choice([
                VerificationState.UNVERIFIED,
                VerificationState.CONFLICTED,
                VerificationState.REJECTED,
            ])
            fid = f"F_{i}"
            if is_verified and q_alpha and q_alpha > 10.0:
                verified_fids.add(fid)
            sources.append(SourceStock(
                facility_id=fid,
                resource_key="MED_TEST",
                reported_qty=reported,
                verified_state=state,
                q_alpha=q_alpha,
                safety_stock=10.0,
            ))

        needs = [
            Need(
                facility_id=f"F_{rng.randint(0, n_fac - 1)}",
                resource_key="MED_TEST",
                shortfall=rng.uniform(10.0, 80.0),
                days_to_stockout=rng.uniform(0.5, 10.0),
                essentiality=rng.choice([1.0, 2.0, 3.0]),
            )
            for _ in range(rng.randint(1, 3))
        ]

        dec = optimise(facilities, sources, needs)

        if dec.allowed and dec.transfers:
            for t in dec.transfers:
                # PROPERTY 1: Donor must be in verified_fids
                assert t.from_facility in verified_fids, (
                    f"VIOLATION: Unverified facility {t.from_facility} moved stock in run {run_idx}!"
                )
                # PROPERTY 2: Quantity must be positive
                assert t.qty > 0
