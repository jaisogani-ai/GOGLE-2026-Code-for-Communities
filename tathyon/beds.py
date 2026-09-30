"""TATHYON Beds Module — Capacity, Occupancy, Trust Scoring & Patient Diversion.

Build with AI: Code for Communities 2.0 (Track 3) — Smart Health & Supply Chain Resilience.

DOCTRINES & PHYSICAL INVARIANTS:
1. BEDS DO NOT MOVE: When a facility faces bed saturation or emergency surge,
   the allocator DIVERTS PATIENTS to nearest verified facilities with available beds.
2. AI NEVER COUNTS: Humans verify ward occupancy; Gemini only digitizes census sheets.
3. PHANTOM BED TRAP: Directing an acute patient to an unverified "free" bed risks
   ambulance turnaround deaths when the bed is broken, unstaffed, or already occupied.
4. RECIPIENT SAFETY CEILING: Recipient facilities must never be loaded beyond 85-90%
   safe occupancy, preserving a local emergency buffer.
5. ALL LABELS COME FROM THE GENERATOR: Truth and observed are generated separately;
   features read observed data ONLY (past-only, causal).
"""
from __future__ import annotations

import math
from dataclasses import dataclass, asdict, field
from typing import Any, Dict, List, Optional, Tuple
import numpy as np
import pandas as pd
from sklearn.ensemble import GradientBoostingClassifier
from sklearn.metrics import average_precision_score, roc_auc_score

from .schema import ResourceType, FacilityType, now, new_id


# --------------------------------------------------------------------------
# Bed Types & CAG Failure Anchors
# --------------------------------------------------------------------------

BED_TYPES = {
    "GEN_MALE": {"name": "General Male Ward Bed", "acuity": 1, "target_turnover_days": 4.0},
    "GEN_FEMALE": {"name": "General Female Ward Bed", "acuity": 1, "target_turnover_days": 4.0},
    "MATERNITY": {"name": "Maternity & Obstetric Bed", "acuity": 2, "target_turnover_days": 2.5},
    "PEDIATRIC": {"name": "Pediatric Ward Bed", "acuity": 2, "target_turnover_days": 3.5},
    "ICU_O2": {"name": "High-Dependency / ICU Oxygen Bed", "acuity": 3, "target_turnover_days": 5.0},
    "ISOLATION": {"name": "Infectious Isolation Bed", "acuity": 2, "target_turnover_days": 6.0},
}

# CAG Bihar Rpt 3/2021 Para 2.3 & CAG UP Rpt 5/2020:
# Reported available beds frequently unstaffed, broken, or contaminated.
BED_CAG_ANCHORS = {
    "phantom_free_bed": 0.082,      # reported vacant on paper, physically occupied or broken
    "unreported_maintenance": 0.065, # broken oxygen port/frame, not subtracted from available
    "reporting_lag_stale": 0.180,    # ward census not updated in > 24 hours
    "arithmetic_mismatch": 0.040,    # total != occupied + available + maintenance
}

BED_TRUST_FEATURES = (
    "t1_bed_arithmetic_violation",   # total != occupied + available + broken
    "t1_unreported_broken_now",      # maintenance reported 0 but previous workorder active
    "t2_admissions_delta_gap",       # |admissions - discharges - delta_occupied| / capacity
    "t2_turnover_anomaly",           # turnover rate z-score vs clinical baseline
    "t2_occupancy_ratio_reported",   # occupied / total capacity
    "t2_reported_free_beds",         # reported available beds
    "t2_hours_since_ward_census",    # staleness of attestation (hours)
    "t2_emergency_divert_rate_7d",   # trailing patient diversions from this ward
    "t2_weekend_plateau_flag",       # occupancy flatline across weekend without discharges
    "t2_bed_saturation_pressure",    # max(0, reported_occupancy - 0.85)
)


# --------------------------------------------------------------------------
# Synthetic Bed Generator (Anti-Leakage Ground Truth vs Observed)
# --------------------------------------------------------------------------

