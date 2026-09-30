/* TATHYON console. Every value rendered here comes from the API; nothing is computed for show. */
"use strict";

const S = { token: null, user: null, config: null, status: null, ws: null, queue: null, plans: [], layers: null,
            events: null, outcome: null, view: "queue", mapMode: "2d", map: null, selectedFacility: null };
const $ = (id) => document.getElementById(id);
const store = { get(k) { try { return sessionStorage.getItem(k); } catch { return null; } },
                set(k, v) { try { sessionStorage.setItem(k, v); } catch { /* storage unavailable */ } } };

function h(tag, attrs = {}, ...kids) {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v === null || v === undefined || v === false) continue;
    if (k === "class") el.className = v;
    else if (k.startsWith("on")) el.addEventListener(k.slice(2), v);
    else if (k === "text") el.textContent = v;
    else el.setAttribute(k, v === true ? "" : v);
  }
  for (const kid of kids.flat()) if (kid !== null && kid !== undefined && kid !== false)
    el.append(kid instanceof Node ? kid : document.createTextNode(String(kid)));
  return el;
}
const fmt = (n, d = 1) => (n === null || n === undefined || Number.isNaN(n)) ? "—" : Number(n).toLocaleString(undefined, { maximumFractionDigits: d });
const ago = (iso) => { if (!iso) return "never"; const hrs = (Date.now() - Date.parse(iso)) / 36e5;
  return hrs < 48 ? `${fmt(hrs, 0)} h ago` : `${fmt(hrs / 24, 0)} d ago`; };
const dt = (iso) => iso ? new Date(iso).toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" }) : "—";

function toast(msg, bad = false) {
  const t = $("toast"); t.textContent = msg; t.className = "toast" + (bad ? " bad" : ""); t.hidden = false;
  clearTimeout(toast._t); toast._t = setTimeout(() => { t.hidden = true; }, 5200);
}

async function api(path, opts = {}) {
  const headers = { ...(opts.headers || {}) };
  if (S.token) headers.Authorization = `Bearer ${S.token}`;
  if (opts.body && typeof opts.body !== "string") { headers["Content-Type"] = "application/json"; opts.body = JSON.stringify(opts.body); }
  let res;
  try { res = await fetch(path, { ...opts, headers }); }
  catch { throw new Error("Network error: the TATHYON server is unreachable."); }
  const text = await res.text();
  let data = null; try { data = text ? JSON.parse(text) : null; } catch { data = { message: text }; }
  if (!res.ok) {
    if (res.status === 401) { S.token = null; store.set("tathyon.token", ""); }
    const e = new Error(`${data?.error || res.status}: ${data?.message || "Request failed"}`); e.code = data?.error; throw e;
  }
  return data;
}
async function act(fn, okMsg) {
  try { const r = await fn(); if (okMsg) toast(okMsg); await refreshAll(); return r; }
  catch (e) { toast(e.message, true); return null; }
}

/* ---------- badges ---------- */
function prov(p) {
  const v = String(p || "UNKNOWN");
  const cls = v.includes("SYNTHETIC") || v.includes("HYPOTHETICAL") || v.startsWith("SAMPLE") ? "b-synthetic" : v.includes("SIMULATED") ? "b-simulated"
    : v === "REAL_PUBLIC_OSM" ? "b-real" : v.includes("ATTESTED") ? "b-verified" : v.includes("USER") || v.includes("REAL_USER") ? "b-user"
    : v.includes("NOT_CONFIGURED") || v === "UNKNOWN" ? "b-nc" : "b-info";
  const label = { REAL_PUBLIC_OSM: "REAL · OSM", USER_SUPPLIED_UNVERIFIED: "USER-SUPPLIED", REAL_USER_PROVIDED: "USER-SUPPLIED",
    HUMAN_ATTESTED: "VERIFIED COUNT", SYNTHETIC: "SYNTHETIC" }[v] || v.replaceAll("_", " ");
  return h("span", { class: `badge ${cls}`, title: v }, label);
}
const stateBadge = (s) => h("span", { class: `badge ${s === "VERIFIED" ? "b-verified" : "b-unverified"}` }, s || "—");
const actBadge = (a) => h("span", { class: `badge act-${a}` }, a);
const statusBadge = (s) => h("span", { class: `badge ${/REJECT|INVALID|REPLAN|FAIL|PHANTOM|PARTIAL|DELAYED/.test(s) ? "b-bad"
  : /APPROVED|RECEIVED$|DISPATCHED|CONSISTENT|VERIFIED/.test(s) ? "b-ok" : /CONTINGENT|PROPOSED|PENDING|OPEN/.test(s) ? "b-warn" : "b-nc"}` }, s || "—");
const empty = (title, body) => h("div", { class: "empty" }, h("strong", {}, title), body);
const canDo = (...roles) => S.user && roles.includes(S.user.role);

/* ---------- identity + status ---------- */
async function signIn(userKey, facilityId) {
  if (S.config?.demo_users?.[userKey]?.scoped && !facilityId) {
    facilityId = prompt("Facility id this in-charge signs for (e.g. from the map drawer):");
    if (!facilityId) throw new Error("A facility in-charge signs in for one facility.");
  }
  const r = await api("/api/auth/session", { method: "POST", body: { user: userKey, facility_id: facilityId || null } });
  S.token = r.token; S.user = r.user; store.set("tathyon.token", r.token); store.set("tathyon.user", userKey);
}
function renderStatus() {
  const st = S.status, strip = $("statusStrip"); strip.replaceChildren();
  if (!st) return;
  const g = st.gemini, m = st.maps;
  strip.append(
    h("span", { class: "badge b-real", title: "Data sources: public registry, uploads, human counts" }, `DATA · ${S.ws?.environment || "—"}`),
    h("span", { class: `badge ${g.configured ? "b-ok" : "b-nc"}`, title: g.fallback }, g.configured ? `GEMINI · ${g.model}` : "GEMINI · NOT CONFIGURED"),
    h("span", { class: `badge ${m.basemap === "GOOGLE_MAPS_JS" ? "b-ok" : "b-nc"}` }, m.basemap === "GOOGLE_MAPS_JS" ? "MAPS · GOOGLE" : "MAPS · OSM"),
    h("span", { class: `badge ${m.routes_provider === "GOOGLE_ROUTES_API" ? "b-ok" : "b-nc"}` }, `ROUTES · ${m.routes_provider === "GOOGLE_ROUTES_API" ? "GOOGLE" : "NOT CONFIGURED"}`),
    h("span", { class: `badge ${m.tiles_3d.state === "AVAILABLE" ? "b-ok" : "b-nc"}`, title: m.tiles_3d.reason || "" }, `3D · ${m.tiles_3d.state.replaceAll("_", " ")}`),
    h("span", { class: `badge ${S.chain === false ? "b-bad" : "b-ok"}` }, S.chain === false ? "LEDGER · TAMPERED" : "LEDGER · INTACT"),
    h("span", { class: "badge b-nc", title: "Polls this workspace's ledger every 15 s and refreshes when it changes. Not a government feed." },
      `MONITOR · ${S.updatedAt ? S.updatedAt.toLocaleTimeString() : "—"}`));
}

