"""Agent 3 — Resilience Analyst. Explains multi-facility risk and runs what-ifs in a sandbox.

It may propose analysis. It never executes allocation: run_scenario solves the
same CP-SAT model on a sandbox copy whose events never reach the real ledger.
"""
from __future__ import annotations

import re
from typing import Optional

from pydantic import BaseModel, Field

from . import readers
from .base import AgentOutput, AgentSpec, BoundedAgent, RunContext, Statement, Tool, cite


class SkuArgs(BaseModel):
    sku: Optional[str] = Field(default=None, max_length=40)


class ResourceArgs(BaseModel):
    facility_id: str = Field(min_length=1, max_length=60)
    sku: str = Field(min_length=1, max_length=40)


class ScenarioArgs(BaseModel):
    sku: str = Field(min_length=1, max_length=40)
    counted_facility_id: Optional[str] = Field(default=None, max_length=60)
    counted_usable_qty: Optional[float] = Field(default=None, ge=0, le=1_000_000)
    demand_multiplier: float = Field(default=1.0, ge=0.5, le=3.0)


class PlanArgs(BaseModel):
    plan_id: Optional[str] = Field(default=None, max_length=60)


def _run_scenario(ws, a: ScenarioArgs) -> dict:
    sb = ws.sandbox()
    notes = []
    if a.counted_facility_id and a.counted_usable_qty is not None:
        sb.submit_attestation(a.counted_facility_id, a.sku, attester_id="hypothetical-count",
                              attester_role="field_verifier", present_qty=a.counted_usable_qty,
                              usable_qty=a.counted_usable_qty, provenance="HYPOTHETICAL")
        notes.append(f"Assumed a count of {a.counted_usable_qty:g} usable at {a.counted_facility_id}.")
    if a.demand_multiplier != 1.0:
        sb.reports = {k: ({**v, "daily_consumption": v["daily_consumption"] * a.demand_multiplier}
                          if k[1] == a.sku else v) for k, v in sb.reports.items()}
        notes.append(f"Assumed demand x{a.demand_multiplier:g} for {a.sku}.")
    sb.plans = {pid: {**p, "status": "SANDBOX_IGNORED"} if p["status"] == "PROPOSED" else p
                for pid, p in sb.plans.items()}
    plan = sb.propose_plan(a.sku, actor="agent:resilience_analyst:sandbox")
    return {"provenance": "HYPOTHETICAL_SANDBOX_NOT_PERSISTED", "assumptions": notes,
            "needs": plan["needs"], "recommended_option": plan["recommended_option"],
            "rule": plan["recommendation_rule"],
            "options": [{k: o[k] for k in ("option", "fulfilled_qty", "shortfall_qty", "qty_weighted_travel_hours")}
                        for o in plan["options"]],
            "based_on_event_ids": [r["event_id"] for k, r in ws.reports.items() if k[1] == a.sku]}


COUNT_RE = re.compile(r"\b((?:PHC|CHC|DWH|DH|SC)-[A-Z0-9]+)\b\D{0,30}?(\d+(?:\.\d+)?)\s*(?:usable|units|ampoules|vials)?",
                      re.I)
DEMAND_RE = re.compile(r"demand\D{0,20}?(?:x\s*(\d+(?:\.\d+)?)|(\d+(?:\.\d+)?)\s*%)", re.I)


def _parse_what_if(request: str) -> Optional[dict]:
    if not re.search(r"\bwhat\s+if\b|\bif\b|\bassum", request, re.I):
        return None
    out: dict = {}
    m = COUNT_RE.search(request)
    if m:
        out.update(counted_facility_id=m.group(1).upper(), counted_usable_qty=float(m.group(2)))
    d = DEMAND_RE.search(request)
    if d:
        mult = float(d.group(1)) if d.group(1) else 1 + float(d.group(2)) / 100
        out["demand_multiplier"] = min(max(mult, 0.5), 3.0)
    return out or None