def generate_facility_bed_census(
    facility_id: str,
    facility_type: str,
    date: pd.Timestamp,
    rng: np.random.Generator,
    seed: int,
    outbreak_surge_factor: float = 1.0,
) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    """Generates one day's bed census for a facility across bed types.
    Returns (truth_dict, observed_dict) strictly separated."""
    # Base bed capacities by facility tier
    tier_capacities = {
        "DISTRICT_HOSPITAL": {"GEN_MALE": 40, "GEN_FEMALE": 40, "MATERNITY": 30, "PEDIATRIC": 25, "ICU_O2": 15, "ISOLATION": 10},
        "CHC": {"GEN_MALE": 12, "GEN_FEMALE": 12, "MATERNITY": 8, "PEDIATRIC": 6, "ICU_O2": 2, "ISOLATION": 2},
        "PHC": {"GEN_MALE": 3, "GEN_FEMALE": 3, "MATERNITY": 2, "PEDIATRIC": 0, "ICU_O2": 0, "ISOLATION": 0},
    }
    caps = tier_capacities.get(facility_type, tier_capacities["PHC"])
    
    truth_rows = []
    observed_rows = []
    
    for bed_type, total_beds in caps.items():
        if total_beds == 0:
            continue
        
        # Ground truth simulation
        base_occ_rate = rng.uniform(0.55, 0.85) * outbreak_surge_factor
        base_occ_rate = min(0.98, max(0.20, base_occ_rate))
        
        # True physical breakdown
        true_broken = int(rng.binomial(total_beds, 0.05))
        usable_capacity = max(1, total_beds - true_broken)
        true_occupied = int(min(usable_capacity, round(usable_capacity * base_occ_rate)))
        true_available = max(0, usable_capacity - true_occupied)
        
        # Simulated admissions & discharges today
        avg_los = BED_TYPES[bed_type]["target_turnover_days"]
        daily_turnover = usable_capacity / avg_los
        true_admissions = int(rng.poisson(daily_turnover))
        true_discharges = int(rng.poisson(daily_turnover * 0.95))
        
        # Pathology injection into OBSERVED report
        is_wrong = False
        reported_total = total_beds
        reported_occupied = true_occupied
        reported_available = true_available
        reported_broken = true_broken
        hours_since_census = float(rng.choice([2, 4, 8, 14, 26, 48], p=[0.4, 0.3, 0.15, 0.08, 0.05, 0.02]))
        
        roll = rng.random()
        if roll < BED_CAG_ANCHORS["phantom_free_bed"]:
            # Ward claims available beds that are already taken or unusable
            phantom_delta = int(rng.integers(2, max(3, int(total_beds * 0.35) + 1)))
            reported_available = min(total_beds - 1, true_available + phantom_delta)
            reported_occupied = max(0, total_beds - reported_available - reported_broken)
            is_wrong = True
        elif roll < BED_CAG_ANCHORS["phantom_free_bed"] + BED_CAG_ANCHORS["unreported_maintenance"]:
            # Broken beds hidden to avoid maintenance query
            reported_broken = 0
            reported_available = total_beds - reported_occupied
            is_wrong = (true_broken > 0)
        elif roll < BED_CAG_ANCHORS["phantom_free_bed"] + BED_CAG_ANCHORS["unreported_maintenance"] + BED_CAG_ANCHORS["arithmetic_mismatch"]:
            # Clerical error where total != occupied + available
            reported_available = max(0, true_available + 3)
            is_wrong = True
            
        # Materiality rule: free beds error > 15% of capacity or >= 2 critical beds
        free_bed_gap = abs(reported_available - true_available)
        is_materially_wrong = is_wrong and (free_bed_gap >= 2 or (free_bed_gap / max(total_beds, 1)) > 0.15)
        
        truth_rec = {
            "date": date, "facility_id": facility_id, "bed_type": bed_type,
            "total_beds": total_beds, "true_occupied": true_occupied,
            "true_available": true_available, "true_broken": true_broken,
            "true_admissions": true_admissions, "true_discharges": true_discharges,
            "is_materially_wrong": int(is_materially_wrong),
        }
        
        observed_rec = {
            "date": date, "facility_id": facility_id, "facility_type": facility_type,
            "bed_type": bed_type, "reported_total": reported_total,
            "reported_occupied": reported_occupied, "reported_available": reported_available,
            "reported_broken": reported_broken, "reported_admissions": true_admissions,
            "reported_discharges": true_discharges, "hours_since_census": hours_since_census,
            "acuity_level": BED_TYPES[bed_type]["acuity"],
        }
        
        truth_rows.append(truth_rec)
        observed_rows.append(observed_rec)
        
    return truth_rows, observed_rows