/* ---------- tabs ---------- */
function show(view) {
  S.view = view;
  document.querySelectorAll("nav.tabs button").forEach((b) => b.setAttribute("aria-selected", String(b.dataset.view === view)));
  document.querySelectorAll("section.view").forEach((s) => { s.hidden = s.id !== `view-${view}`; });
  if (view === "map") renderMap();
}

/* ---------- data loading ---------- */
async function refreshAll() {
  const [ws, queue, plans, outcome, events] = await Promise.all([
    api("/api/workspace"), api(`/api/trust/queue?verifier_hours=${Number($("queueHours").value) || 6}${$("queueSku").value ? `&sku=${encodeURIComponent($("queueSku").value)}` : ""}`),
    api("/api/plans"), api("/api/outcome"), api(`/api/events?limit=300${$("auditType").value ? `&event_type=${$("auditType").value}` : ""}`)]);
  Object.assign(S, { ws, queue, plans: plans.plans, outcome, events, chain: events.chain_intact });
  fillSelect($("queueSku"), ["", ...Object.keys(ws.skus)], (k) => k || "All SKUs");
  fillSelect($("planSku"), Object.keys(ws.skus), (k) => `${k} — ${ws.skus[k].name}`);
  fillSelect($("mapSku"), ["", ...Object.keys(ws.skus)], (k) => k || "All resources");
  $("sampleBanner").hidden = !ws.has_sample_data;
  S.lastEvents = events.total_events; S.updatedAt = new Date();
  renderStatus(); renderQueue(); renderAllocation(); renderApproval(); renderOutcome(); renderAudit(); renderData();
  $("c-queue").textContent = queue.rows.filter((r) => r.recommended_action === "VERIFY" || r.recommended_action === "TRANSFER").length;
  $("c-alloc").textContent = S.plans.length;
  $("c-approval").textContent = S.plans.filter((p) => p.status === "PROPOSED").length;
  $("c-audit").textContent = events.total_events;
  $("c-map").textContent = ws.facilities;
  if (S.view === "map") renderMap();
}
function fillSelect(sel, values, label) {
  const cur = sel.value;
  sel.replaceChildren(...values.map((v) => h("option", { value: v }, label(v))));
  if (values.includes(cur)) sel.value = cur;
}

/* ---------- trust queue ---------- */
function renderQueue() {
  const body = $("queueBody"), q = S.queue; body.replaceChildren();
  if (!S.ws.facilities) return body.append(empty("No facilities loaded", "Open Data intake and load the OpenStreetMap registry or upload your facility registry."));
  if (!q.rows.length) return body.append(empty("No stock data yet", "Upload a DVDMS / e-Aushadhi-shaped stock export in Data intake. Nothing is generated."));
  body.append(h("p", { class: "muted", style: "margin:0 0 8px" }, `As of ${dt(q.as_of)} · ${q.method} · verifier hours used ${fmt(q.hours_used)} of ${q.verifier_hours}.`));
  const rows = q.rows.map((r) => h("tr", {},
    h("td", {}, h("strong", {}, r.facility_name), h("div", { class: "mono muted" }, r.facility_id)),
    h("td", {}, r.sku),
    h("td", {}, actBadge(r.recommended_action)),
    h("td", { class: "reason" }, r.reason, r.signals.length ? h("div", { class: "mono muted" }, r.signals.join(" · ")) : null),
    h("td", { class: "num" }, fmt(r.reported_now), h("div", {}, prov(r.report_provenance))),
    h("td", { class: "num" }, fmt(r.usable_estimate), h("div", {}, stateBadge(r.verification_state))),
    h("td", {}, h("div", {}, `report ${ago(r.report_date)}`), h("div", { class: "muted" }, `count ${ago(r.last_attested_at)}`)),
    h("td", {}, h("span", { class: `badge ${r.evidence_confidence === "HIGH" ? "b-ok" : r.evidence_confidence === "MEDIUM" ? "b-warn" : "b-bad"}`,
      title: "Evidence confidence: HIGH = counted ≤7 d, MEDIUM = counted ≤30 d, LOW = older or never. Not a probability." }, r.evidence_confidence)),
    h("td", { class: "num" }, r.runway_days === null ? "—" : `${fmt(r.runway_days)} d`, h("div", { class: "muted" }, r.projected_failure_at ? dt(r.projected_failure_at) : "")),
    h("td", { class: "num" }, `${fmt(r.visit_hours)} h`),
    h("td", { class: "num" }, fmt(r.units_at_stake)),
    h("td", {}, canDo("field_verifier", "block_supervisor") ? h("button", { class: "btn sm", onclick: () => openAttest(r) }, "Record count") : null)));
  body.append(h("div", { class: "table-wrap" }, h("table", {},
    h("thead", {}, h("tr", {}, ...["Facility", "Resource", "Action", "Reason", "Reported now", "Usable estimate", "Observation age",
      "Confidence", "Projected failure", "Visit cost", "Units at stake", ""].map((c) => h("th", { scope: "col" }, c)))),
    h("tbody", {}, rows))));
}
function openAttest(r) {
  const f = $("attestForm"); $("attestPanel").hidden = false;
  f.facility_id.value = r.facility_id; f.sku.value = r.sku; f.present_qty.value = ""; f.usable_qty.value = "";
  f.present_qty.focus();
}

