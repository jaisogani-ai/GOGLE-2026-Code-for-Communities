"""TATHYON Personnel Module — Roster vs Physical Attendance, Trust Scoring & Redeployment.

Build with AI: Code for Communities 2.0 (Track 3) — Smart Health & Supply Chain Resilience.

DOCTRINES & OPERATIONAL INVARIANTS:
1. GHOST WORKER TRAP: Trusting paper rosters without attendance trust scoring results in
   redeploying staff who do not physically exist, leaving emergency clinics abandoned.
2. CONFIGURED SAFETY FLOORS: A donor facility must not be stripped below the configured
   clinical staffing floor. Validate each floor against current state and facility policy.
3. AI NEVER CERTIFIES ATTENDANCE: Humans (District Health Officers / Verifiers) conduct roll-calls;
   Gemini transcribes handwritten attendance registers with blank-to-fill confidence gating.
4. HUMAN APPROVAL: Every inter-facility staff redeployment requires review by an
   authorized human under the applicable department and state procedures.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, asdict, field
from typing import Any, Dict, List, Optional, Tuple
import numpy as np
import pandas as pd
from sklearn.ensemble import GradientBoostingClassifier

from .schema import ResourceType, FacilityType, now, new_id


# --------------------------------------------------------------------------
# Staff Roles & CAG/NITI Aayog Failure Anchors
# --------------------------------------------------------------------------

STAFF_ROLES = {
    "MEDICAL_OFFICER": {"title": "General Medical Officer (MBBS)", "tier": "physician", "min_per_phc": 1},
    "SPECIALIST_OBGYN": {"title": "Obstetrician / Gynecologist", "tier": "specialist", "min_per_phc": 0},
    "SPECIALIST_PEDIA": {"title": "Pediatrician", "tier": "specialist", "min_per_phc": 0},
    "STAFF_NURSE": {"title": "Staff Nurse (GNM / B.Sc)", "tier": "nurse", "min_per_phc": 1},
    "LAB_TECHNICIAN": {"title": "Laboratory Technician", "tier": "allied", "min_per_phc": 1},
    "PHARMACIST": {"title": "Chief Pharmacist", "tier": "pharmacy", "min_per_phc": 1},
}

# CAG Jharkhand Rpt 2/2022 & NITI Aayog District Hospital Studies:
# High unauthorized absence, proxy attendance, and ghost roster records.
PERSONNEL_CAG_ANCHORS = {
    "ghost_roster_worker": 0.075,   # employee listed on duty roster but transferred or resigned
    "proxy_punch_absence": 0.090,   # biometric/register proxy marked present by peer while absent
    "early_departure_deficit": 0.120, # left post after 1-2 hours; clinical service void
    "dual_practice_conflict": 0.040, # moonlighting during public duty hours
}

PERSONNEL_TRUST_FEATURES = (
    "t1_impossible_duplicate_punch",  # same employee punched at 2 facilities on same day
    "t1_zero_clinical_encounters",     # staff logged 8h duty but zero OPD/IPD events logged
    "t2_clinical_volume_per_hour",    # OPD patients + procedures / logged hours
    "t2_punch_timestamp_regularity",  # variance of sign-in minutes (0 variance = proxy batch)
    "t2_consecutive_days_unverified", # days since independent physical inspection/roll-call
    "t2_shift_fulfillment_ratio",     # physically observed hours / rostered hours
    "t2_round_number_hours_flag",     # shift hours exactly round 8.000 (biometric vs manual)
    "t2_peer_attendance_correlation", # correlation of absence with specific coworker
    "t2_facility_remote_hardship_z",  # distance/isolation index of posting
    "t2_historical_unauthorized_absence_rate", # trailing 90-day unexcused absence
)


# --------------------------------------------------------------------------
# Synthetic Personnel Generator (Truth vs Observed Roster)
# --------------------------------------------------------------------------

def generate_facility_personnel_duty(
    facility_id: str,
    facility_type: str,
    date: pd.Timestamp,
    rng: np.random.Generator,
    seed: int,
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """Generates synthetic personnel roster and attendance for one facility day."""
    role_quotas = {
        "DISTRICT_HOSPITAL": {"MEDICAL_OFFICER": 8, "SPECIALIST_OBGYN": 3, "SPECIALIST_PEDIA": 3, "STAFF_NURSE": 20, "LAB_TECHNICIAN": 4, "PHARMACIST": 3},
        "CHC": {"MEDICAL_OFFICER": 4, "SPECIALIST_OBGYN": 1, "SPECIALIST_PEDIA": 1, "STAFF_NURSE": 8, "LAB_TECHNICIAN": 2, "PHARMACIST": 2},
        "PHC": {"MEDICAL_OFFICER": 2, "SPECIALIST_OBGYN": 0, "SPECIALIST_PEDIA": 0, "STAFF_NURSE": 3, "LAB_TECHNICIAN": 1, "PHARMACIST": 1},
    }
    roster_cfg = role_quotas.get(facility_type, role_quotas["PHC"])
    
    truth_rows = []
    observed_rows = []
    
    emp_idx = 0
    for role, count in roster_cfg.items():
        for i in range(count):
            emp_idx += 1
            staff_id = f"EMP_{facility_id}_{role[:3]}_{i+1:02d}"
            
            # Ground truth physical presence
            roll = rng.random()
            is_ghost = (roll < PERSONNEL_CAG_ANCHORS["ghost_roster_worker"])
            is_proxy = (roll >= PERSONNEL_CAG_ANCHORS["ghost_roster_worker"] and
                        roll < PERSONNEL_CAG_ANCHORS["ghost_roster_worker"] + PERSONNEL_CAG_ANCHORS["proxy_punch_absence"])
            is_early_depart = (roll >= 0.165 and roll < 0.165 + PERSONNEL_CAG_ANCHORS["early_departure_deficit"])
            
            if is_ghost:
                true_present_hours = 0.0
                true_clinical_encounters = 0
                reported_present_hours = 8.0
                is_wrong = True
            elif is_proxy:
                true_present_hours = 0.0
                true_clinical_encounters = 0
                reported_present_hours = 8.0
                is_wrong = True
            elif is_early_depart:
                true_present_hours = float(rng.uniform(1.0, 2.5))
                true_clinical_encounters = int(rng.poisson(true_present_hours * 3.0))
                reported_present_hours = 8.0
                is_wrong = True
            else:
                true_present_hours = float(rng.uniform(7.5, 8.5))
                true_clinical_encounters = int(rng.poisson(true_present_hours * 4.5))
                reported_present_hours = round(true_present_hours, 1)
                is_wrong = False
                
            # Materiality: discrepancy > 3 hours or completely absent ghost worker
            is_materially_wrong = is_wrong and (abs(reported_present_hours - true_present_hours) >= 3.0 or true_present_hours == 0.0)
            
            days_since_rollcall = float(rng.choice([1, 3, 7, 14, 30, 60], p=[0.25, 0.30, 0.20, 0.15, 0.07, 0.03]))
            punch_minute_noise = 0.0 if (is_proxy or is_ghost) else float(rng.uniform(1.0, 15.0))
            
            truth_rec = {
                "date": date, "facility_id": facility_id, "staff_id": staff_id, "role": role,
                "true_present_hours": true_present_hours,
                "true_clinical_encounters": true_clinical_encounters,
                "is_materially_wrong": int(is_materially_wrong),
                "is_ghost_worker": int(is_ghost),
            }
            
            observed_rec = {
                "date": date, "facility_id": facility_id, "facility_type": facility_type,
                "staff_id": staff_id, "role": role,
                "reported_present_hours": reported_present_hours,
                "logged_clinical_encounters": true_clinical_encounters,
                "days_since_rollcall": days_since_rollcall,
                "punch_minute_variance": punch_minute_noise,
                "is_remote_posting": 1.0 if facility_type == "PHC" else 0.0,
            }
            
            truth_rows.append(truth_rec)
            observed_rows.append(observed_rec)
            
    return truth_rows, observed_rows


def generate_personnel_dataset(
    facility_ids: List[str],
    facility_types: Dict[str, str],
    start_date: str = "2026-06-01",
    days: int = 30,
    seed: int = 1001,
) -> Tuple[pd.DataFrame, pd.DataFrame]:
    """Generates synthetic district personnel dataset across dates."""
    rng = np.random.default_rng(seed)
    dates = pd.date_range(start_date, periods=days, freq="D")
    
    all_truth = []
    all_observed = []
    
    for d in dates:
        for f_id in facility_ids:
            f_type = facility_types.get(f_id, "PHC")
            t_rows, o_rows = generate_facility_personnel_duty(f_id, f_type, d, rng, seed)
            all_truth.extend(t_rows)
            all_observed.extend(o_rows)
            
    return pd.DataFrame(all_truth), pd.DataFrame(all_observed)


# --------------------------------------------------------------------------
# Personnel Trust Features (Causal & Observed-Data ONLY)
# --------------------------------------------------------------------------

def extract_personnel_trust_features(df_obs: pd.DataFrame) -> pd.DataFrame:
    """Extracts 10 strictly observed features for personnel attendance verification."""
    df = df_obs.copy()
    
    # 1. Impossible duplicate punch: flagged if duplicate records exist for staff_id on same day
    dup_counts = df.groupby(["date", "staff_id"])["facility_id"].transform("nunique")
    df["t1_impossible_duplicate_punch"] = (dup_counts > 1).astype(float)
    
    # 2. Zero clinical encounters during reported shift
    df["t1_zero_clinical_encounters"] = (
        (df["reported_present_hours"] >= 4.0) & (df["logged_clinical_encounters"] == 0)
    ).astype(float)
    
    # 3. Clinical volume per hour
    df["t2_clinical_volume_per_hour"] = (
        df["logged_clinical_encounters"] / df["reported_present_hours"].clip(lower=1.0)
    ).clip(0.0, 20.0)
    
    # 4. Punch timestamp regularity (low variance = proxy batch signature)
    df["t2_punch_timestamp_regularity"] = (df["punch_minute_variance"] < 1.0).astype(float)
    
    # 5. Consecutive days unverified
    df["t2_consecutive_days_unverified"] = df["days_since_rollcall"].clip(0.0, 90.0)
    
    # 6. Shift fulfillment ratio vs standard 8h
    df["t2_shift_fulfillment_ratio"] = (df["reported_present_hours"] / 8.0).clip(0.0, 2.0)
    
    # 7. Exactly round number hours flag
    df["t2_round_number_hours_flag"] = (df["reported_present_hours"] % 1.0 == 0.0).astype(float)
    
    # 8. Peer attendance correlation proxy
    df["t2_peer_attendance_correlation"] = 0.5 # constant neutral in single-slice extraction
    
    # 9. Remote posting hardship
    df["t2_facility_remote_hardship_z"] = df["is_remote_posting"].astype(float)
    
    # 10. Historical unexcused absence rate proxy
    df["t2_historical_unauthorized_absence_rate"] = (df["t2_consecutive_days_unverified"] > 14.0).astype(float) * 0.25
    
    return df[list(PERSONNEL_TRUST_FEATURES)]


# --------------------------------------------------------------------------
# Personnel Trust Scorer
# --------------------------------------------------------------------------

class PersonnelTrustScorer:
    """Gradient boosting model to detect ghost workers and proxy attendance."""
    
    def __init__(self, n_estimators: int = 150, max_depth: int = 4, learning_rate: float = 0.04):
        self.model = GradientBoostingClassifier(
            n_estimators=n_estimators, max_depth=max_depth,
            learning_rate=learning_rate, subsample=0.8, random_state=42
        )
        self.fitted = False
        
    def fit(self, df_obs: pd.DataFrame, df_truth: pd.DataFrame) -> "PersonnelTrustScorer":
        X = extract_personnel_trust_features(df_obs).to_numpy(dtype=float)
        y = df_truth["is_materially_wrong"].to_numpy(dtype=int)
        self.model.fit(X, y)
        self.fitted = True
        return self
        
    def predict_p_wrong(self, df_obs: pd.DataFrame) -> np.ndarray:
        if not self.fitted:
            raise RuntimeError("PersonnelTrustScorer must be fitted before predict")
        X = extract_personnel_trust_features(df_obs).to_numpy(dtype=float)
        return self.model.predict_proba(X)[:, 1]


# --------------------------------------------------------------------------
# Staff Redeployment Optimizer (CP-SAT Multi-Facility Optimization)
# --------------------------------------------------------------------------

@dataclass(frozen=True)
class PersonnelFacilityState:
    facility_id: str
    facility_name: str
    role: str
    rostered_staff: int
    verified_present_staff: int
    p_wrong: float
    is_verified: bool
    required_statutory_floor: int
    surge_deficit: int
    x_km: float
    y_km: float


@dataclass(frozen=True)
class StaffRedeploymentTransfer:
    origin_facility_id: str
    destination_facility_id: str
    role: str
    staff_count: int
    distance_km: float
    estimated_travel_hours: float
    donor_retained_staff: int
    rationale: str


@dataclass
class StaffRedeploymentPlan:
    plan_id: str
    timestamp: str
    role: str
    transfers: List[StaffRedeploymentTransfer]
    total_staff_redeployed: int
    rejected_donors: List[Dict[str, Any]]
    total_travel_km: float
    solver_status: str


def solve_staff_redeployment(
    role: str,
    facilities: List[PersonnelFacilityState],
    max_travel_km: float = 75.0,
    p_wrong_donor_threshold: float = 0.35,
) -> StaffRedeploymentPlan:
    """OR-Tools CP-SAT formulation for emergency inter-facility staff redeployment.
    
    DOCTRINE:
    - Never redeploy staff from an unverified donor with high phantom/ghost probability (p_wrong > 0.35).
    - Strictly enforce donor statutory safety floor: donor must retain >= required_statutory_floor staff.
    - Match donor surpluses with destination clinical surge deficits.
    """
    from ortools.sat.python import cp_model
    
    plan_id = new_id("plan_staff_redep")
    model = cp_model.CpModel()
    
    donors = []
    recipients = []
    rejected_donors = []
    
    for f in facilities:
        if f.role != role:
            continue
            
        if f.surge_deficit > 0:
            recipients.append(f)
        else:
            # Donor eligibility check
            if f.p_wrong > p_wrong_donor_threshold and not f.is_verified:
                rejected_donors.append({
                    "facility_id": f.facility_id,
                    "reason": f"VERIFICATION_GATE_REJECTED: High ghost-worker risk (p_wrong={f.p_wrong:.2f} > {p_wrong_donor_threshold})",
                    "available_for_redeploy": 0,
                })
                continue
                
            # Donor surplus above mandatory statutory floor
            effective_present = f.verified_present_staff if f.is_verified else f.rostered_staff
            surplus = max(0, effective_present - f.required_statutory_floor)
            if surplus <= 0:
                rejected_donors.append({
                    "facility_id": f.facility_id,
                    "reason": f"STATUTORY_SAFETY_FLOOR_RETAINED: Facility at {effective_present} staff; floor is {f.required_statutory_floor}",
                    "available_for_redeploy": 0,
                })
                continue
                
            donors.append((f, surplus))
            
    if not donors or not recipients:
        return StaffRedeploymentPlan(
            plan_id=plan_id, timestamp=now(), role=role, transfers=[],
            total_staff_redeployed=0, rejected_donors=rejected_donors,
            total_travel_km=0.0, solver_status="NO_VIABLE_TRANSFERS"
        )
        
    # Variables: x[donor_idx, recipient_idx]
    flow_vars = {}
    valid_pairs = []
    
    for d_idx, (d_fac, surplus) in enumerate(donors):
        for r_idx, r_fac in enumerate(recipients):
            dist = math.hypot(d_fac.x_km - r_fac.x_km, d_fac.y_km - r_fac.y_km)
            if dist <= max_travel_km:
                max_transfer = min(surplus, r_fac.surge_deficit)
                var = model.NewIntVar(0, max_transfer, f"x_{d_idx}_{r_idx}")
                flow_vars[(d_idx, r_idx)] = var
                valid_pairs.append((d_idx, r_idx, dist))
                
    if not valid_pairs:
        return StaffRedeploymentPlan(
            plan_id=plan_id, timestamp=now(), role=role, transfers=[],
            total_staff_redeployed=0, rejected_donors=rejected_donors,
            total_travel_km=0.0, solver_status="DISTANCE_RADIUS_EXCEEDED"
        )
        
    # Constraints:
    # 1. Outflow from donor <= surplus
    for d_idx, (d_fac, surplus) in enumerate(donors):
        donor_outflows = [flow_vars[(d_idx, r_idx)] for (di, r_idx, _) in valid_pairs if di == d_idx]
        if donor_outflows:
            model.Add(sum(donor_outflows) <= surplus)
            
    # 2. Inflow to recipient <= surge_deficit
    for r_idx, r_fac in enumerate(recipients):
        rec_inflows = [flow_vars[(d_idx, r_idx)] for (d_idx, ri, _) in valid_pairs if ri == r_idx]
        if rec_inflows:
            model.Add(sum(rec_inflows) <= r_fac.surge_deficit)
            
    # Objective: Maximize staff redeployed to cover deficit, penalize travel distance
    obj_terms = []
    for (d_idx, r_idx, dist) in valid_pairs:
        dist_cost = int(round(dist * 20))
        obj_terms.append(flow_vars[(d_idx, r_idx)] * (10000 - dist_cost))
        
    model.Maximize(sum(obj_terms))
    
    solver = cp_model.CpSolver()
    solver.parameters.num_search_workers = 1  # Deterministic, avoids pthread conflicts on mac
    solver.parameters.max_time_in_seconds = 5.0
    status = solver.Solve(model)
    
    transfers = []
    total_redeployed = 0
    total_km = 0.0
    
    if status in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        for (d_idx, r_idx, dist) in valid_pairs:
            qty = solver.Value(flow_vars[(d_idx, r_idx)])
            if qty > 0:
                d_fac, surplus = donors[d_idx]
                r_fac = recipients[r_idx]
                retained = (d_fac.verified_present_staff if d_fac.is_verified else d_fac.rostered_staff) - qty
                travel_hours = round(dist / 45.0, 2)
                transfers.append(StaffRedeploymentTransfer(
                    origin_facility_id=d_fac.facility_id,
                    destination_facility_id=r_fac.facility_id,
                    role=role,
                    staff_count=qty,
                    distance_km=round(dist, 1),
                    estimated_travel_hours=travel_hours,
                    donor_retained_staff=retained,
                    rationale=f"Redeployed {qty} {role} to cover deficit ({qty}/{r_fac.surge_deficit} needed); donor retains {retained} (floor: {d_fac.required_statutory_floor})",
                ))
                total_redeployed += qty
                total_km += dist * qty
                
    return StaffRedeploymentPlan(
        plan_id=plan_id,
        timestamp=now(),
        role=role,
        transfers=transfers,
        total_staff_redeployed=total_redeployed,
        rejected_donors=rejected_donors,
        total_travel_km=round(total_km, 1),
        solver_status="OPTIMAL" if status == cp_model.OPTIMAL else "FEASIBLE",
    )
