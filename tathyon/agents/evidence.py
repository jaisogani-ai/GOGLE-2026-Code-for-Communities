"""Agent 5 — Evidence / Audit Agent. Turns the ledger into a cited operational account.

Answers, for one SKU's case: what happened, why TATHYON recommended it, what
evidence supported it, what changed, why the original plan failed, and the
final outcome. Every sentence cites event ids; ungrounded sentences are dropped
by the harness.
"""
from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, Field

from . import readers
from .base import AgentOutput, AgentSpec, BoundedAgent, NoArgs, RunContext, Statement, Tool, cite


class SearchArgs(BaseModel):
    event_type: Optional[str] = Field(default=None, max_length=40)
    facility_id: Optional[str] = Field(default=None, max_length=60)
    sku: Optional[str] = Field(default=None, max_length=40)
    limit: int = Field(default=25, ge=1, le=100)


class CaseArgs(BaseModel):
    sku: Optional[str] = Field(default=None, max_length=40)


class FacilityOpt(BaseModel):
    facility_id: Optional[str] = Field(default=None, max_length=60)


class EvidenceAgent(BoundedAgent):
    SPEC = AgentSpec(
        name="evidence_agent", title="Evidence and Audit Agent",
        trigger="An auditor or officer asks what happened in a case, or exports an evidence pack.",
        purpose="Explain a case from the event ledger with event-id citations for every statement.",
        forbidden_actions=("edit or delete events", "make unsupported claims", "infer causes not on the ledger"),
        human_boundary="The account is evidence for a human reviewer; it is not a finding of fault.",
        failure_behavior="Statements without ledger support are dropped; if nothing is supported it says so.",
        max_tool_calls=8,
    )

    def tools(self) -> list[Tool]:
        ws = self.ws

        def case_history(a: CaseArgs) -> dict:
            plans = sorted((p for p in ws.plans.values() if not a.sku or p["sku"] == a.sku),
                           key=lambda p: p["version"])
            return {"plans": [readers.plan_view(ws, p["plan_id"]) for p in plans]}

        def attestations(a: FacilityOpt) -> dict:
            return {"attestations": [{"facility_id": k[0], "sku": k[1], **v} for k, v in ws.attestations.items()
                                     if not a.facility_id or k[0] == a.facility_id]}

        def decisions(_: NoArgs) -> dict:
            evs = [e for e in ws.store.events if e.event_type.value in
                   ("plan_approved", "rejected", "overridden", "transfer_approved")]
            return {"decisions": [readers.event_row(e) for e in evs]}

        return [
            Tool("search_events", "Search the ledger.", SearchArgs,
                 lambda a: {"events": readers.search_events(ws, a.event_type, a.facility_id, a.sku, None, a.limit)}),
            Tool("get_case_history", "Plan versions for a SKU with their event ids.", CaseArgs, case_history),
            Tool("get_attestations", "Human counts and reconciliation findings.", FacilityOpt, attestations),
            Tool("get_decisions", "Human approvals, rejections and overrides.", NoArgs, decisions),
            Tool("get_outcomes", "Receipts, outcomes, replans and escalations.", NoArgs,
                 lambda a: ws.outcome_summary()),
        ]

    def plan_deterministic(self, request: str, ctx: RunContext) -> list[tuple[str, dict]]:
        return [("get_case_history", {}), ("get_attestations", {}), ("get_decisions", {}), ("get_outcomes", {})]

    def compose_deterministic(self, request: str, ctx: RunContext) -> AgentOutput:
        st: list[Statement] = []
        plans = ctx.results.get("get_case_history", {}).get("plans", [])
        atts = ctx.results.get("get_attestations", {}).get("attestations", [])
        decisions = ctx.results.get("get_decisions", {}).get("decisions", [])
        oc = ctx.results.get("get_outcomes", {})
        if not plans:
            return AgentOutput(answer="No case on the ledger yet: no plan has been proposed.", statements=[])
        first, last = plans[0], plans[-1]
        needs = ", ".join(f"{n['facility_id']} ({n['shortfall']:g})" for n in first["needs"])
        st.append(Statement(text=f"What happened: shortage of {first['sku']} at {needs or 'no facility'}; "
                                 f"{len(plans)} plan version(s) followed.", event_ids=[first["proposed_event_id"]]))
        st.append(Statement(text=f"Why recommended: v1 recommended {first['recommended_option']} under rule "
                                 f"{first['recommendation_rule']}.", event_ids=[first["proposed_event_id"]]))
        for d in decisions[:4]:
            p = d["payload"]
            st.append(Statement(text=f"Human decision: {d['event_type']} by {d['actor']} "
                                     f"(option {p.get('option', '-')}): {p.get('reason') or p.get('justification', '')}",
                                event_ids=[d["event_id"]]))
        for a in atts:
            if a.get("finding") and a["finding"] != "CONSISTENT" and a.get("attester_id") != "fv-prior":
                st.append(Statement(text=f"Evidence: {a['attester_id']} counted {a['usable_qty']:g} usable at "
                                         f"{a['facility_id']} vs {a.get('reported_qty_at_count')} reported — "
                                         f"{a['finding']} ({a['provenance']}).", event_ids=[a["event_id"]]))
        for p in plans[1:]:
            st.append(Statement(text=f"What changed: v{p['version']} ({p['trigger']}) recommends "
                                     f"{p['recommended_option']}; status {p['status']}.",
                                event_ids=cite(p["proposed_event_id"])))
        for r in oc.get("replan_events", []):
            st.append(Statement(text=f"Why a plan failed: {r['plan_id']} — {', '.join(r['codes'])}.",
                                event_ids=[r["event_id"]]))
        for o in oc.get("outcomes", []):
            st.append(Statement(text=f"Outcome: shipment {o['shipment_id']} net {o['net_received']:g} units; "
                                     f"recipient runway {o['runway_before_days']} → {o['runway_after_days']} d.",
                                event_ids=cite(o["event_id"], o.get("receipt_event_id"))))
        answer = (f"Case account for {first['sku']} from the {self.ws.environment} ledger. Latest plan "
                  f"v{last['version']} is {last['status']}. Phantom units blocked: {oc.get('phantom_units_blocked', 0):g}.")
        return AgentOutput(answer=answer, statements=st[:20])