/* ---------- allocation ---------- */
function latestPlans() {
  const by = {}; for (const p of S.plans) if (!by[p.sku] || by[p.sku].version < p.version) by[p.sku] = p; return by;
}
function renderAllocation() {
  const body = $("allocBody"); body.replaceChildren();
  $("proposePlan").disabled = !canDo("district_medical_officer", "logistics_officer", "data_officer") || !Object.keys(S.ws.skus).length;
  if (!S.plans.length) return body.append(empty("No plan yet", "Generate options for a SKU. The CP-SAT solver only counts physically verified stock above each donor's safety floor."));
  const sku = $("planSku").value || Object.keys(latestPlans())[0];
  const chain = S.plans.filter((p) => p.sku === sku).sort((a, b) => b.version - a.version);
  if (!chain.length) return body.append(empty(`No plan for ${sku}`, "Generate options to start."));
  const p = chain[0];
  body.append(h("div", { class: "notice info" }, `Plan v${p.version} · ${p.status} · trigger ${p.trigger} · recommended ${p.recommended_option} (${p.recommendation_rule}) · ledger `,
    citeBtn(p.proposed_event_id)));
  body.append(h("div", { class: "grid2" },
    h("div", { class: "panel" }, h("h2", {}, "Needs (recipients below alert runway)"), p.needs.length ? table(["Facility", "Shortfall", "Runway", "Essentiality"],
      p.needs.map((n) => [n.facility_id, fmt(n.shortfall), `${fmt(n.days_to_stockout)} d`, n.essentiality])) : h("p", { class: "muted" }, "No open need.")),
    h("div", { class: "panel" }, h("h2", {}, "Rejected donors — why"), p.rejected_donors.length ? table(["Donor", "Recipient", "Reason"],
      p.rejected_donors.map((d) => [d.facility_id, d.recipient || "all", d.reason])) : h("p", { class: "muted" }, "None."))));
  body.append(h("div", { class: "panel" }, h("h2", {}, "Options and counterfactuals"), ...p.options.map((o) => h("div", { class: `option-card${o.option === p.recommended_option ? " rec" : ""}` },
    h("h3", {}, o.option.replaceAll("_", " "), o.option === p.recommended_option ? h("span", { class: "badge b-info" }, "RECOMMENDED") : null,
      !o.approvable ? h("span", { class: "badge b-nc" }, "COUNTERFACTUAL · NOT APPROVABLE") : null),
    h("div", { class: "muted" }, o.why),
    h("div", { class: "mono" }, `fulfils ${fmt(o.fulfilled_qty)} · shortfall ${fmt(o.shortfall_qty)} · qty-weighted travel ${fmt(o.qty_weighted_travel_hours)} h`),
    o.lines.length ? h("div", { class: "mono muted" }, o.lines.map((l) => `${l.from_facility}→${l.to_facility} ${fmt(l.qty)} (${l.kind === "IMMEDIATE" ? "now" : "after count"})`).join(" · ")) : null))));
  body.append(h("div", { class: "panel" }, h("h2", {}, "Lines"), p.lines.length ? table(["Line", "From", "To", "Qty", "Travel", "Kind", "Status", "Action"],
    p.lines.map((l) => [h("span", { class: "mono" }, l.line_id), l.from_facility, l.to_facility, fmt(l.qty), `${fmt(l.travel_hours)} h`, l.kind.replaceAll("_", " "),
      statusBadge(l.status), lineActions(p, l)])) : h("p", { class: "muted" }, "This option has no transfer lines."),
    h("p", { class: "muted" }, "Planner travel times are straight-line × 1.35 estimates at 38 km/h; road routes on the map come from the route provider shown there.")));
  if (chain.length > 1) body.append(h("div", { class: "panel" }, h("h2", {}, "Plan history"),
    table(["Version", "Status", "Trigger", "Recommended", "Ledger"], chain.map((c) => [`v${c.version}`, statusBadge(c.status), c.trigger, c.recommended_option, citeBtn(c.proposed_event_id)]))));
}
function lineActions(p, l) {
  const box = h("div", { style: "display:flex;gap:4px;flex-wrap:wrap" });
  if (p.status === "APPROVED" && (l.status === "APPROVED" || l.status === "RELEASED_BREAK_GLASS") && canDo("district_medical_officer", "logistics_officer"))
    box.append(h("button", { class: "btn sm primary", onclick: () => act(() => api(`/api/plans/${p.plan_id}/lines/${l.line_id}/dispatch`, { method: "POST" }), "Dispatched") }, "Dispatch"));
  if (l.status === "APPROVED_CONTINGENT" && canDo("district_medical_officer")) {
    box.append(h("button", { class: "btn sm", onclick: () => act(() => api(`/api/plans/${p.plan_id}/lines/${l.line_id}/release`, { method: "POST" }), "Line released after count") }, "Release after count"));
    box.append(h("button", { class: "btn sm danger", onclick: () => breakGlass(p, l) }, "Break-glass"));
  }
  return box;
}
async function breakGlass(p, l) {
  const reasons = (await api("/api/break-glass/reasons")).reasons;
  const code = prompt(`Break-glass releases UNVERIFIED stock and creates a 24 h count obligation.\nReason code (${Object.keys(reasons).join(", ")}):`);
  if (!code) return;
  const just = prompt("Justification (20+ characters, recorded on the ledger):");
  if (!just) return;
  act(() => api(`/api/plans/${p.plan_id}/lines/${l.line_id}/break-glass`, { method: "POST", body: { reason_code: code.trim().toUpperCase(), justification: just } }), "Break-glass recorded");
}
function table(cols, rows) {
  return h("div", { class: "table-wrap" }, h("table", {}, h("thead", {}, h("tr", {}, ...cols.map((c) => h("th", { scope: "col" }, c)))),
    h("tbody", {}, rows.map((r) => h("tr", {}, ...r.map((c) => h("td", {}, c)))))));
}
function citeBtn(id) { return id ? h("button", { class: "cite", onclick: () => openEvent(id), title: "Open ledger event" }, id) : "—"; }

/* ---------- approval ---------- */
function renderApproval() {
  const body = $("approvalBody"); body.replaceChildren();
  const pending = S.plans.filter((p) => p.status === "PROPOSED");
  if (!canDo("district_medical_officer")) body.append(h("div", { class: "notice warn" }, `You are signed in as ${S.user?.role}. Only a district medical officer can approve or reject.`));
  if (!pending.length) return body.append(empty("Nothing awaiting a decision", "Proposed plans appear here with their evidence."));
  for (const p of pending) {
    const form = h("form", { class: "panel" });
    const approvable = p.options.filter((o) => o.approvable);
    form.append(h("h2", {}, `${p.sku} · plan v${p.version} · ${p.trigger}`),
      h("p", {}, "Recommended: ", h("strong", {}, p.recommended_option), ` (${p.recommendation_rule}). Evidence: `, citeBtn(p.proposed_event_id)),
      h("fieldset", { style: "border:0;padding:0;margin:0 0 8px" }, h("legend", { class: "muted" }, "Option to approve"),
        ...approvable.map((o) => h("label", { style: "display:block;margin:3px 0" },
          h("input", { type: "radio", name: "option", value: o.option, checked: o.option === p.recommended_option }),
          ` ${o.option.replaceAll("_", " ")} — fulfils ${fmt(o.fulfilled_qty)}, shortfall ${fmt(o.shortfall_qty)}`))),
      h("div", { class: "muted", style: "margin-bottom:4px" }, "Lines of the recommended option (untick to reject a line):"),
      ...(p.options.find((o) => o.option === p.recommended_option)?.lines || []).map((l) => h("label", { style: "display:block" },
        h("input", { type: "checkbox", name: "line", value: l.line_id, checked: true }),
        ` ${l.from_facility} → ${l.to_facility} · ${fmt(l.qty)} · ${l.kind === "IMMEDIATE" ? "dispatch now" : "held until a count confirms the donor"}`)),
      h("label", { class: "field", style: "margin-top:8px" }, "Reason (recorded on the ledger)", h("textarea", { name: "reason", required: true, maxlength: 1000, style: "min-height:60px" })),
      h("div", { style: "display:flex;gap:8px;margin-top:8px" },
        h("button", { class: "btn primary", type: "submit", value: "APPROVE", disabled: !canDo("district_medical_officer") }, "Approve"),
        h("button", { class: "btn danger", type: "submit", value: "REJECT", disabled: !canDo("district_medical_officer") }, "Reject")));
    form.addEventListener("submit", (ev) => {
      ev.preventDefault();
      const decision = ev.submitter?.value || "APPROVE", fd = new FormData(form), option = fd.get("option");
      const rec = p.options.find((o) => o.option === p.recommended_option);
      const kept = new Set(fd.getAll("line"));
      const rejected = option === p.recommended_option ? (rec?.lines || []).map((l) => l.line_id).filter((id) => !kept.has(id)) : [];
      act(() => api(`/api/plans/${p.plan_id}/decision`, { method: "POST", body: { decision, option, rejected_lines: rejected, reason: fd.get("reason") } }),
        decision === "APPROVE" ? "Plan approved and ledgered" : "Plan rejected and ledgered");
    });
    body.append(form);
  }
}