def generate_beds_dataset(
    facility_ids: List[str],
    facility_types: Dict[str, str],
    start_date: str = "2026-06-01",
    days: int = 60,
    seed: int = 1001,
    outbreak_surge_factor: float = 1.0,
) -> Tuple[pd.DataFrame, pd.DataFrame]:
    """Generates synthetic bed dataset for a district across time."""
    rng = np.random.default_rng(seed)
    dates = pd.date_range(start_date, periods=days, freq="D")
    
    all_truth = []
    all_observed = []
    
    for d in dates:
        for f_id in facility_ids:
            f_type = facility_types.get(f_id, "PHC")
            t_rows, o_rows = generate_facility_bed_census(
                f_id, f_type, d, rng, seed, outbreak_surge_factor=outbreak_surge_factor
            )
            all_truth.extend(t_rows)
            all_observed.extend(o_rows)
            
    df_truth = pd.DataFrame(all_truth)
    df_observed = pd.DataFrame(all_observed)
    return df_truth, df_observed


# --------------------------------------------------------------------------
# Bed Trust Feature Extraction (Causal & Observed-Data ONLY)
# --------------------------------------------------------------------------

def extract_bed_trust_features(df_obs: pd.DataFrame) -> pd.DataFrame:
    """Extracts 10 strictly observed causal features for bed reports."""
    df = df_obs.copy()
    
    # 1. Arithmetic violation: total == occupied + available + broken
    arithmetic_sum = df["reported_occupied"] + df["reported_available"] + df["reported_broken"]
    df["t1_bed_arithmetic_violation"] = (arithmetic_sum != df["reported_total"]).astype(float)
    
    # 2. Unreported broken beds: broken == 0 on high-wear acuity beds with high census age
    df["t1_unreported_broken_now"] = (
        (df["reported_broken"] == 0) & (df["acuity_level"] >= 3) & (df["hours_since_census"] >= 24.0)
    ).astype(float)
    
    # 3. Admissions vs delta occupancy gap
    # |admissions - discharges| / capacity (simplified daily flux check)
    net_flux = (df["reported_admissions"] - df["reported_discharges"]).abs()
    df["t2_admissions_delta_gap"] = (net_flux / df["reported_total"].clip(lower=1.0)).clip(0.0, 5.0)
    
    # 4. Turnover anomaly: admissions / capacity relative to target
    turnover = df["reported_admissions"] / df["reported_total"].clip(lower=1.0)
    df["t2_turnover_anomaly"] = (turnover - 0.25).abs().clip(0.0, 3.0)
    
    # 5. Occupancy ratio
    df["t2_occupancy_ratio_reported"] = (df["reported_occupied"] / df["reported_total"].clip(lower=1.0)).clip(0.0, 1.5)
    
    # 6. Raw reported free beds
    df["t2_reported_free_beds"] = df["reported_available"].astype(float)
    
    # 7. Hours since ward census
    df["t2_hours_since_ward_census"] = df["hours_since_census"].clip(0.0, 72.0)
    
    # 8. Emergency divert rate (synthetic trailing proxy)
    df["t2_emergency_divert_rate_7d"] = (df["t2_occupancy_ratio_reported"] > 0.90).astype(float) * 0.5
    
    # 9. Weekend plateau flag
    is_weekend = df["date"].dt.dayofweek.isin([5, 6])
    df["t2_weekend_plateau_flag"] = (is_weekend & (df["t2_occupancy_ratio_reported"] > 0.80)).astype(float)
    
    # 10. Bed saturation pressure
    df["t2_bed_saturation_pressure"] = (df["t2_occupancy_ratio_reported"] - 0.85).clip(lower=0.0)
    
    return df[list(BED_TRUST_FEATURES)]


# --------------------------------------------------------------------------
# Bed Trust Scorer
# --------------------------------------------------------------------------

class BedTrustScorer:
    """Gradient boosting trust model for hospital and clinic bed availability."""
    
    def __init__(self, n_estimators: int = 150, max_depth: int = 4, learning_rate: int = 0.04):
        self.model = GradientBoostingClassifier(
            n_estimators=n_estimators, max_depth=max_depth,
            learning_rate=learning_rate, subsample=0.8, random_state=42
        )
        self.fitted = False
        
    def fit(self, df_obs: pd.DataFrame, df_truth: pd.DataFrame) -> "BedTrustScorer":
        X = extract_bed_trust_features(df_obs).to_numpy(dtype=float)
        y = df_truth["is_materially_wrong"].to_numpy(dtype=int)
        self.model.fit(X, y)
        self.fitted = True
        return self
        
    def predict_p_wrong(self, df_obs: pd.DataFrame) -> np.ndarray:
        if not self.fitted:
            raise RuntimeError("BedTrustScorer must be fitted before predict")
        X = extract_bed_trust_features(df_obs).to_numpy(dtype=float)
        return self.model.predict_proba(X)[:, 1]


