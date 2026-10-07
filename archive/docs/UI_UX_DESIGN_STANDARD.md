# UI/UX Base Design Standard: Scandinavian Tactile Minimalist

## 1. Philosophy & Aesthetic Foundation

This document defines the authoritative, mandatory UI/UX design standard for all CIS AI projects and web applications under this directory. It derives from the reference designs established in September 2026, synthesizing Scandinavian minimalism, Apple Card tactile clarity, and Linear operational density.

### Core Principles
1. **No AI-Slop:**
   - Strictly forbidden: rainbow gradient backgrounds, blurry purple/neon dropshadows, arbitrary 0–100 pseudo health meters, 24px-padded empty bubbly cards, and slow bouncy easing animations.
2. **Tactile Density & Craft:**
   - Every pixel serves a purpose. Generous information density without visual noise.
   - 1px crisp structural borders (`#E5E5DF`) rather than diffuse shadows for component elevation.
   - Distinct surface hierarchy: Warm desktop canvas (`#ECECE8`) $\rightarrow$ Floating application window (`#F8F8F6`, `rounded-[28px]`) $\rightarrow$ Crisp pure white card surfaces (`#FFFFFF`).
3. **Signature Chromatic Restraint:**
   - High-contrast neutral palette.
   - **Signature Spring Lime Green (`#66CC55` / `#72D563`):** Reserved for active navigation pills, peak chart highlights, and primary positive execution actions. Paired with dark charcoal text/icons (`#141814`) for maximum legibility.
   - **Matte Charcoal (`#1E201E` / `#232522`):** Grounded, understated black for primary structural CTAs (`+ Catat Transaksi`, `+ New`). Text on dark accent is `#FFFFFF`.
4. **Data Rigor & Tabular Numerals:**
   - All financial amounts, counters, timestamps, IDs, and tabular figures MUST use `font-variant-numeric: tabular-nums;`.

---

## 2. Design Tokens & Color Palette

### 2.1 CSS Variables Foundation
```css
:root {
  /* Viewport & Canvas */
  --canvas-desktop: #ECECE8;        /* Outer desktop canvas / backdrop */
  --canvas-app: #F8F8F6;            /* Main app window background */
  --canvas-card: #FFFFFF;           /* Card & container surface */
  --canvas-subtle: #F2F2ED;         /* Secondary containers, inactive rails */
  --canvas-muted: #EFEFEA;          /* Table header bg, disabled tracks */

  /* Structural Borders */
  --border-structural: #E5E5DF;     /* 1px primary card border */
  --border-divider: #EFEFEA;        /* Table row borders, sub-dividers */
  --border-focus: #1A1D1A;          /* Active focus ring */

  /* Signature Accents */
  --accent-lime: #66CC55;           /* Primary highlight, active nav pill, peak chart bar */
  --accent-lime-hover: #5ABF4A;
  --accent-lime-contrast: #141814;  /* High-contrast dark text on lime */
  --accent-dark: #1E201E;           /* Charcoal black primary CTA, calendar widget */
  --accent-dark-hover: #2B2E2B;
  --accent-dark-contrast: #FFFFFF;  /* White text on dark accent */

  /* Semantic Status Pills (Pastel Background + Saturated Text) */
  --status-success-bg: #E8F8EA;
  --status-success-text: #1E7E34;
  --status-warning-bg: #FFF3E0;
  --status-warning-text: #B45309;
  --status-danger-bg: #FDE8E8;
  --status-danger-text: #DC2626;
  --status-info-bg: #EBF5FF;
  --status-info-text: #0369A1;
  --status-purple-bg: #F3E8FF;
  --status-purple-text: #7E22CE;

  /* Typography */
  --text-primary: #1A1D1A;          /* Headings, metric values, table titles */
  --text-secondary: #4A4D48;        /* Body text, table rows */
  --text-muted: #787A74;            /* Captions, labels, timestamps */
  --text-tertiary: #A2A49D;         /* Micro headers, inactive icons */

  /* Radii */
  --radius-window: 28px;            /* Floating application window container */
  --radius-card: 16px;              /* Standard cards & workbench containers */
  --radius-card-sm: 12px;           /* Sub-cards, icon boxes, inputs */
  --radius-pill: 9999px;            /* Status badges, active nav indicators */
}
```