/* ---------- outcome ---------- */
function metric(v, l, src) { return h("div", { class: "metric" }, h("div", { class: "v" }, v), h("div", { class: "l" }, l), h("div", { class: "src" }, src)); }
function renderOutcome() {
  const o = S.outcome, body = $("outcomeBody"); body.replaceChildren();
  body.append(h("div", { class: "notice info" }, o.label));
  const sd = o.stockout_days_projected;
  body.append(h("div", { class: "grid3", style: "margin-bottom:14px" },
    metric(`${fmt(sd.baseline_at_load)} → ${fmt(sd.current)}`, "Projected stockout-days in horizon (at first load → now)", sd.baseline_at_load === null ? "No baseline snapshot recorded for this workspace" : "Projection from ledger state + in-transit stock"),
    metric(fmt(o.phantom_units_blocked), "Phantom units blocked", "Units a trust-the-report plan would have moved from donors later counted short"),
    metric(o.verification_hit_rate === null ? "—" : `${fmt(o.verification_hit_rate * 100, 0)}%`, `Verification hit rate (${o.verification_tasks_completed} tasks)`, o.verification_hit_rate_definition)));
  body.append(h("div", { class: "panel" }, h("h2", {}, "Shipments"), o.shipments.length ? table(["Shipment", "Route", "Qty", "Status", "Received", "Damaged", "ETA", "Action"],
    o.shipments.map((s) => [h("span", { class: "mono" }, s.shipment_id), `${s.from_facility} → ${s.to_facility}`, fmt(s.qty), statusBadge(s.status), fmt(s.received_qty), fmt(s.damaged_qty), dt(s.eta), shipmentActions(s)]))
    : h("p", { class: "muted" }, "No shipments dispatched."), h("p", { class: "muted" }, "Status is entered by people. There is no GPS or live vehicle tracking.")));
  body.append(h("div", { class: "grid2" },
    h("div", { class: "panel" }, h("h2", {}, "Count findings"), o.findings.length ? table(["Facility", "SKU", "Finding", "Variance", "Ledger"],
      o.findings.map((f) => [f.facility_id, f.sku, statusBadge(f.finding), fmt(f.variance_units), citeBtn(f.event_id)])) : h("p", { class: "muted" }, "No counts recorded.")),
    h("div", { class: "panel" }, h("h2", {}, "Replans and escalations"), (o.replan_events.length || o.escalations.length) ? table(["Kind", "Detail", "Ledger"],
      [...o.replan_events.map((r) => ["REPLAN REQUIRED", r.codes.join(", "), citeBtn(r.event_id)]), ...o.escalations.map((e) => ["ESCALATED", `${fmt(e.qty)} units · ${e.reason}`, citeBtn(e.event_id)])])
      : h("p", { class: "muted" }, "None."))));
  if (o.outcomes.length) body.append(h("div", { class: "panel" }, h("h2", {}, "Intervention results (recipient runway before → after receipt)"),
    table(["Facility", "Net received", "Runway before", "Runway after", "Ledger"], o.outcomes.map((x) => [x.shipment_id ? S.outcome.shipments.find((s) => s.shipment_id === x.shipment_id)?.to_facility || "—" : "—",
      fmt(x.net_received), `${fmt(x.runway_before_days)} d`, `${fmt(x.runway_after_days)} d`, citeBtn(x.event_id)]))));
}
function shipmentActions(s) {
  const box = h("div", { style: "display:flex;gap:4px" });
  if (!["IN_TRANSIT", "DELAYED"].includes(s.status)) return box;
  if (canDo("facility_incharge", "pharmacist") && (!S.user.facility_id || S.user.facility_id === s.to_facility))
    box.append(h("button", { class: "btn sm primary", onclick: () => {
      const rec = prompt(`Units received at ${s.to_facility} (dispatched ${s.qty}):`); if (rec === null) return;
      const dmg = prompt("Of those, damaged/unusable:", "0"); if (dmg === null) return;
      act(() => api(`/api/shipments/${s.shipment_id}/receive`, { method: "POST", body: { received_qty: Number(rec), damaged_qty: Number(dmg) } }), "Receipt recorded");
    } }, "Record receipt"));
  if (!(S.user.role === "facility_incharge" && S.user.facility_id === s.to_facility))
    box.append(h("button", { class: "btn sm", title: "Demo: sign in as the receiving facility's in-charge",
      onclick: async () => { try { await signIn("incharge", s.to_facility); $("identity").value = "incharge"; await refreshAll();
        toast(`Signed in as in-charge of ${s.to_facility}`); } catch (e) { toast(e.message, true); } } }, "Act as receiving in-charge"));
  if (canDo("district_medical_officer", "logistics_officer"))
    box.append(h("button", { class: "btn sm", onclick: () => {
      const hrs = prompt("Delay in hours:"); if (!hrs) return; const why = prompt("Reason:"); if (!why) return;
      act(() => api(`/api/shipments/${s.shipment_id}/delay`, { method: "POST", body: { hours: Number(hrs), reason: why } }), "Delay recorded");
    } }, "Report delay"));
  return box;
}

/* ---------- audit ---------- */
const EVENT_TYPES = ["", "workspace_loaded", "facility_registered", "sku_registered", "claim_ingested", "row_quarantined", "verification_requested", "attested",
  "reconciled", "plan_proposed", "plan_approved", "rejected", "overridden", "obligation_created", "transfer_approved", "shipment_dispatched",
  "shipment_delayed", "received", "outcome_recorded", "replan_required", "escalated", "agent_run", "agent_refused", "extracted", "flagged"];
