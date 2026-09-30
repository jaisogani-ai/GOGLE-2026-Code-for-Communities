"""Agent 2 — Operations Copilot. Read-only Q&A for district officers, every claim cited."""
from __future__ import annotations

import re
from typing import Optional

from pydantic import BaseModel, Field

from . import readers
from .base import AgentOutput, AgentSpec, BoundedAgent, NoArgs, RunContext, Statement, Tool, cite

FACILITY_RE = re.compile(r"\b((?:PHC|CHC|DWH|DH|SC)-[A-Z0-9]+)\b", re.I)
EVENT_RE = re.compile(r"\bevt_[0-9a-f]{16}\b")


class QueueArgs(BaseModel):
    sku: Optional[str] = Field(default=None, max_length=40)
    verifier_hours: int = Field(default=6, ge=1, le=80)


class FacilityArgs(BaseModel):
    facility_id: str = Field(min_length=1, max_length=60)


class SearchArgs(BaseModel):
    event_type: Optional[str] = Field(default=None, max_length=40)
    facility_id: Optional[str] = Field(default=None, max_length=60)
    plan_id: Optional[str] = Field(default=None, max_length=60)
    limit: int = Field(default=15, ge=1, le=50)


class PlanArgs(BaseModel):
    plan_id: Optional[str] = Field(default=None, max_length=60)


class EventArgs(BaseModel):
    event_id: str = Field(pattern=r"^evt_[0-9a-f]{16}$")


class OpsCopilot(BoundedAgent):
    SPEC = AgentSpec(
        name="ops_copilot", title="Operations Copilot",
        trigger="A district officer asks a question in the console.",
        purpose="Help district officers understand queue, facility, plan, event and outcome state.",
        forbidden_actions=("approve", "veto", "allocate", "modify inventory", "execute shipment", "change policy"),
        human_boundary="Decisions are made by the district medical officer in the Approval view.",
        failure_behavior="Refuses consequential requests; answers 'not on the ledger' when tools return nothing; "
                         "falls back to deterministic composition if Gemini fails or cites unseen events.",
    )

    def tools(self) -> list[Tool]:
        ws = self.ws

        def queue(a: QueueArgs) -> dict:
            q = ws.trust_queue(a.sku, a.verifier_hours)
            keep = ("facility_id", "sku", "verification_state", "runway_days", "units_at_stake",
                    "recommended_action", "reason", "signals", "report_event_id", "last_attestation_event_id",
                    "report_provenance")
            return {"as_of": q["as_of"], "environment": q["environment"],
                    "rows": [{k: r[k] for k in keep} for r in q["rows"][:12]]}

        return [
            Tool("query_trust_queue", "Ranked verification/transfer queue with reasons.", QueueArgs, queue),
            Tool("get_facility_detail", "Facility registry record, resource states, tasks, shipments.",
                 FacilityArgs, lambda a: readers.facility_detail(ws, a.facility_id)),
            Tool("search_audit_log", "Search ledger events.", SearchArgs,
                 lambda a: readers.search_events(ws, a.event_type, a.facility_id, None, a.plan_id, a.limit)),
            Tool("get_allocation_plan", "Plan options, lines, rejected donors and decision.", PlanArgs,
                 lambda a: readers.plan_view(ws, a.plan_id)),
            Tool("explain_event", "Explain one ledger event and chain integrity.", EventArgs,
                 lambda a: readers.explain_event(ws, a.event_id)),
            Tool("get_outcome", "Outcome metrics measured from the ledger.", NoArgs, lambda a: ws.outcome_summary()),
        ]

    def plan_deterministic(self, request: str, ctx: RunContext) -> list[tuple[str, dict]]:
        q = request.lower()
        calls: list[tuple[str, dict]] = []
        for ev in EVENT_RE.findall(request)[:2]:
            calls.append(("explain_event", {"event_id": ev}))
        for fid in dict.fromkeys(m.upper() for m in FACILITY_RE.findall(request)):
            calls.append(("get_facility_detail", {"facility_id": fid}))
        if any(w in q for w in ("plan", "allocat", "donor", "reject", "transfer", "why", "option")):
            calls.append(("get_allocation_plan", {}))
        if any(w in q for w in ("verify", "verification", "queue", "priorit", "count", "trust", "risk")):
            calls.append(("query_trust_queue", {}))
        if any(w in q for w in ("outcome", "result", "stockout", "phantom", "impact", "hit rate")):
            calls.append(("get_outcome", {}))
        return calls or [("query_trust_queue", {}), ("get_outcome", {})]

    def compose_deterministic(self, request: str, ctx: RunContext) -> AgentOutput:
        st: list[Statement] = []
        r = ctx.results
        env = self.ws.environment
        if "explain_event" in r and "error" not in r["explain_event"]:
            e = r["explain_event"]
            st.append(Statement(text=f"{e['event_type']} by {e['actor']} at {e['occurred_at']}: {e['explanation']} "
                                     f"Hash chain intact: {e['chain_intact']}.", event_ids=[e["event_id"]]))
        fd = r.get("get_facility_detail")
        if fd and "error" not in fd:
            f = fd["facility"]
            for s in fd["resources"]:
                ids = cite(s["report_event_id"], s["last_attestation_event_id"]) or cite(f["event_id"])
                st.append(Statement(text=(
                    f"{f['facility_id']} {s['sku']}: {s['verification_state']}, usable estimate "
                    f"{s['usable_estimate']} (basis {s['estimate_basis']}), runway {s['runway_days']} d; reported "
                    f"{s['reported_qty']} [{s['report_provenance']}]; last count "
                    f"{s['last_attested_at'] or 'never'}{', finding ' + s['last_finding'] if s['last_finding'] else ''}."),
                    event_ids=ids))
        pv = r.get("get_allocation_plan")
        if pv and "error" not in pv:
            st.append(Statement(text=(
                f"Plan v{pv['version']} ({pv['status']}) recommends {pv['recommended_option']} under rule "
                f"{pv['recommendation_rule']}; selected: {pv['selected_option'] or 'pending human decision'}."),
                event_ids=[pv["proposed_event_id"]]))
            for d in pv["rejected_donors"][:4]:
                st.append(Statement(text=f"Donor {d['facility_id']} rejected: {d['reason']}"
                                         f"{' for ' + d['recipient'] if d.get('recipient') else ''}.",
                                    event_ids=[pv["proposed_event_id"]]))
        qv = r.get("query_trust_queue")
        if qv and "error" not in qv:
            for row in [x for x in qv["rows"] if x["recommended_action"] in ("VERIFY", "TRANSFER")][:4]:
                ids = cite(row["report_event_id"], row["last_attestation_event_id"])
                if ids:
                    st.append(Statement(text=f"{row['recommended_action']} {row['facility_id']} {row['sku']}: "
                                             f"{row['reason']}", event_ids=ids))
        oc = r.get("get_outcome")
        if oc and "error" not in oc:
            for fnd in oc["findings"][:3]:
                st.append(Statement(text=f"Count at {fnd['facility_id']} found {fnd['finding']} "
                                         f"(variance {fnd['variance_units']} units).", event_ids=[fnd["event_id"]]))
            for rp in oc["replan_events"][:3]:
                st.append(Statement(text=f"Replan required for {rp['plan_id']}: {', '.join(rp['codes'])}.",
                                    event_ids=[rp["event_id"]]))
        if not st:
            return AgentOutput(answer="That is not on this workspace's ledger. I don't have that data.", statements=[])
        head = f"Answer from the {env} workspace ledger ({len(st)} cited facts)."
        return AgentOutput(answer=head, statements=st[:12])
