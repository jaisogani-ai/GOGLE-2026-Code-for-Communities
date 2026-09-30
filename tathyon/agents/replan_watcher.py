"""Agent 4 — Replan Watcher. Detects approved plans that can no longer be honoured.

Triggers: shipment delay, partial receipt, a count that contradicts a donor,
new shortage, or an explicit run. It may flag REPLAN_REQUIRED (through the
deterministic feasibility monitor) and PROPOSE a replan candidate. The
candidate enters the Approval queue; it cannot be approved or dispatched here.
"""
from __future__ import annotations

from pydantic import BaseModel, Field

from .base import AgentOutput, AgentSpec, BoundedAgent, NoArgs, RunContext, Statement, Tool


class PlanIdArgs(BaseModel):
    plan_id: str = Field(min_length=1, max_length=60)


class ReplanWatcher(BoundedAgent):
    SPEC = AgentSpec(
        name="replan_watcher", title="Replan Watcher",
        trigger="After shipment delays, receipts and counts; or on demand from the Outcome view.",
        purpose="Detect approved plans made infeasible by delays, partial receipts, donor stock changes, "
                "new shortages or verification results, and draft a replan candidate.",
        forbidden_actions=("approve a plan", "dispatch", "cancel shipments", "change quantities"),
        human_boundary="A replan candidate is a PROPOSED plan; only the district medical officer can approve it.",
        failure_behavior="If candidate generation fails (e.g. a plan is already pending), it reports the reason "
                         "and leaves the plan in REPLAN_REQUIRED.",
        max_tool_calls=10,
    )

    def tools(self) -> list[Tool]:
        ws = self.ws

        def active(_: NoArgs) -> dict:
            return {"plans": [{"plan_id": p["plan_id"], "sku": p["sku"], "version": p["version"],
                               "status": p["status"], "event_id": p["history"][-1],
                               "has_child": any(c.get("parent_plan_id") == p["plan_id"] for c in ws.plans.values())}
                              for p in ws.plans.values() if p["status"] in ("APPROVED", "REPLAN_REQUIRED", "PROPOSED")]}

        def shipments(_: NoArgs) -> dict:
            return {"shipments": [{k: s.get(k) for k in ("shipment_id", "from_facility", "to_facility", "qty", "status",
                                                          "eta", "received_qty", "damaged_qty")}
                                  | {"event_id": s["events"][-1]} for s in ws.shipments.values()]}

        def attestations(_: NoArgs) -> dict:
            return {"attestations": [{"facility_id": k[0], "sku": k[1], "usable_qty": a["usable_qty"],
                                      "finding": a.get("finding"), "observed_at": a["observed_at"],
                                      "event_id": a["event_id"]} for k, a in ws.attestations.items()]}

        def flag(_: NoArgs) -> dict:
            flagged = ws.check_feasibility(actor="agent:replan_watcher")
            return {"flagged": flagged}

        def candidate(a: PlanIdArgs) -> dict:
            plan = ws.plans.get(a.plan_id)
            if plan is None or plan["status"] != "REPLAN_REQUIRED":
                return {"error": "PLAN_NOT_IN_REPLAN_REQUIRED"}
            new = ws.propose_plan(plan["sku"], actor="agent:replan_watcher", parent_plan_id=a.plan_id,
                                  trigger="REPLAN_WATCHER")
            return {"candidate_plan_id": new["plan_id"], "recommended_option": new["recommended_option"],
                    "event_id": new["event_id"], "status": "PROPOSED_AWAITING_HUMAN_DECISION"}

        return [
            Tool("get_active_plans", "Plans that are proposed, approved or need a replan.", NoArgs, active),
            Tool("get_shipments", "Shipment statuses (status updates only; no GPS).", NoArgs, shipments),
            Tool("get_latest_attestations", "Latest human counts and findings.", NoArgs, attestations),
            Tool("evaluate_plan_feasibility", "Read-only feasibility check of one plan.", PlanIdArgs,
                 lambda a: ws.evaluate_feasibility(a.plan_id)),
            Tool("flag_replan_required", "Run the deterministic monitor (writes REPLAN_REQUIRED only if infeasible).",
                 NoArgs, flag, effect="PROPOSE"),
            Tool("generate_replan_candidate", "Propose a new plan for a REPLAN_REQUIRED plan.", PlanIdArgs,
                 candidate, effect="PROPOSE"),
        ]

    def plan_deterministic(self, request: str, ctx: RunContext) -> list[tuple[str, dict]]:
        return [("flag_replan_required", {}), ("get_active_plans", {}), ("get_shipments", {})]

    def compose_deterministic(self, request: str, ctx: RunContext) -> AgentOutput:
        st: list[Statement] = []
        for f in ctx.results.get("flag_replan_required", {}).get("flagged", []):
            codes = ", ".join(sorted({p["code"] for p in f["problems"]}))
            st.append(Statement(text=f"REPLAN_REQUIRED flagged now for {f['plan_id']}: {codes}.",
                                event_ids=[f["event_id"]]))
        plans = ctx.results.get("get_active_plans", {}).get("plans", [])
        for p in plans:
            if p["status"] == "REPLAN_REQUIRED" and not p["has_child"]:
                res = self._call("generate_replan_candidate", {"plan_id": p["plan_id"]}, ctx)
                if "candidate_plan_id" in res:
                    st.append(Statement(text=f"Plan v{p['version']} needs a replan; drafted candidate "
                                             f"{res['candidate_plan_id']} recommending {res['recommended_option']}. "
                                             "Awaiting the district medical officer.",
                                        event_ids=[p["event_id"], res["event_id"]]))
                else:
                    st.append(Statement(text=f"Plan v{p['version']} needs a replan; no candidate drafted "
                                             f"({res.get('error')}).", event_ids=[p["event_id"]]))
            elif p["status"] == "REPLAN_REQUIRED":
                st.append(Statement(text=f"Plan v{p['version']} needs a replan; a candidate already exists.",
                                    event_ids=[p["event_id"]]))
            elif p["status"] == "APPROVED":
                st.append(Statement(text=f"Plan v{p['version']} is still feasible.", event_ids=[p["event_id"]]))
        for s in ctx.results.get("get_shipments", {}).get("shipments", []):
            if s["status"] in ("DELAYED", "RECEIVED_PARTIAL"):
                st.append(Statement(text=f"Shipment {s['from_facility']}→{s['to_facility']} is {s['status']}"
                                         f" (ETA {s['eta']}).", event_ids=[s["event_id"]]))
        if not st:
            return AgentOutput(answer="No approved plan to watch yet.", statements=[])
        return AgentOutput(answer="Watcher sweep complete. Candidates, if any, are in the Approval queue.",
                           statements=st)