function actorKind(a) { return a.startsWith("agent:") ? ["AGENT", "b-info"] : a.startsWith("tathyon_") || a === "system" ? ["ENGINE", "b-nc"] : ["HUMAN", "b-ok"]; }
function summarize(e) {
  const p = e.payload || {};
  return p.reason || p.finding || p.status || p.category || p.refusal_code || p.agent || (p.facility && p.facility.name) || p.recommended_option || p.note || "";
}
function renderAudit() {
  const ev = S.events, body = $("auditBody"); body.replaceChildren();
  body.append(h("div", { class: `notice ${ev.chain_intact ? "ok" : "bad"}` }, ev.chain_intact
    ? `Hash chain verified over ${ev.total_events} events. Each event seals the previous event's SHA-256 hash (tamper-evident, not tamper-proof).`
    : `Hash chain BROKEN at offset ${ev.first_bad_offset}. Treat later events as untrusted.`));
  if (!ev.events.length) return body.append(empty("No events", "Events appear as data is loaded and decisions are made."));
  body.append(h("div", { class: "table-wrap" }, h("table", {}, h("thead", {}, h("tr", {}, ...["#", "Time", "Event", "Actor", "Kind", "Facility", "SKU", "Detail", "Hash"].map((c) => h("th", { scope: "col" }, c)))),
    h("tbody", {}, ev.events.map((e) => { const [k, cls] = actorKind(e.actor);
      return h("tr", { class: "clickable", tabindex: "0", onclick: () => openEvent(e.event_id), onkeydown: (x) => { if (x.key === "Enter") openEvent(e.event_id); } },
        h("td", { class: "num" }, e.offset), h("td", {}, dt(e.occurred_at)), h("td", { class: "mono" }, e.event_type), h("td", {}, e.actor),
        h("td", {}, h("span", { class: `badge ${cls}` }, k)), h("td", {}, e.facility_id), h("td", {}, e.sku), h("td", { class: "reason" }, String(summarize(e)).slice(0, 140)),
        h("td", { class: "mono" }, e.hash)); })))));
}
async function openEvent(id) {
  try {
    const e = await api(`/api/events/${id}`);
    openDrawer();
    $("agentLog").prepend(h("div", { class: "agent-answer" }, h("strong", {}, `${e.event_type} · ${id}`),
      h("p", {}, e.explanation), h("dl", { class: "kv" }, h("dt", {}, "Actor"), h("dd", {}, e.actor), h("dt", {}, "Occurred"), h("dd", {}, dt(e.occurred_at)),
        h("dt", {}, "Hash / prev"), h("dd", { class: "mono" }, `${e.hash} ← ${e.prev_hash}`), h("dt", {}, "Chain"), h("dd", {}, e.chain_intact ? "intact" : `broken at ${e.first_bad_offset}`)),
      h("pre", { class: "mono", style: "white-space:pre-wrap;max-height:240px;overflow:auto" }, JSON.stringify(e.payload, null, 2))));
  } catch (err) { toast(err.message, true); }
}

/* ---------- data intake ---------- */
function renderData() {
  const s = S.status?.data_sources || {}, box = $("dataSources"); box.replaceChildren(h("h2", {}, "Sources and their status"));
  box.append(table(["Source", "Status"], Object.entries(s).map(([k, v]) => [k.replaceAll("_", " "), h("span", {}, prov(v.split(" ")[0]), " ", v.split(" ").slice(1).join(" "))])));
  const up = canDo("data_officer", "district_medical_officer");
  ["loadOsm", "loadSample"].forEach((id) => { $(id).disabled = !up || !S.ws.osm_registry_available; });
  $("resetWs").disabled = !canDo("district_medical_officer");
  document.querySelectorAll("#registryForm button, #stockForm button").forEach((b) => { b.disabled = !up; });
  const q = S.ws.quarantine, qb = $("quarantineBody"); qb.replaceChildren();
  qb.append(q.length ? table(["Source", "Row", "Reasons", "Ledger"], q.slice().reverse().map((x) => [x.source, x.row_number, x.reasons.join(", "), citeBtn(x.event_id)]))
    : h("p", { class: "muted" }, "No quarantined rows."));
}
async function readFile(input) {
  const f = input.files?.[0]; if (!f) throw new Error("Choose a file first.");
  if (f.size > 2_000_000) throw new Error("File is larger than 2 MB.");
  return { content: await f.text(), source_filename: f.name.replace(/[\\/]/g, "_").slice(0, 120) };
}