---

## 3. Structural Layout Standards

### 3.1 The 3-Tier Canvas Architecture
1. **Level 0 (Desktop Canvas):**
   - Full viewport `min-h-screen bg-[var(--canvas-desktop)] p-3 sm:p-5 lg:p-6 flex flex-col items-center justify-center`.
2. **Level 1 (Application Window):**
   - Centered floating container `w-full max-w-[1600px] min-h-[calc(100vh-2.5rem)] bg-[var(--canvas-app)] rounded-[var(--radius-window)] border border-[var(--border-structural)] shadow-xl flex overflow-hidden`.
3. **Level 2 (Navigation & Workbench):**
   - Fixed Left Navigation Rail (`w-[230px]`) + Fluid Workbench Canvas + Optional Context Action Rail (`w-[340px]`).

---

## 4. Reusable Component Archetypes

### 4.1 Left Navigation Rail
- Micro-uppercase category headers (`text-[10px] font-bold tracking-wider text-[var(--text-tertiary)] uppercase px-3 py-1.5`).
- Active State: Solid Lime pill (`bg-[var(--accent-lime)] text-[var(--accent-lime-contrast)] font-bold rounded-xl shadow-xs`).
- Inactive State: Subtle hover (`hover:bg-[#EFEFEA] text-[var(--text-secondary)] rounded-xl`).
- Aligned count badges on the right (`bg-black/10 text-[10px] font-bold px-1.5 py-0.5 rounded-full`).
- Bottom user profile pill with quick-toggle actions.

### 4.2 4-Card Top Stat Strip
- 4-column horizontal card grid (`grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4`).
- White card (`bg-white border border-[var(--border-structural)] rounded-2xl p-4 shadow-xs`).
- Left: Rounded icon box (`rounded-xl bg-[#F2F2ED] p-2 h-9 w-9 flex items-center justify-center`).
- Center/Right: Label above (12px muted), large bold tabular metric (26px–30px), and status/delta pill.

### 4.3 Analytical Takeaway Banner
- Full-width callout card at section bottoms or under KPI rows (`bg-[#EBF7EE] border border-[#D0EBD5] rounded-2xl p-4 flex items-center gap-3.5`).
- Sparkle / Lightbulb icon in emerald circle.
- 1–2 sentence high-impact operational finding with highlighted metrics.

### 4.4 Slide-Out Context Drawer (Sheet)
- Replaces disruptive modal dialogs for deep row inspection.
- Emerges from the right edge (`w-[480px] max-w-full bg-white border-l border-[var(--border-structural)] shadow-2xl`).
- Contains: Sticky header, status alert banner, definition list, photo/receipt preview, and bottom dual-action triggers.

### 4.5 High-Density Data Tables
- Row density: 44px–52px.
- Subtle 1px dividers: `border-b border-[#EFEFEA]`.
- First column: Entity avatar initial + primary title + secondary muted caption.
- Status column: Pastel pill badge (`rounded-full px-2.5 py-0.5 text-[11px] font-semibold`).
- Right column: Clean inline trigger (`Detail >` or `ChevronRight`).

### 4.6 Segmented Progress Tracks
- Horizontal bar `h-2.5 rounded-full bg-[#EFEFEA] overflow-hidden flex`.
- Proportional color segments (e.g. Need `#38BDF8`, Want `#FF3B69`, Saving `#66CC55`).
- Dot legend underneath with tabular amount and percentages.

---

## 5. Chart & Data Visualization Guidelines
- **Bar Charts:**
  - Neutral muted bars for baseline/historical data (`#E0E0DB` or `#D4D4CE`).
  - **Spring Lime (`#66CC55`)** highlight for the peak spending day or current active day.
  - Dotted horizontal benchmark lines with pill labels.
- **Donut & Radial Gauges:**
  - Clean stroke width (12px–16px).
  - High-contrast center metric (bold tabular count or percentage).
- **Bullet Benchmark Graphs:**
  - Horizontal bar with vertical target tick mark indicating the budget/target ceiling.
