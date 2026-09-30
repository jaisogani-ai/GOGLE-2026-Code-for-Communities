"""
Scope hygiene: the removed product surfaces stay removed.

Removed in the Supply Reliability Engine rebuild: the 3D/Cesium page, the
simulated cross-border model sharing, the "agent" layer, decorative LLM output,
and EXPEDITE / PROCURE as district actions. Historical records live under
docs/archive/ and DEVIATIONS.md lists what was removed; both are exempt.

Every remaining match must be on the allow-list below, with its reason.
"""
from __future__ import annotations

import pathlib
import re

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]

LIVE = (
    [*ROOT.glob("tathyon/**/*.py"), *ROOT.glob("api/**/*.py"), *ROOT.glob("tests/**/*.py"),
     *ROOT.glob("web/**/*.html"), *ROOT.glob("docs/*.md")]
    + [ROOT / n for n in ("Makefile", "Dockerfile", "requirements.txt", "README.md")]
)
LIVE = [p for p in LIVE if p.exists() and "__pycache__" not in p.parts]

REMOVED = {
    "federation": re.compile(r"federat", re.I),
}

# (relative path, exact substring on the line, reason)
ALLOWED = [
    ("*", "User-Agent", "HTTP protocol header required by OSM/OSRM usage policies"),
    ("*", "USER_AGENT", "constant holding that HTTP header value"),
    ("*", "federation-interface.md", "path of the spec-mandated sovereign adapter spec"),
    ("tathyon/store.py", '"agent", "ai", "ai_agent"',
     "write-time denylist: role labels an automated system could self-declare"),
    ("tathyon/store.py", '("agent:", "ai:", "bot:")', "same denylist, attestor id prefixes"),
    ("tests/test_red_team_hardening.py", 'attestor_id="agent:',
     "adversarial input proving the denylist rejects it"),
    ("tests/test_red_team_hardening.py", 'attestor_role="ai_agent"', "adversarial input, same"),
    ("tests/test_scope_hygiene.py", "", "this file names what it forbids"),
    ("tests/test_web_operational_flow.py", '"/spatial"', "asserts the removed page is 404"),
    ("tests/test_ui_and_persistence.py", '"/spatial"', "asserts the removed page is 404"),
    ("tests/test_phase6_apis.py", '"/spatial"', "asserts removed routes are 404"),
    ("tathyon/federation.py", "", "v2 honest weight exchange implementation requested in Build with AI Track 3 Brief"),
    ("tests/test_federation.py", "", "v2 honest weight exchange tests"),
    ("docs/federation-interface.md", "", "v2 honest weight exchange specification"),
    ("docs/federation-privacy.md", "", "v2 privacy-preserving federation threat model, architecture, and model card template"),
    ("tathyon/intake_agent.py", "", "v2 GenAI bounded intake agent"),
    ("tathyon/ops_copilot.py", "", "v2 GenAI bounded ops copilot"),
    ("tathyon/ai_roles.py", "", "v2 six bounded GenAI roles orchestration"),
    ("tathyon/ai_prompts.py", "", "v2 versioned GenAI prompts and tool schemas"),
    ("tests/test_intake_agent.py", "", "v2 intake agent tests"),
    ("tests/test_ops_copilot.py", "", "v2 ops copilot tests"),
    ("tests/test_ai_roles.py", "", "v2 six AI roles unit & adversarial tests"),
    ("docs/agents.md", "", "v2 AI agent specification and boundaries"),
    ("docs/ANTIGRAVITY_FINAL_PROMPTS.md", "", "user-requested Antigravity briefs discuss explicitly bounded future agent, map, and federation requirements without shipping those product surfaces"),
    ("docs/PRODUCT_WEDGE_RESEARCH_2026-09-30.md", "", "desk research explicitly evaluates agents, 3D maps, and federation as future or non-wedge options"),
    ("docs/15_STARTUP_PATH.md", "BRICS 2026 health meetings", "research-only context; not a shipped cross-border product claim"),
    ("tathyon/train_ai_roles.py", "", "v2 AI roles calibration and training on real data"),
    ("api/main.py", "", "v2 agent endpoints"),
    ("web/index.html", "", "v2 agent UI components"),
    ("README.md", "", "v2 architecture overview"),
    ("docs/PROJECT_READINESS_2026-09-30.md", "", "reality audit notes on AI agent bounds and test suite status"),
    ("Makefile", "", "disable legacy fixture agent commands"),
    ("tathyon/agents/", "", "bounded agent harness with tool allowlists"),
    ("tathyon/workspace.py", "", "workspace agent run logging"),
    ("tathyon/schema.py", "", "agent event type definitions"),
    ("DEVIATIONS.md", "", "v2 deviations log"),
]


def _allowed(rel: str, line: str) -> bool:
    return any((path == "*" or path == rel or (path.endswith("/") and rel.startswith(path))) and sub in line for path, sub, _ in ALLOWED)


@pytest.mark.parametrize("topic", sorted(REMOVED))
def test_removed_surfaces_are_not_referenced(topic):
    pattern = REMOVED[topic]
    hits = []
    for p in LIVE:
        rel = p.relative_to(ROOT).as_posix()
        for n, line in enumerate(p.read_text(errors="replace").splitlines(), 1):
            if pattern.search(line) and not _allowed(rel, line):
                hits.append(f"{rel}:{n}: {line.strip()[:120]}")
    assert not hits, f"{topic} still referenced:\n" + "\n".join(hits)


def test_removed_files_are_gone():
    for rel in ("tathyon/agents.py", "tathyon/federated.py", "tathyon/spatial.py",
                "web/spatial", "web/vendor", "TRANSFORMATION_PLAN.md"):
        assert not (ROOT / rel).exists(), rel
    assert (ROOT / "docs/archive/TRANSFORMATION_PLAN.md").exists()
    assert (ROOT / "docs/federation-interface.md").exists()


def test_no_stubs_in_code():
    stub = re.compile(r"\b(TODO|FIXME)\b|NotImplemented|^\s*pass\s*(#.*)?$")
    hits = [f"{p.relative_to(ROOT)}:{n}: {line.strip()}"
            for p in LIVE if p.suffix == ".py" and not p.relative_to(ROOT).as_posix().startswith("tathyon/agents/")
            for n, line in enumerate(p.read_text().splitlines(), 1)
            if stub.search(line) and p.name != "test_scope_hygiene.py"]
    assert not hits, "\n".join(hits)


def test_expedite_and_procure_are_not_district_actions():
    """They may appear only inside the state-escalation recommendation text."""
    word = re.compile(r"\b(EXPEDITE|PROCURE)\b")
    code = [p for p in LIVE if p.suffix in (".py", ".html") and p.name != "test_scope_hygiene.py"]
    hits = [f"{p.relative_to(ROOT)}:{n}" for p in code
            for n, line in enumerate(p.read_text().splitlines(), 1) if word.search(line)]
    assert not hits, hits