/* ---------- map ---------- */
function markerColor(m) {
  if (m.has_phantom_finding) return "#7a3fa0";
  if (m.is_recipient) return "#a3231f";
  if (m.is_verification_target) return "#d99a00";
  if (m.resources.some((r) => r.verification_state === "VERIFIED")) return "#1d6b3a";
  return m.resources.length ? "#0b4f8a" : "#9aa3ae";
}
async function renderMap() {
  const q = new URLSearchParams(); if ($("mapSku").value) q.set("sku", $("mapSku").value);
  if ($("mapBlock").value) q.set("block", $("mapBlock").value); if ($("mapTier").value) q.set("tier", $("mapTier").value);
  let layers; try { layers = await api(`/api/map/layers?${q}`); } catch (e) { $("mapNotice").replaceChildren(h("div", { class: "notice bad" }, e.message)); return; }
  S.layers = layers;
  fillSelect($("mapBlock"), ["", ...layers.filters.blocks], (b) => b || "All districts");
  fillSelect($("mapTier"), ["", ...layers.filters.tiers], (t) => t || "All facility types");
  $("facilityList").replaceChildren(...layers.markers.map((m) => h("option", { value: `${m.name} (${m.facility_id})` })));
  const t3 = S.status.maps.tiles_3d;
  $("mode3d").disabled = t3.state !== "AVAILABLE"; $("mode3d").title = t3.state === "AVAILABLE" ? "Google Photorealistic 3D Tiles" : t3.reason;
  const notes = [];
  if (!layers.markers.length) notes.push(h("div", { class: "notice warn" }, "No facilities in the registry. Load OpenStreetMap facilities or upload a registry in Data intake."));
  if (t3.state !== "AVAILABLE") notes.push(h("div", { class: "notice info" }, `3D unavailable: ${t3.reason} 2D is the operational view.`));
  if (layers.routes.some((r) => r.provenance.startsWith("SYNTHETIC"))) notes.push(h("div", { class: "notice warn" }, "Some routes are straight-line estimates (route provider not configured or failed); they are labelled on each route."));
  $("mapNotice").replaceChildren(...notes);
  try {
    if (S.mapMode === "3d" && t3.state === "AVAILABLE") await draw3d(layers);
    else if (S.config.maps_browser_key) await drawGoogle(layers);
    else drawLeaflet(layers);
  } catch (e) {
    $("mapNotice").append(h("div", { class: "notice bad" }, `Map failed to load (${e.message}). Showing OpenStreetMap fallback.`));
    drawLeaflet(layers);
  }
}
function resetCanvas() { if (S.map?.destroy) S.map.destroy(); if (S.map?.remove) S.map.remove(); S.map = null; const c = $("mapCanvas"); c.replaceChildren(); return c; }
function selectFacility(m) {
  S.selectedFacility = m.facility_id;
  const d = $("mapDrawer"); d.replaceChildren(h("h2", {}, m.name),
    h("dl", { class: "kv" }, h("dt", {}, "ID"), h("dd", { class: "mono" }, m.facility_id), h("dt", {}, "Type"), h("dd", {}, m.tier),
      h("dt", {}, "District / block"), h("dd", {}, m.block || "—"), h("dt", {}, "Location"), h("dd", { class: "mono" }, `${m.lat.toFixed(5)}, ${m.lon.toFixed(5)}`),
      h("dt", {}, "Source"), h("dd", {}, prov(m.coordinates_provenance)), h("dt", {}, "Cold chain"), h("dd", {}, m.has_cold_chain === null ? "unknown" : m.has_cold_chain ? "yes" : "no"),
      h("dt", {}, "Registry event"), h("dd", {}, citeBtn(m.registry_event_id))));
  if (!m.resources.length) d.append(h("p", { class: "muted" }, "No stock report for this facility. Its state is unknown, not zero."));
  for (const r of m.resources) d.append(h("div", { class: "option-card" }, h("h3", {}, r.sku, actBadge(r.recommended_action), stateBadge(r.verification_state)),
    h("div", { class: "mono" }, `usable est. ${fmt(r.usable_estimate)} · reported ${fmt(r.reported_now)} · runway ${fmt(r.runway_days)} d`),
    h("div", {}, prov(r.report_provenance), " ", h("span", { class: "badge b-nc" }, `confidence ${r.evidence_confidence}`), r.last_finding ? statusBadge(r.last_finding) : null),
    h("p", { class: "muted", style: "margin:6px 0 0" }, r.reason)));
  const bs = h("div", {}, h("span", { class: "spinner" }));
  d.append(h("h2", { style: "margin-top:12px" }, "Beds & staff"), bs);
  api(`/api/facilities/${encodeURIComponent(m.facility_id)}`).then((fd) => {
    bs.replaceChildren(fd.beds_and_staff.length ? table(["Metric", "Value", "Observed", "Source", "Signal"], fd.beds_and_staff.map((o) =>
      [`${o.resource_key} ${o.metric.replaceAll("_", " ")}`, `${fmt(o.value)} ${o.unit}`, ago(o.observed_at), prov(o.provenance),
        o.advisory_event_id ? h("span", {}, h("span", { class: "badge b-warn", title: "Statistical anomaly signal — human decides" }, "UNUSUAL"), " ", citeBtn(o.advisory_event_id))
          : h("span", { class: "muted" }, o.anomaly_status.replaceAll("_", " ").toLowerCase())]))
      : h("p", { class: "muted" }, "No bed or staff report for this facility. No bed or attendance system is connected."));
  }).catch((e) => bs.replaceChildren(h("span", { class: "muted" }, e.message)));
  const routes = S.layers.routes.filter((r) => r.from === m.facility_id || r.to === m.facility_id);
  if (routes.length) d.append(h("h2", { style: "margin-top:12px" }, "Routes"), table(["Route", "Qty", "Status", "Provider"], routes.map((r) => [`${r.from}→${r.to}`, fmt(r.qty),
    statusBadge(r.shipment_status || r.line_status), h("span", {}, prov(r.provenance), r.duration_min ? ` ${fmt(r.duration_min, 0)} min, ${fmt(r.distance_km)} km` : "")])));
}
function drawLeaflet(layers) {
  const c = resetCanvas(); const map = L.map(c); S.map = map;
  L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", { maxZoom: 18, attribution: "© OpenStreetMap contributors" }).addTo(map);
  const pts = [];
  for (const m of layers.markers) { pts.push([m.lat, m.lon]);
    L.circleMarker([m.lat, m.lon], { radius: m.resources.length ? 8 : 5, color: "#222", weight: 1, fillColor: markerColor(m), fillOpacity: .9 })
      .addTo(map).bindTooltip(`${m.name} · ${m.coordinates_provenance}`).on("click", () => selectFacility(m)); }
  for (const r of layers.routes) L.polyline(r.path, { color: r.line_status === "INVALIDATED" ? "#a3231f" : "#0b4f8a", weight: 3,
    dashArray: ["DISPATCHED", "DELAYED", "RECEIVED"].includes(r.line_status) ? null : "6 6" }).addTo(map).bindTooltip(`${r.from}→${r.to} ${r.qty} · ${r.line_status} · ${r.provenance}`);
  if (pts.length) map.fitBounds(pts, { padding: [30, 30] }); else map.setView([19.07, 82.03], 9);
}
function loadScript(src, id) {
  return new Promise((resolve, reject) => { if (document.getElementById(id)) return resolve();
    const s = h("script", { src, id, async: true }); s.onload = resolve; s.onerror = () => reject(new Error(`could not load ${id}`)); document.head.append(s); });
}
async function drawGoogle(layers) {
  if (!window.google?.maps) {
    await loadScript(`https://maps.googleapis.com/maps/api/js?key=${encodeURIComponent(S.config.maps_browser_key)}&v=weekly`, "gmaps");
    const t0 = Date.now(); while (!window.google?.maps?.Map) { if (Date.now() - t0 > 8000) throw new Error("Google Maps did not initialise"); await new Promise((r) => setTimeout(r, 100)); }
  }
  window.gm_authFailure = () => { toast("Google Maps rejected the key (check referrer/API restrictions). Using OpenStreetMap.", true); drawLeaflet(S.layers); };
  const c = resetCanvas(); const g = google.maps;
  const map = new g.Map(c, { center: { lat: 19.07, lng: 82.03 }, zoom: 9, mapTypeId: "roadmap", streetViewControl: false, fullscreenControl: true });
  S.map = { remove() {} };
  const bounds = new g.LatLngBounds();
  for (const m of layers.markers) {
    const mk = new g.Marker({ map, position: { lat: m.lat, lng: m.lon }, title: `${m.name} · ${m.coordinates_provenance}`,
      icon: { path: g.SymbolPath.CIRCLE, scale: m.resources.length ? 8 : 5, fillColor: markerColor(m), fillOpacity: .95, strokeColor: "#222", strokeWeight: 1 } });
    mk.addListener("click", () => selectFacility(m)); bounds.extend(mk.getPosition());
  }
  for (const r of layers.routes) {
    const solid = ["DISPATCHED", "DELAYED", "RECEIVED"].includes(r.line_status);
    new g.Polyline({ map, path: r.path.map(([lat, lng]) => ({ lat, lng })), strokeColor: r.line_status === "INVALIDATED" ? "#a3231f" : "#0b4f8a",
      strokeOpacity: solid ? .9 : 0, strokeWeight: 3, icons: solid ? [] : [{ icon: { path: "M 0,-1 0,1", strokeOpacity: .9, scale: 3 }, offset: "0", repeat: "14px" }] });
  }
  if (layers.markers.length) map.fitBounds(bounds);
  S.gmap = map;
}
async function draw3d(layers) {
  window.CESIUM_BASE_URL = "https://cdnjs.cloudflare.com/ajax/libs/cesium/1.121.0/";
  if (!document.getElementById("cesium-css")) document.head.append(h("link", { id: "cesium-css", rel: "stylesheet", href: `${window.CESIUM_BASE_URL}Widgets/widgets.min.css` }));
  await loadScript(`${window.CESIUM_BASE_URL}Cesium.min.js`, "cesium");
  const c = resetCanvas(); const C = window.Cesium;
  const viewer = new C.Viewer(c, { globe: false, baseLayer: false, baseLayerPicker: false, geocoder: false, timeline: false, animation: false,
    homeButton: true, sceneModePicker: false, navigationHelpButton: false, infoBox: false, selectionIndicator: true });
  S.map = { destroy: () => viewer.destroy() };
  const tiles = await C.Cesium3DTileset.fromUrl(`https://tile.googleapis.com/v1/3dtiles/root.json?key=${encodeURIComponent(S.config.maps_browser_key)}`, { showCreditsOnScreen: true });
  viewer.scene.primitives.add(tiles);
  const byId = {};
  for (const m of layers.markers) {
    byId[m.facility_id] = viewer.entities.add({ id: m.facility_id, position: C.Cartesian3.fromDegrees(m.lon, m.lat, 650),
      point: { pixelSize: m.resources.length ? 12 : 8, color: C.Color.fromCssColorString(markerColor(m)), outlineColor: C.Color.BLACK, outlineWidth: 1, disableDepthTestDistance: Number.POSITIVE_INFINITY },
      label: { text: m.name, font: "12px sans-serif", pixelOffset: new C.Cartesian2(0, -18), showBackground: true, distanceDisplayCondition: new C.DistanceDisplayCondition(0, 40000), disableDepthTestDistance: Number.POSITIVE_INFINITY } });
  }
  for (const r of layers.routes) viewer.entities.add({ polyline: { positions: C.Cartesian3.fromDegreesArrayHeights(r.path.flatMap(([la, lo]) => [lo, la, 650])), width: 3,
    material: r.line_status === "INVALIDATED" ? C.Color.RED : C.Color.fromCssColorString("#0b4f8a") } });
  viewer.selectedEntityChanged.addEventListener((e) => { const m = e && layers.markers.find((x) => x.facility_id === e.id); if (m) selectFacility(m); });
  const target = layers.markers.find((m) => m.is_recipient) || layers.markers[0];
  if (target) viewer.camera.flyTo({ destination: C.Cartesian3.fromDegrees(target.lon, target.lat - 0.05, 4000), orientation: { pitch: C.Math.toRadians(-35) } });
  S.cesium = viewer;
}
function flyToSearch() {
  const v = $("mapSearch").value.toLowerCase(); if (!v || !S.layers) return;
  const m = S.layers.markers.find((x) => `${x.name} (${x.facility_id})`.toLowerCase() === v || x.facility_id.toLowerCase() === v || x.name.toLowerCase().includes(v));
  if (!m) return toast("No facility matches that search.", true);
  selectFacility(m);
  if (S.gmap && S.mapMode === "2d" && window.google?.maps) { S.gmap.panTo({ lat: m.lat, lng: m.lon }); S.gmap.setZoom(13); }
  else if (S.map?.setView) S.map.setView([m.lat, m.lon], 13);
  else if (S.cesium) S.cesium.camera.flyTo({ destination: window.Cesium.Cartesian3.fromDegrees(m.lon, m.lat - 0.03, 2500), orientation: { pitch: window.Cesium.Math.toRadians(-35) } });
}