class ResilienceAnalyst(BoundedAgent):
    SPEC = AgentSpec(
        name="resilience_analyst", title="Resilience Analyst",
        trigger="An officer asks why facilities are at risk, or requests a what-if before approving.",
        purpose="Explain which facilities are at risk and why, which resources are constrained, what options "
                "exist, which assumptions matter, and what changed since the previous cycle.",
        forbidden_actions=("execute allocation", "approve", "write to the ledger outside the sandbox",
                           "set quantities"),
        human_boundary="All optimisation is the deterministic CP-SAT solve; the officer decides.",
        failure_behavior="Sandbox failures are reported as tool errors; nothing is persisted.",
        max_tool_calls=7,
    )

    def tools(self) -> list[Tool]:
        ws = self.ws

        def case_set(a: SkuArgs) -> dict:
            skus = [a.sku] if a.sku else sorted(ws.skus)
            return {"environment": ws.environment, "as_of": ws.clock.isoformat(),
                    "cases": [{**n, "report_event_id": ws.reports[(n["facility_id"], n["sku"])]["event_id"]}
                              for s in skus for n in ws.needs(s)]}

        def forecast(a: ResourceArgs) -> dict:
            st = ws.resource_state(a.facility_id, a.sku)
            return {"facility_id": a.facility_id, "sku": a.sku, "daily_consumption": st["daily_consumption"],
                    "runway_days": st["runway_days"], "projected_failure_at": st["projected_failure_at"],
                    "method": "Period-average issues from the stock export; no multi-period history is loaded, "
                              "so no seasonal or intermittent-demand model is fitted.",
                    "report_event_id": st["report_event_id"]}

        def constraints(a: SkuArgs) -> dict:
            out = {"policy": ws.policy.__dict__, "rejected_donors": [], "plan_event_id": None}
            try:
                pv = readers.plan_view(ws, sku=a.sku)
                out.update(rejected_donors=pv["rejected_donors"], plan_event_id=pv["proposed_event_id"])
            except Exception:
                pass
            return out

        def compare(a: PlanArgs) -> dict:
            cur = readers.plan_view(ws, a.plan_id)
            prev = readers.plan_view(ws, cur["parent_plan_id"]) if cur["parent_plan_id"] else None
            return {"current": cur, "previous": prev}

        return [
            Tool("get_case_set", "All facilities below alert runway, per SKU.", SkuArgs, case_set),
            Tool("get_resource_state", "Current state of one facility/SKU.", ResourceArgs,
                 lambda a: ws.resource_state(a.facility_id, a.sku)),
            Tool("get_forecast", "Runway projection and its method.", ResourceArgs, forecast),
            Tool("get_network_constraints", "Planning policy and donor rejections.", SkuArgs, constraints),
            Tool("run_scenario", "What-if on a sandbox copy; never persisted.", ScenarioArgs,
                 lambda a: _run_scenario(ws, a)),
            Tool("compare_options", "Current plan options vs the previous cycle.", PlanArgs, compare),
        ]

    def plan_deterministic(self, request: str, ctx: RunContext) -> list[tuple[str, dict]]:
        calls: list[tuple[str, dict]] = [("get_case_set", {})]
        if self.ws.plans:
            calls += [("compare_options", {}), ("get_network_constraints", {"sku": self._sku()})]
        what_if = _parse_what_if(request)
        if what_if and self._sku():
            calls.append(("run_scenario", {"sku": self._sku(), **what_if}))
        return calls

    def _sku(self) -> Optional[str]:
        plans = sorted(self.ws.plans.values(), key=lambda p: p["version"])
        return plans[-1]["sku"] if plans else (sorted(self.ws.skus)[0] if self.ws.skus else None)

    def compose_deterministic(self, request: str, ctx: RunContext) -> AgentOutput:
        st: list[Statement] = []
        cs = ctx.results.get("get_case_set", {})
        for c in cs.get("cases", [])[:6]:
            st.append(Statement(text=f"{c['facility_id']} {c['sku']} is at risk: runway {c['runway_days']} d below the "
                                     f"alert; needs {c['shortfall']:g} units to reach the planning horizon.",
                                event_ids=[c["report_event_id"]]))
        cmp_ = ctx.results.get("compare_options")
        if cmp_ and "error" not in cmp_:
            cur, prev = cmp_["current"], cmp_["previous"]
            for o in cur["options"]:
                st.append(Statement(text=f"Option {o['option']}: fulfils {o['fulfilled_qty']:g}, shortfall "
                                         f"{o['shortfall_qty']:g}, qty-weighted travel {o['qty_weighted_travel_hours']:g} h. "
                                         f"{o['why']}", event_ids=[cur["proposed_event_id"]]))
            if prev:
                gone = {l["from_facility"] for l in prev["lines"]} - {l["from_facility"] for l in cur["lines"]}
                st.append(Statement(text=f"Since v{prev['version']} ({prev['status']}): donors removed "
                                         f"{sorted(gone) or 'none'}; recommendation changed from "
                                         f"{prev['recommended_option']} to {cur['recommended_option']}.",
                                    event_ids=cite(prev["proposed_event_id"], cur["proposed_event_id"])))
        nc = ctx.results.get("get_network_constraints")
        if nc and nc.get("plan_event_id"):
            reasons = sorted({f"{d['facility_id']}:{d['reason']}" for d in nc["rejected_donors"]})
            st.append(Statement(text=f"Binding constraints: donor floor {nc['policy']['donor_floor_days']:g} d, "
                                     f"max travel {nc['policy']['max_travel_hours']:g} h, cold chain. "
                                     f"Rejected donors: {', '.join(reasons) or 'none'}.",
                                event_ids=[nc["plan_event_id"]]))
        sc = ctx.results.get("run_scenario")
        if sc and "error" not in sc and sc.get("based_on_event_ids"):
            st.append(Statement(text=f"What-if ({'; '.join(sc['assumptions']) or 'no change'}): the solver would "
                                     f"recommend {sc['recommended_option']} ({sc['rule']}). Not persisted.",
                                event_ids=sc["based_on_event_ids"][:6]))
        if not st:
            return AgentOutput(answer="No facility is below the alert runway and no plan exists yet.", statements=[])
        return AgentOutput(answer=f"Risk analysis over the {self.ws.environment} workspace. Key assumption: "
                                  "unverified reports are not supply until counted.", statements=st[:14])
