# TATHYON: Video Recording & Demo Checklist

Use this checklist before recording the 3–5 minute demonstration video for Build with AI: Code for Communities 2.0 (Track 3).

---

## 1. Environment & Pre-Flight Configuration

- [ ] **Real Gemini API Active (Crucial 25% Rubric Gate)**:
  - Verify that `GEMINI_API_KEY` is set in your environment:
    ```bash
    export GEMINI_API_KEY="AIzaSy..."
    ```
  - Confirm live API status by calling `/audit/narrative`: the response must report `"provenance": "GEMINI_2_5_FLASH"` (or live model), NOT `[SIMULATED FIXTURE]`.
- [ ] **Display Resolution & Framing**:
  - Set screen recording resolution to **1920×1080 (1080p, 16:9)**.
  - Zoom browser UI to **100% or 110%** so badge text and numbers are crisply legible on mobile and laptop screens.
  - Close unused browser tabs, bookmarks bar, and system notification popups.
- [ ] **Data Seed Pinned**:
  - Verify container or local server was started with seed `20260928` (`make seed` or `./app/entrypoint.sh`).
  - Open `/health` in a browser tab to confirm `"data": {"loaded": true}` and `"chain": {"intact": true}`.

---

## 2. On-Screen Visual Quality & Provenance Checks

- [ ] **Provenance Badges Visible**:
  - Ensure every card displaying numbers has its visible provenance badge:
    - `[SYNTHETIC e-Aushadhi EXTRACT]` on stock data;
    - `[CAUSAL TRUST SCORER]` on probability scores;
    - `[OR-TOOLS CP-SAT]` on allocation routes;
    - `[SHA-256 EVENT LOG]` on attestation receipts.
- [ ] **No Dead Clicks or Long Freezes**:
  - Pre-warm the browser so Leaflet map tiles for Bastar district render smoothly without grey flashing.
  - Pre-test button transitions between all 5 tabs:
    1. Verification Queue
    2. Field Attestation (Count Sheet)
    3. Allocation Engine
    4. CMO Approval & Break-Glass
    5. Delivery Reconciliation & Provenance Brief
- [ ] **Gemini Blank-to-Fill Demonstration**:
  - Have the sample count sheet image ready in your file picker.
  - Confirm on screen that smudged or ambiguous digits show as an empty editable input box with a yellow/amber border (`Confidence < 0.70`), proving the human-in-the-loop doctrine.
- [ ] **Clickable Provenance Citations**:
  - In View 5, click at least one `[EVT-004]` tag in the narrative to demonstrate that the text jumps directly to the corresponding event in the cryptographic audit log.

---

## 3. Audio & Voiceover Quality

- [ ] Clear external microphone (avoid built-in laptop mics with fan noise).
- [ ] Script timing strictly between **3 minutes 15 seconds** and **4 minutes 45 seconds**.
- [ ] Pacing matches screen actions: never speak about a feature before clicking it.

---

## 4. Post-Recording Verification

- [ ] Video renders in MP4 format (H.264 video codec, AAC audio).
- [ ] File uploaded to an accessible, unlisted link (YouTube, Loom, or Google Drive with "Anyone with link can view").
- [ ] Link tested in an incognito browser window without login.