/* ---------- agents ---------- */
function openDrawer() { $("agentDrawer").hidden = false; }
async function askAgent(name, request) {
  openDrawer();
  const pending = h("div", { class: "agent-answer" }, h("span", { class: "spinner" }), ` ${name} is reading the ledger…`);
  $("agentLog").prepend(pending);
  try {
    const r = await api(`/api/agents/${name}/run`, { method: "POST", body: { request, language: $("agentLang").value } });
    pending.replaceWith(renderAnswer(r, request)); if (name === "replan_watcher") refreshAll();
  } catch (e) { pending.replaceWith(h("div", { class: "notice bad" }, e.message)); }
}
function renderAnswer(r, request) {
  const p = r.provider || {};
  return h("div", { class: "agent-answer" },
    h("div", { style: "display:flex;gap:6px;flex-wrap:wrap;margin-bottom:6px" }, h("strong", {}, r.agent), statusBadge(r.status),
      h("span", { class: `badge ${p.mode === "GEMINI" ? "b-info" : "b-nc"}` }, p.mode === "GEMINI" ? `GEMINI · ${p.model}` : p.mode),
      h("span", { class: "badge b-nc" }, r.label)),
    request ? h("div", { class: "muted" }, `Q: ${request}`) : null,
    h("p", {}, r.answer),
    r.statements?.length ? h("ol", {}, r.statements.map((s) => h("li", {}, s.text, " ",
      ...s.event_ids.flatMap((id, i) => [i ? " · " : "", citeBtn(id)])))) : null,
    p.degraded_reason ? h("div", { class: "muted" }, `Fallback reason: ${p.degraded_reason}`) : null,
    r.unsupported_statements_dropped ? h("div", { class: "muted" }, `${r.unsupported_statements_dropped} uncited statement(s) were dropped.`) : null,
    r.translation_note ? h("div", { class: "muted" }, r.translation_note) : null,
    "speechSynthesis" in window ? h("button", { class: "btn sm", type: "button", onclick: () => speak(r) }, "🔊 Read aloud") : null,
    h("div", { class: "mono muted" }, `tools: ${r.tools_used.map((t) => t.tool + (t.error ? `!${t.error}` : "")).join(", ") || "none"} · run `, citeBtn(r.run_event_id)));
}
const SPEECH_LANG = { en: "en-IN", hi: "hi-IN", bn: "bn-IN", te: "te-IN", mr: "mr-IN", ta: "ta-IN", gu: "gu-IN", kn: "kn-IN",
  ml: "ml-IN", or: "or-IN", pa: "pa-IN", ur: "ur-IN" };
function speak(r) {
  speechSynthesis.cancel();
  const text = [r.answer, ...(r.statements || []).map((s) => s.text)].join(". ").replace(/evt_[0-9a-f]{16}/g, "");
  const u = new SpeechSynthesisUtterance(text.slice(0, 3000)); u.lang = SPEECH_LANG[$("agentLang").value] || "en-IN";
  speechSynthesis.speak(u);
}
function startDictation() {
  const Rec = window.SpeechRecognition || window.webkitSpeechRecognition;
  if (!Rec) return toast("Voice input is not supported in this browser. Type your question instead.", true);
  const rec = new Rec(); rec.lang = SPEECH_LANG[$("agentLang").value] || "en-IN"; rec.interimResults = false;
  $("micBtn").setAttribute("aria-pressed", "true"); $("micBtn").textContent = "● Listening…";
  rec.onresult = (e) => { $("agentQuery").value = e.results[0][0].transcript; };
  rec.onerror = (e) => toast(`Voice input failed: ${e.error}`, true);
  rec.onend = () => { $("micBtn").setAttribute("aria-pressed", "false"); $("micBtn").textContent = "🎙 Speak"; };
  rec.start();
}

