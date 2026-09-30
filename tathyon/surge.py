"""TATHYON Emergency Surge Mode — Outbreak Multipliers, Warning Thresholds & Joint Prioritization.

Build with AI: Code for Communities 2.0 (Track 3) — Smart Health & Supply Chain Resilience.

DOCTRINES & CLINICAL LOGIC:
1. OUTBREAK MULTIPLIERS: Epidemics (Cholera, Dengue, Viral Respiratory, Mass Trauma) do not
   affect resources in isolation; they create correlated demand surges across medicines, beds, and staff.
2. TIGHTENED WARNING THRESHOLDS: During an active outbreak declaration, warning horizons
   expand from 7 days to 14 days, and minimum safety buffers increase by 50%.
3. JOINT PRIORITIZATION (The Synergistic Care Bottleneck):
   A health facility cannot treat an acute pneumonia case if it has an ICU bed but zero amoxicillin,
   or antibiotics but zero on-duty staff nurses. TATHYON evaluates joint multi-resource fragility.
"""
from __future__ import annotations

from dataclasses import dataclass, asdict, field
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple
import numpy as np
import pandas as pd

from .schema import ResourceType, now, new_id


class OutbreakType(str, Enum):
    CHOLERA_DIARRHEA = "CHOLERA_DIARRHEA"
    DENGUE_VECTOR = "DENGUE_VECTOR"
    RESPIRATORY_EPIDEMIC = "RESPIRATORY_EPIDEMIC"
    MONSOON_FLOOD_TRAUMA = "MONSOON_FLOOD_TRAUMA"


@dataclass(frozen=True)
class SurgeProfile:
    outbreak_type: OutbreakType
    medicine_multipliers: Dict[str, float]
    bed_multipliers: Dict[str, float]
    staff_multipliers: Dict[str, float]
    threshold_runway_days: float
    description: str


SURGE_PROFILES: Dict[OutbreakType, SurgeProfile] = {
    OutbreakType.CHOLERA_DIARRHEA: SurgeProfile(
        outbreak_type=OutbreakType.CHOLERA_DIARRHEA,
        medicine_multipliers={"ORS01": 4.5, "METRO4": 2.8, "INJDEX": 2.0},
        bed_multipliers={"GEN_MALE": 1.8, "GEN_FEMALE": 1.8, "PEDIATRIC": 2.5, "ISOLATION": 3.0},
        staff_multipliers={"STAFF_NURSE": 1.8, "MEDICAL_OFFICER": 1.4},
        threshold_runway_days=14.0,
        description="Acute watery diarrheal epidemic; extreme hydration & pediatric beds needed",
    ),
    OutbreakType.DENGUE_VECTOR: SurgeProfile(
        outbreak_type=OutbreakType.DENGUE_VECTOR,
        medicine_multipliers={"PCM500": 3.2, "INJDEX": 2.2},
        bed_multipliers={"GEN_MALE": 2.2, "GEN_FEMALE": 2.2, "ICU_O2": 3.0, "ISOLATION": 2.5},
        staff_multipliers={"LAB_TECHNICIAN": 2.5, "STAFF_NURSE": 1.6},
        threshold_runway_days=14.0,
        description="Vector-borne outbreak; high febrile admissions, platelet monitoring & HDU beds",
    ),
    OutbreakType.RESPIRATORY_EPIDEMIC: SurgeProfile(
        outbreak_type=OutbreakType.RESPIRATORY_EPIDEMIC,
        medicine_multipliers={"SALB": 3.8, "AMX250": 3.0, "CEFX500": 2.2, "INJDEX": 2.5},
        bed_multipliers={"ICU_O2": 4.0, "PEDIATRIC": 2.8, "ISOLATION": 3.5},
        staff_multipliers={"MEDICAL_OFFICER": 2.0, "STAFF_NURSE": 2.2},
        threshold_runway_days=18.0,
        description="Severe acute respiratory outbreak; pediatric pneumonia & ICU oxygen surge",
    ),
    OutbreakType.MONSOON_FLOOD_TRAUMA: SurgeProfile(
        outbreak_type=OutbreakType.MONSOON_FLOOD_TRAUMA,
        medicine_multipliers={"AMX250": 2.5, "ORS01": 3.0, "INJDEX": 3.0},
        bed_multipliers={"GEN_MALE": 2.5, "GEN_FEMALE": 2.5, "ICU_O2": 2.0},
        staff_multipliers={"MEDICAL_OFFICER": 2.0, "STAFF_NURSE": 2.0, "PHARMACIST": 1.5},
        threshold_runway_days=14.0,
        description="Monsoon disaster inundation; trauma emergencies, water-borne disease",
    ),
}


@dataclass
class FacilityJointFragility:
    facility_id: str
    facility_name: str
    medicine_stockout_risk: float   # 0 to 1
    bed_saturation_risk: float      # 0 to 1
    personnel_deficit_risk: float   # 0 to 1
    joint_fragility_score: float    # 0 to 1 (harmonic/geometric bottleneck)
    primary_bottleneck: str
    recommended_interventions: List[str]