# --------------------------------------------------------------------------
# Patient Diversion Optimizer (Beds Stay Put; Patients Are Diverted)
# --------------------------------------------------------------------------

@dataclass(frozen=True)
class BedFacilityNode:
    facility_id: str
    facility_name: str
    bed_type: str
    total_beds: int
    reported_occupied: int
    reported_available: int
    p_wrong: float
    is_verified: bool
    x_km: float
    y_km: float


@dataclass(frozen=True)
class PatientDiversionRoute:
    origin_id: str
    destination_id: str
    bed_type: str
    patients_diverted: int
    distance_km: float
    travel_time_mins: float
    destination_safe_capacity_remaining: int
    rationale: str


@dataclass
class PatientDiversionPlan:
    plan_id: str
    timestamp: str
    routes: List[PatientDiversionRoute]
    diverted_patients_count: int
    rejected_destinations: List[Dict[str, Any]]
    total_travel_patient_km: float
    emergency_surge_active: bool
    solver_status: str


def solve_patient_diversion(
    surging_facility_id: str,
    bed_type: str,
    excess_patients_seeking_admission: int,
    candidate_facilities: List[BedFacilityNode],
    max_travel_km: float = 65.0,
    safe_occupancy_ceiling: float = 0.88,
    p_wrong_verification_threshold: float = 0.40,
) -> PatientDiversionPlan:
    """OR-Tools CP-SAT formulation for optimal emergency patient diversion.
    
    DOCTRINE:
    - Never divert patients to an unverified destination with high phantom probability (p_wrong > 0.40).
    - Never overload a receiving facility past safe_occupancy_ceiling (default 88%).
    - Minimize total patient transport ambulance distance.
    """
    from ortools.sat.python import cp_model
    
    plan_id = new_id("plan_bed_div")
    origin_node = next((n for n in candidate_facilities if n.facility_id == surging_facility_id and n.bed_type == bed_type), None)
    if not origin_node:
        return PatientDiversionPlan(
            plan_id=plan_id, timestamp=now(), routes=[], diverted_patients_count=0,
            rejected_destinations=[], total_travel_patient_km=0.0,
            emergency_surge_active=True, solver_status="ORIGIN_NOT_FOUND"
        )
        
    dest_candidates = [n for n in candidate_facilities if n.facility_id != surging_facility_id and n.bed_type == bed_type]
    
    model = cp_model.CpModel()
    flow_vars = {}
    rejected = []
    
    valid_dests = []
    for d in dest_candidates:
        # Distance calculation (Haversine/Euclidean km)
        dist = math.hypot(d.x_km - origin_node.x_km, d.y_km - origin_node.y_km)
        
        # Verification Gate Check
        if d.p_wrong > p_wrong_verification_threshold and not d.is_verified:
            rejected.append({
                "facility_id": d.facility_id,
                "reason": f"VERIFICATION_GATE_REJECTED: High phantom bed risk (p_wrong={d.p_wrong:.2f} > {p_wrong_verification_threshold})",
                "reported_available": d.reported_available,
            })
            continue
            
        if dist > max_travel_km:
            rejected.append({
                "facility_id": d.facility_id,
                "reason": f"TRAVEL_RADIUS_EXCEEDED: Distance {dist:.1f} km exceeds maximum safe transfer radius {max_travel_km} km",
                "reported_available": d.reported_available,
            })
            continue
            
        # Safe capacity headroom: max permissible patients without exceeding 88% occupancy
        safe_max_occupied = int(d.total_beds * safe_occupancy_ceiling)
        safe_headroom = max(0, safe_max_occupied - d.reported_occupied)
        available_cap = min(d.reported_available, safe_headroom)
        
        if available_cap <= 0:
            rejected.append({
                "facility_id": d.facility_id,
                "reason": f"SAFE_OCCUPANCY_CEILING_REACHED: Destination at {d.reported_occupied}/{d.total_beds} beds; safe headroom is 0",
                "reported_available": d.reported_available,
            })
            continue
            
        valid_dests.append((d, dist, available_cap))
        flow_vars[d.facility_id] = model.NewIntVar(0, available_cap, f"flow_{d.facility_id}")
        
    if not valid_dests:
        return PatientDiversionPlan(
            plan_id=plan_id, timestamp=now(), routes=[], diverted_patients_count=0,
            rejected_destinations=rejected, total_travel_patient_km=0.0,
            emergency_surge_active=True, solver_status="NO_VERIFIED_CAPACITY_AVAILABLE"
        )
        
    # Constraint: sum(diverted) <= excess_patients
    total_diverted_var = model.NewIntVar(0, excess_patients_seeking_admission, "total_diverted")
    model.Add(total_diverted_var == sum(flow_vars[d.facility_id] for d, _, _ in valid_dests))
    
    # Objective: Maximize patients diverted, penalize distance traveled
    # Scale distance to integer: 1 patient diverted = +10,000 points; 1 km travel = -50 points
    obj_terms = []
    for d, dist, _ in valid_dests:
        dist_cost = int(round(dist * 50))
        obj_terms.append(flow_vars[d.facility_id] * (10000 - dist_cost))
        
    model.Maximize(sum(obj_terms))
    
    solver = cp_model.CpSolver()
    solver.parameters.num_search_workers = 1  # Deterministic, avoids pthread conflicts on mac
    solver.parameters.max_time_in_seconds = 5.0
    status = solver.Solve(model)
    
    routes = []
    total_km = 0.0
    total_patients = 0
    
    if status in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        for d, dist, cap in valid_dests:
            qty = solver.Value(flow_vars[d.facility_id])
            if qty > 0:
                travel_time = round((dist / 40.0) * 60.0 + 10.0, 1) # 40 km/h avg rural speed + 10m staging
                remaining = cap - qty
                routes.append(PatientDiversionRoute(
                    origin_id=surging_facility_id,
                    destination_id=d.facility_id,
                    bed_type=bed_type,
                    patients_diverted=qty,
                    distance_km=round(dist, 1),
                    travel_time_mins=travel_time,
                    destination_safe_capacity_remaining=remaining,
                    rationale=f"Diverted {qty} patients to verified available beds ({qty}/{cap} slots used; destination buffer preserved)",
                ))
                total_km += dist * qty
                total_patients += qty
                
    return PatientDiversionPlan(
        plan_id=plan_id,
        timestamp=now(),
        routes=routes,
        diverted_patients_count=total_patients,
        rejected_destinations=rejected,
        total_travel_patient_km=round(total_km, 1),
        emergency_surge_active=True,
        solver_status="OPTIMAL" if status == cp_model.OPTIMAL else "FEASIBLE",
    )