/* ---------- wiring ---------- */
function wire() {
  document.querySelectorAll("nav.tabs button").forEach((b) => b.addEventListener("click", () => show(b.dataset.view)));
  $("identity").addEventListener("change", async (e) => { try { await signIn(e.target.value); await refreshAll(); toast(`Signed in as ${S.user.display}`); } catch (x) { toast(x.message, true); } });
  $("queueRefresh").addEventListener("click", () => act(async () => {}));
  $("queueSku").addEventListener("change", () => act(async () => {}));
  $("attestCancel").addEventListener("click", () => { $("attestPanel").hidden = true; });
  $("attestForm").addEventListener("submit", (e) => { e.preventDefault(); const f = e.target;
    const body = { facility_id: f.facility_id.value, sku: f.sku.value, present_qty: Number(f.present_qty.value), usable_qty: Number(f.usable_qty.value),
      expired_qty: Number(f.expired_qty.value || 0), evidence_ref: f.evidence_ref.value, client_event_id: `att_${crypto.randomUUID().replaceAll("-", "").slice(0, 24)}` };
    act(async () => { const r = await api("/api/verify/attest", { method: "POST", body }); $("attestPanel").hidden = true;
      toast(`Count recorded: ${r.finding} (variance ${fmt(r.variance_units)})${r.replans.length ? " — replan required" : ""}`); return r; }); });
  $("proposePlan").addEventListener("click", () => act(() => api("/api/plans", { method: "POST", body: { sku: $("planSku").value } }), "Options generated; awaiting approval"));
  $("planSku").addEventListener("change", renderAllocation);
  ["mapSku", "mapBlock", "mapTier"].forEach((id) => $(id).addEventListener("change", renderMap));
  $("mapSearch").addEventListener("change", flyToSearch);
  $("mode2d").addEventListener("click", () => { S.mapMode = "2d"; $("mode2d").setAttribute("aria-pressed", "true"); $("mode3d").setAttribute("aria-pressed", "false"); renderMap(); });
  $("mode3d").addEventListener("click", () => { S.mapMode = "3d"; $("mode3d").setAttribute("aria-pressed", "true"); $("mode2d").setAttribute("aria-pressed", "false"); renderMap(); });
  $("runWatcher").addEventListener("click", () => askAgent("replan_watcher", "Check whether any approved plan is now infeasible and draft a replan if needed."));
  fillSelect($("auditType"), EVENT_TYPES, (t) => t || "All event types");
  $("auditType").addEventListener("change", () => act(async () => {}));
  $("auditRefresh").addEventListener("click", () => act(async () => {}));
  $("auditExplain").addEventListener("click", () => askAgent("evidence_agent", "What happened in this case, why was each decision made, and what was the outcome?"));
  $("auditExport").addEventListener("click", async () => {
    try { const res = await fetch("/api/events.csv", { headers: { Authorization: `Bearer ${S.token}` } }); if (!res.ok) throw new Error(`Export failed (${res.status})`);
      const url = URL.createObjectURL(await res.blob()); const a = h("a", { href: url, download: "tathyon_ledger.csv" }); document.body.append(a); a.click(); a.remove(); URL.revokeObjectURL(url);
    } catch (e) { toast(e.message, true); } });
  $("loadOsm").addEventListener("click", () => act(async () => { const r = await api("/api/intake/osm-registry", { method: "POST", body: { district_key: $("osmDistrict").value } });
    toast(`${r.added} OpenStreetMap facilities added for ${r.district}, ${r.state} (fetch ${r.fetch_status})`); return r; }));
  $("loadSample").addEventListener("click", () => act(async () => { const r = await api("/api/intake/sample-dataset", { method: "POST", body: { district_key: $("sampleDistrict").value } });
    toast(`SAMPLE data loaded for ${r.district}: ${r.stock_rows} stock rows, ${r.prior_counts} prior counts, ${r.bed_staff_observations} bed/staff reports`); return r; }));
  $("resetWs").addEventListener("click", () => { if (!confirm("Start an empty workspace? The audit ledger keeps all history.")) return;
    act(() => api("/api/workspace/reset", { method: "POST" }), "Workspace reset"); });
  $("micBtn").addEventListener("click", startDictation);
  setInterval(async () => {
    if (document.hidden || !S.token || document.activeElement?.matches("input,textarea,select")) return;
    try { const hl = await api("/health"); if (hl.ledger.events !== S.lastEvents) await refreshAll(); else { S.updatedAt = new Date(); renderStatus(); } } catch { /* shown on next action */ }
  }, 15000);
  $("registryForm").addEventListener("submit", (e) => { e.preventDefault(); act(async () => { const r = await api("/api/intake/facility-registry", { method: "POST", body: await readFile($("registryFile")) });
    toast(`${r.accepted} facilities accepted, ${r.quarantined.length} quarantined`); return r; }); });
  $("stockForm").addEventListener("submit", (e) => { e.preventDefault(); act(async () => { const r = await api("/api/intake/stock-export", { method: "POST", body: await readFile($("stockFile")) });
    toast(`${r.accepted_rows} rows accepted (UNVERIFIED), ${r.quarantined_rows} quarantined`); return r; }); });
  $("intakeForm").addEventListener("submit", async (e) => { e.preventDefault(); const out = $("intakeResult"); out.replaceChildren(h("span", { class: "spinner" }));
    try { const img = $("intakeImage").files?.[0]; let r;
      if (img) { if (img.size > 4_000_000) throw new Error("Photo larger than 4 MB.");
        const b64 = await new Promise((res, rej) => { const fr = new FileReader(); fr.onload = () => res(String(fr.result).split(",")[1]); fr.onerror = rej; fr.readAsDataURL(img); });
        r = await api("/api/agents/intake_agent/image", { method: "POST", body: { image_base64: b64, mime_type: img.type } }); }
      else r = await api("/api/agents/intake_agent/run", { method: "POST", body: { request: $("intakeText").value } });
      out.replaceChildren(renderAnswer(r), r.candidate ? h("pre", { class: "mono" }, JSON.stringify(r.candidate, null, 2)) : ""); refreshAll();
    } catch (x) { out.replaceChildren(h("div", { class: "notice bad" }, x.message)); } });
  $("openAgents").addEventListener("click", openDrawer);
  $("closeAgents").addEventListener("click", () => { $("agentDrawer").hidden = true; $("openAgents").focus(); });
  $("agentForm").addEventListener("submit", (e) => { e.preventDefault(); const q = $("agentQuery").value.trim(); if (q) askAgent($("agentName").value, q); });
  document.addEventListener("keydown", (e) => { if (e.key === "Escape" && !$("agentDrawer").hidden) $("agentDrawer").hidden = true; });
}

async function boot() {
  wire();
  try {
    [S.config, S.status] = await Promise.all([api("/api/config/public"), api("/api/system/status")]);
    const users = S.config.demo_users;
    $("identity").replaceChildren(...Object.entries(users).map(([k, u]) => h("option", { value: k }, u.display)));
    const saved = store.get("tathyon.user"); const start = users[saved] ? saved : "dmo";
    $("identity").value = start;
    const tok = store.get("tathyon.token");
    if (tok) { S.token = tok; try { S.user = await api("/api/auth/me"); } catch { S.token = null; } }
    if (!S.token || !S.user) await signIn(start);
    const params = new URLSearchParams(location.search);
    const dist = (await api("/api/reference/districts")).districts;
    const opts = () => dist.map((d) => h("option", { value: d.key }, `${d.district}, ${d.state} — ${d.facilities} facilities (${d.fetch_status})`));
    $("osmDistrict").replaceChildren(...opts()); $("sampleDistrict").replaceChildren(...opts());
    await refreshAll();
    if (!S.ws.facilities) show("data"); else show(params.get("view") || "queue");
  } catch (e) {
    document.querySelector("main").prepend(h("div", { class: "notice bad" }, `Could not start: ${e.message}`));
  }
}
boot();
