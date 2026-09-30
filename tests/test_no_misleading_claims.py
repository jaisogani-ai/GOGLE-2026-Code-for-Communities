"""The UI must never claim live government data, GPS/satellite tracking, or unmeasured metrics."""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
UI = [ROOT / "web" / "index.html", ROOT / "web" / "app.js"]
FORBIDDEN = [
    (re.compile(r"\blive (government|dvdms|e-aushadhi|stock feed|gps|vehicle)", re.I), "live-source claim"),
    (re.compile(r"satellite (tracking|tracks)", re.I), "satellite tracking claim"),
    (re.compile(r"\b(9[0-9]|100)% (accura|confiden)", re.I), "unmeasured accuracy claim"),
    (re.compile(r"real[- ]time (government|national) (data|feed)", re.I), "real-time government feed claim"),
]


def test_ui_makes_no_misleading_claims():
    hits = []
    for path in UI:
        for n, line in enumerate(path.read_text().splitlines(), 1):
            for pattern, label in FORBIDDEN:
                m = pattern.search(line)
                if m and not re.search(r"\b(no|not|never|without)\b[^.]{0,25}$", line[:m.start()], re.I):
                    hits.append(f"{path.name}:{n}: {label}: {line.strip()[:120]}")
    assert not hits, "\n".join(hits)


def test_every_number_badge_path_has_provenance_labels():
    js = (ROOT / "web" / "app.js").read_text()
    for label in ("REAL · OSM", "USER-SUPPLIED", "VERIFIED COUNT", "SAMPLE", "NOT CONFIGURED"):
        assert label in js or label.replace(" ", "_") in js or label in (ROOT / "web" / "index.html").read_text(), label