# --------------------------------------------------------------------------
# Emergency Surge Early Warning System
# --------------------------------------------------------------------------

def evaluate_bed_surge_warnings(df_obs: pd.DataFrame, days_runway_threshold: float = 2.0) -> List[Dict[str, Any]]:
    """Evaluates bed saturation runway and generates early warning alerts."""
    warnings = []
    for (f_id, b_type), group in df_obs.groupby(["facility_id", "bed_type"]):
        latest = group.sort_values("date").iloc[-1]
        cap = max(1, latest["reported_total"])
        occ = latest["reported_occupied"]
        avail = latest["reported_available"]
        adm_rate = max(0.5, latest["reported_admissions"])
        
        occ_rate = occ / cap
        # Runway in days before 100% capacity is reached
        days_to_full = avail / adm_rate
        
        if occ_rate >= 0.90 or days_to_full <= days_runway_threshold:
            severity = "CRITICAL" if (occ_rate >= 0.95 or days_to_full <= 1.0) else "WARNING"
            warnings.append({
                "facility_id": f_id,
                "bed_type": b_type,
                "current_occupancy_pct": round(occ_rate * 100, 1),
                "available_beds": int(avail),
                "admissions_rate_daily": round(adm_rate, 1),
                "days_until_saturation": round(days_to_full, 2),
                "severity": severity,
                "action_recommended": "TRIGGER_PREVENTATIVE_PATIENT_DIVERSION" if severity == "CRITICAL" else "MONITOR_HOURLY",
            })
    return warnings
