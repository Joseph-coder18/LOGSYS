# LogMind-X Frontend Redesign: ChatGPT-Style AI Investigation Workspace

> **Goal**: Replace the current card-heavy, dashboard-first UI (plus 11 dead legacy files) with a clean, professional, **ChatGPT / Claude-inspired conversational AI workspace** powered by a unified component system (`shadcn/ui` + Tailwind v4, or Chakra UI).

---

## 1. Existing Component Inventory (`frontend/src/`)

### A. Active Components (Currently Used)
| File | Current Role | Redesign Disposition |
|---|---|---|
| `App.jsx` | Top header, incident sidebar, `Investigation` wrapper, delete dialog | **Refactor** into ChatGPT-style app shell (collapsible left sidebar + main chat canvas + URL session sync) |
| `Investigation.jsx` (512 lines, ~28 KB) | Monolith holding upload, overview, verification card, 5 tabs, and sheet state | **Decompose** into lightweight state hook (`useIncidentSession`) + centered Chat canvas + Right Inspector Drawer |
| `IncidentChat.jsx` | Chat tab panel (currently buried as Tab #5) | **Promote to Primary View** — centered ChatGPT-style message stream + floating bottom composer with file attach & scope filter |
| `ChatSourceSheet.jsx` | Slide-over dialog inspecting a cited observation | **Merge into Right Inspector Drawer** (Artifact/Context panel) |
| `InvestigationGraph.jsx` | Interactive `react-force-graph-2d` topology view | **Keep & Lazy-load** inside the Right Inspector Drawer (`Graph` tab) |
| `IncidentImpact.jsx` | Blast-radius & service impact panel | **Keep** inside Graph / Inspector Drawer |
| `TelemetryTimeline.jsx` + `TimelineFilters.jsx` | Filterable UTC event timeline | **Keep** inside Right Inspector Drawer (`Timeline` tab) |
| `HypothesisList.jsx` | Accordion of candidate hypotheses & 4-point checks | **Keep & Enhance** with confidence % + `causal-ranking` tab inside Inspector Drawer |
| `InvestigationTrace.jsx` | Step-by-step agent execution trace | **Keep** inside Inspector Drawer (`Trace` tab) |
| `IncidentSources.jsx` | Drag-and-drop file upload panel | **Streamline** into a compact Upload Dropzone Modal + inline chat composer attachment button |
| `IncidentOverview.jsx` | 4 summary metric cards | **Condense** into a compact top status bar / inspector summary |
| `components/ui/*` | `button.jsx`, `tabs.jsx`, `tooltip.jsx`, `alert-dialog.jsx`, `surfaces.jsx`, `prompt-kit/loader.jsx` | **Upgrade** with full `shadcn/ui` primitives (`Sheet`/`Drawer`, `Dialog`, `Popover`, `Command`, `DropdownMenu`, `ScrollArea`, `Badge`, `Separator`) |

### B. Dead / Unused Legacy Files (Safe to Delete — 18 files)
These files are **not imported anywhere** in the active app and bloat the codebase:
- `LeftSidebar.jsx` / `LeftSidebar.css`
- `RightSidebar.jsx` / `RightSidebar.css`
- `ChatWindow.jsx` / `ChatWindow.css`
- `ChatMessage.jsx` / `ChatMessage.css`
- `GraphPanel.jsx` / `GraphPanel.css`
- `QueryPanel.jsx` / `QueryPanel.css`
- `ResultsPanel.jsx` / `ResultsPanel.css`
- `UploadPanel.jsx` / `UploadPanel.css`
- `Toast.jsx` / `Toast.css`
- `EvidenceCard.jsx`
- `AgentTrace.jsx`

---

## 2. ChatGPT / Claude-Style Layout & UX Architecture

```text
+--------------------+------------------------------------------+----------------------------+
|  LEFT SIDEBAR      |  CENTER CONVERSATIONAL WORKSPACE         |  RIGHT INSPECTOR DRAWER    |
|  (260px, collapse) |  (flex-1, centered max-w-3xl stream)     |  (420px, opens on demand)  |
|                    |                                          |                            |
|  [+ New Incident]  |  Top Context Bar (sticky, minimal):      |  Tabs:                     |
|                    |  [user-db · Supported · 96%] [Inspect >] |  [Source] [Graph]          |
|  Incidents         |                                          |  [Timeline] [Hypotheses]   |
|  > DB Pool Drop    |  --------------------------------------  |  [Causal Rank] [Trace]     |
|  > Checkout 504    |  User: What caused the latency spike?    |                            |
|                    |                                          |  Shows:                    |
|  Preview           |  LogMind-X:                              |  - Cited raw log/metric    |
|  > Sample Incident |  The pool limit on user-db dropped from  |  - ForceGraph topology     |
|                    |  100 to 20 at 02:29:15 [1], saturating   |  - Unsupervised causal     |
|  ----------------  |  connections [2] before auth-service     |    ranker scores           |
|  (o) API Online    |  timed out [3].                          |  - Counterfactual fix      |
|  [Light / Dark]    |                                          |    simulator (/remediate)  |
|                    |  [96% conf] [Audit: Verified 100%]       |                            |
|                    |  [Copy] [View Evidence (3)]              |                            |
|                    |                                          |                            |
|                    |  +------------------------------------+  |                            |
|                    |  | [+] Ask about logs, metrics, RCA...|  |                            |
|                    |  |     [Service: All] [UTC]   [Send ^]|  |                            |
|                    |  +------------------------------------+  |                            |
+--------------------+------------------------------------------+----------------------------+
```

### Key UX Principles (No "AI-Generated Card Soup")
1. **Chat-First Centerpiece**: Clicking an incident opens directly into the conversation. No giant wall of cards before you can ask a question.
2. **Unboxed Assistant Prose**: Assistant messages render cleanly on the canvas (like ChatGPT/Claude) with crisp typography (`Inter` + `JetBrains Mono` for timestamps/IDs), inline clickable citation pills (`[1]`, `[2]`), and a subtle metadata footer (`95% confidence · Audit Verified · Copy`).
3. **Floating Pill Composer**: Rounded input bar anchored at the bottom with:
   - `[+]` button to attach logs/metrics/JSON/screenshots directly into the session.
   - Scope pill (`Service`, `UTC Window`) inside a compact `Popover`.
   - Starter prompt chips when the session is empty, or an inline Upload Dropzone when no telemetry has been uploaded yet.
4. **On-Demand Right Inspector ("Artifact" Panel)**:
   - Closed by default for a clean, focused chat experience.
   - Slides open smoothly when clicking any citation `[1]`, `Inspect Graph`, `Timeline`, `Hypotheses`, or `Causal Ranking`.
5. **Polished Color & Dark/Light Mode**:
   - Neutral zinc/slate palette (`#09090b` / `#18181b` in dark mode; `#ffffff` / `#f4f4f5` in light mode) with restrained semantic accents (emerald for `Supported`, amber for `Insufficient Evidence`, rose for `Flagged/Error`) — avoiding neon gradients and heavy box shadows.

---

## 3. Component Library Strategy: `shadcn/ui` vs `Chakra UI`

> **Architectural Note**: `frontend/components.json` is already initialized for **shadcn/ui** (`base-nova` style with Tailwind v4 + `@base-ui/react`). Installing both Tailwind+shadcn AND full Chakra UI v3 (Emotion CSS-in-JS) in one app can cause CSS reset conflicts and inflate the JS bundle.

Two clean paths:
- **Option A (Recommended): Pure `shadcn/ui` + Tailwind v4**
  - Uses existing `components.json` and Tailwind v4 setup.
  - Zero CSS-in-JS runtime conflicts, fastest build, native ChatGPT/Vercel/Linear look.
  - Add missing primitives: `Sheet` (right drawer), `Popover` (scope filter), `DropdownMenu`, `ScrollArea`, `Separator`, `Dialog`, `Skeleton`.
- **Option B: Hybrid (`Chakra UI` Layout/Feedback + `shadcn` primitives, or full `Chakra UI v3`)**
  - Requires disabling Chakra's global Preflight (`disableGlobalStyle` / scoped theme) so Tailwind utilities don't collide, or replacing Tailwind styling with Chakra `Box`/`Flex`/`Drawer` props.

---

## 4. Surfacing All Backend & Novelty Features in the New UI

| Backend Feature / Endpoint | Where It Appears in the New UI |
|---|---|
| `confidence_score` (`0.0–1.0`) | Compact pill in Top Context Bar + Assistant message footer (`96% confidence`) |
| **Claim Auditor** (`audit_status`, `audit_warnings`, `audit_score`) | Inline shield badge on assistant replies (`Audit: 100% Verified` in green, or `Flagged` with expandable warning tooltip showing exact numeric/timestamp mismatch) |
| **Unsupervised Causal Ranker** (`GET /causal-ranking`) | Dedicated `Causal Rank` tab in Right Inspector showing ranked suspect services, score breakdown (`Temporal`, `Upstream`, `Modal`), and downstream blast radius |
| **Verification & Hypotheses** (`POST /verification`) | Top Context Bar status pill (`Verify` / `Supported · user-db`) + `Hypotheses` tab in Right Inspector showing 4-point checks |
| **Missing Telemetry / Next Check** (`next_check`, `GET /next-step`) | Subtle inline callout banner above composer when evidence is incomplete, with `[Upload missing telemetry]` button |
| **Counterfactual Remediation** (`POST /remediate`) | Actionable button inside `Causal Rank` / `Hypotheses` inspector tab showing simulated rollback steps |
| **Interactive Graph & Timeline** (`/graph`, `/observations`) | `Graph` & `Timeline` tabs in Right Inspector Drawer (lazy-loaded to keep initial bundle < 300 KB) |

---

## 5. Phased Implementation Plan

### Phase 1: Cleanup & Design System Foundation
1. Delete the 18 dead legacy component/CSS files (`LeftSidebar`, `RightSidebar`, `ChatWindow`, `ChatMessage`, `GraphPanel`, `QueryPanel`, `ResultsPanel`, `UploadPanel`, `Toast`, `EvidenceCard`, `AgentTrace`).
2. Establish clean dark/light design tokens in `src/index.css` (neutral zinc surfaces, `Inter` + `JetBrains Mono`, remove cluttered legacy CSS classes).
3. Add required `shadcn/ui` (or Chakra UI) primitives (`Sheet`, `Popover`, `ScrollArea`, `DropdownMenu`, `Separator`).
4. Add `getCausalRanking(sessionId)` to `src/services/api.js`.

### Phase 2: ChatGPT-Style App Shell & Custom Hook
1. Extract `src/hooks/useIncidentSession.js` from `Investigation.jsx` to manage session loading, uploads, verification, causal ranking, and chat state cleanly.
2. Refactor `src/App.jsx` into a collapsible **Sidebar + Main Canvas** layout with URL hash/path sync (`?session=...`) so refreshing preserves the active incident, plus Dark/Light theme toggle.

### Phase 3: Conversational Centerpiece & Composer
1. Build the **Top Context Bar** (incident title, `Supported / Insufficient` badge + confidence %, `Verify` button, `Inspect` toggle).
2. Redesign **`IncidentChat.jsx`** as a true ChatGPT-style stream:
   - Empty-state greeting + inline drag-and-drop upload card + starter prompt chips.
   - Unboxed assistant prose with clickable `[1]` citation pills, **Claim Auditor** verification badge (`audit_score` + `audit_warnings`), and copy action.
   - Bottom floating **Composer** with `[+]` file attach, Service/UTC scope popover, and Enter-to-send.

### Phase 4: Right Inspector Drawer ("Artifact" Panel) & Verification
1. Build **`InspectorDrawer.jsx`** hosting `Source`, `Graph`, `Timeline`, `Hypotheses`, `Causal Rank`, and `Trace` tabs (lazy-loaded).
2. Wire citation clicks directly to open the `Source` tab in the drawer; wire `Verify` / `Inspect` to show Hypotheses & Unsupervised Causal Ranking.
3. Verify `npm run build` (confirming clean bundle splitting) and test all flows in the live browser against the FastAPI backend.

