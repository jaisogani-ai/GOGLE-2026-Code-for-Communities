# TATHYON Sovereign Health-Operations Design System

## 1. Overview & Operational Principles

TATHYON is a sovereign public health resource intelligence and allocation engine designed for high-stress district and national command operations. Health officials, District Health Officers (DHOs), and Chief Medical Officers (CMOs) operate under strict time pressure, severe logistical bottlenecks, and unreliable field reports.

The user interface adheres to six core principles:
1. **Desktop-First at 1440px Canvas**: Optimized for dense data consoles at 1440px wide, responding gracefully down to 1280px laptops and field mobile viewports.
2. **Deterministic Clarity over Decorative Flourish**: Zero gradients, glassmorphism, dimensional CSS transforms, or frivolous animations.
3. **8-Pixel Spatial Cadence**: Rigid baseline rhythm governing paddings, margins, gutters, and card radii.
4. **Reserved Semantic Status Colors**: Red, amber, and green indicate strictly operational conditions and are never shown without textual or iconic pairing.
5. **Human Review**: All machine intelligence is marked `AI suggestion — human decides`. Configure consequential action roles from applicable department/state procedures; TATHYON policy does not itself establish legal authority.
6. **Bilingual Accessibility**: Critical actions, column headers, and statuses carry accessible English and Hindi (हिन्दी) equivalents.

---

## 2. Color Palette & Semantics

| Token | Hex Value | Semantic Purpose |
| :--- | :--- | :--- |
| `--color-teal-700` | `#0F766E` | Primary navigation, active indicators, and high-priority primary actions |
| `--color-teal-800` | `#115E59` | Primary action hover state and active click states |
| `--color-teal-50` | `#F0FDFA` | Subtle primary callout tint, selection backgrounds |
| `--color-surface` | `#FFFFFF` | Primary card, table, and modal background |
| `--color-surface-subtle` | `#F8FAFC` | Secondary panel backgrounds, alternating zebra rows, table headers |
| `--color-border` | `#E2E8F0` | Structural borders, dividers, and card boundaries |
| `--color-border-strong` | `#CBD5E1` | Input focus rings, high-contrast dividers |
| `--color-text-main` | `#0F172A` | Primary typography, headers, high-contrast metrics |
| `--color-text-muted` | `#64748b` | Field labels, secondary timestamps, provenance disclaimers |
| `--color-red-600` | `#DC2626` | Imminent stockout, break-glass override, rejected donor gate |
| `--color-red-50` | `#FEF2F2` | Critical alert background tint |
| `--color-amber-600` | `#D97706` | Unverified inventory warning, impending risk, stale data |
| `--color-amber-50` | `#FFFBEB` | Warning callout background, persistent demo data banner |
| `--color-green-700` | `#15803D` | Physically verified stock, approved transfer, intact hash chain |
| `--color-green-50` | `#F0FDF4` | Verified status badge background, positive delivery receipt |

---

## 3. Typography & Rhythm

- **Display Headings (`font-display`)**: Georgia, "Times New Roman", serif. Used for console title, major section headings, and module headers.
- **Operational Data (`font-sans`)**: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif. Used for dense data tables, forms, buttons, and navigation.
- **Ledger & Metrics (`font-mono`)**: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace. Used for hashes, stock counts, event offsets, and formulas.

### Type Scale
- Headline / Display: 20px (weight: 700, serif)
- Section Heading: 14px (weight: 700, uppercase tracking +0.5px)
- Dense Table Text: 14px (weight: 400-600, line-height: 1.4)
- Form Labels & Subtitles: 12px (weight: 600)
- Provenance Badges & Micro-copy: 11px / 10px (weight: 700, uppercase)

### Spacing Scale (8px Grid)
- `spacing-1`: 4px (micro padding)
- `spacing-2`: 8px (dense button gap, table cell vertical padding)
- `spacing-3`: 12px (card inner gap)
- `spacing-4`: 16px (card padding, grid gutters)
- `spacing-6`: 24px (section margins, page outer padding)
- `spacing-8`: 32px (major layout separations)

---

## 4. Operational Surfaces & States

1. **Persistent Synthetic Demo Banner**:
   - Amber alert header anchored to the top of every screen: `SYNTHETIC DEMO DATA — Bastar District Operations Sandbox. Not for live clinical reliance.`
2. **Context & Role Switcher**:
   - Header controls allow toggling between Chief Medical Officer (CMO), District Health Officer (DHO), Field Verifier, and Facility Incharge.
3. **Empty / Loading / Error Triad**:
   - Every data table and view renders dedicated loading skeletons, clear empty explanations, and actionable error states with retry buttons.
4. **Slide-Over Detail Drawer**:
   - Right-anchored modal drawer (420px wide) providing deep inspectability into facility records, contributing risk reasons, and cryptographic event payloads without losing page position.
5. **Interactive 12-Step Phantom Trap Walkthrough**:
   - Step-by-step interactive synthetic demo illustrating how the verification and CP-SAT workflow behaves under generated phantom-inventory scenarios. Any displayed 80.87-day figure is a simulation result, not an observed clinic outcome.