def evaluate_joint_fragility(
    facility_id: str,
    facility_name: str,
    medicine_runway_days: float,
    bed_occupancy_pct: float,
    staff_deficit_count: int,
    active_outbreak: Optional[OutbreakType] = None,
) -> FacilityJointFragility:
    """Evaluates the multi-resource care bottleneck for a single facility under surge conditions."""
    profile = SURGE_PROFILES.get(active_outbreak) if active_outbreak else None
    runway_threshold = profile.threshold_runway_days if profile else 7.0
    
    # 1. Medicine risk: 0 (safe > threshold) to 1.0 (empty)
    med_risk = min(1.0, max(0.0, (runway_threshold - medicine_runway_days) / max(runway_threshold, 1.0)))
    
    # 2. Bed saturation risk: 0 (< 70% occupancy) to 1.0 (>= 95% occupancy)
    bed_risk = min(1.0, max(0.0, (bed_occupancy_pct - 70.0) / 25.0))
    
    # 3. Staff deficit risk: 0 (no deficit) to 1.0 (deficit >= 3 staff)
    staff_risk = min(1.0, max(0.0, staff_deficit_count / 3.0))
    
    # Joint Fragility: Bottleneck is driven by the weakest link in clinical care capability
    # Using weighted max + average to reflect both catastrophic single-point failure and compound strain
    joint_score = round(0.6 * max(med_risk, bed_risk, staff_risk) + 0.4 * ((med_risk + bed_risk + staff_risk) / 3.0), 3)
    
    risks = [("MEDICINE", med_risk), ("BEDS", bed_risk), ("PERSONNEL", staff_risk)]
    risks.sort(key=lambda x: -x[1])
    primary = risks[0][0]
    
    interventions = []
    if med_risk >= 0.5:
        interventions.append(f"EXPEDITE_STOCK_TRANSFER (Medicine runway: {medicine_runway_days:.1f}d < {runway_threshold:.0f}d threshold)")
    if bed_risk >= 0.5:
        interventions.append(f"PREPARE_PATIENT_DIVERSION (Bed occupancy: {bed_occupancy_pct:.1f}% >= safe threshold)")
    if staff_risk >= 0.5:
        interventions.append(f"REQUEST_STAFF_REDEPLOYMENT (Staff deficit: {staff_deficit_count} clinical workers)")
        
    return FacilityJointFragility(
        facility_id=facility_id,
        facility_name=facility_name,
        medicine_stockout_risk=round(med_risk, 3),
        bed_saturation_risk=round(bed_risk, 3),
        personnel_deficit_risk=round(staff_risk, 3),
        joint_fragility_score=joint_score,
        primary_bottleneck=primary,
        recommended_interventions=interventions,
    )


@dataclass
class EmergencySurgeResponsePlan:
    plan_id: str
    timestamp: str
    outbreak_type: OutbreakType
    district_fragility_index: float
    critical_facilities: List[FacilityJointFragility]
    demand_multipliers_applied: Dict[str, float]
    action_summary: str


def generate_emergency_surge_plan(
    outbreak_type: OutbreakType,
    facility_snapshots: List[Dict[str, Any]],
) -> EmergencySurgeResponsePlan:
    """Generates a comprehensive district-wide emergency surge plan combining all 3 resources."""
    profile = SURGE_PROFILES[outbreak_type]
    plan_id = new_id("plan_surge")
    
    fragilities: List[FacilityJointFragility] = []
    for f in facility_snapshots:
        frag = evaluate_joint_fragility(
            facility_id=f["facility_id"],
            facility_name=f["facility_name"],
            medicine_runway_days=f["medicine_runway_days"],
            bed_occupancy_pct=f["bed_occupancy_pct"],
            staff_deficit_count=f["staff_deficit_count"],
            active_outbreak=outbreak_type,
        )
        fragilities.append(frag)
        
    fragilities.sort(key=lambda x: -x.joint_fragility_score)
    mean_fragility = float(np.mean([fg.joint_fragility_score for fg in fragilities])) if fragilities else 0.0
    critical_facs = [fg for fg in fragilities if fg.joint_fragility_score >= 0.40]
    
    summary = (
        f"EMERGENCY SURGE ACTIVATED [{outbreak_type.value}]: {len(critical_facs)} facilities in high fragility. "
        f"Demand forecasts scaled by outbreak multipliers (e.g. {list(profile.medicine_multipliers.keys())[:2]}). "
        f"Warning horizons tightened to {profile.threshold_runway_days} days."
    )
    
    return EmergencySurgeResponsePlan(
        plan_id=plan_id,
        timestamp=now(),
        outbreak_type=outbreak_type,
        district_fragility_index=round(mean_fragility, 3),
        critical_facilities=critical_facs,
        demand_multipliers_applied={**profile.medicine_multipliers, **profile.bed_multipliers, **profile.staff_multipliers},
        action_summary=summary,
    )
