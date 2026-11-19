# LogMind‑X Implementation Plan – Phases 2‑5

## Overview
This document records the team's proposed tasks for the next LogMind-X milestones. The Phase 1 graph workflow is implemented, with parser, provenance, retrieval, and final verification checks still open in the project-level `LOGSYS_IMPLEMENTATION_PLAN.md`. The review on 19 November 2026 confirmed 57 backend tests and five graph tests passing.

Use the original project plan's phase numbering when assigning work. The counterfactual-remediation section below maps to its Phase 5. Evaluation and rehearsal map to Phase 4. UI integration and polish span the implementation phases. The task lists below remain proposals for the separate experimental endpoints until they are reconciled with the existing incident-verification workflow.

---

## Phase 2 – Contradiction‑Aware Hypothesis Verification

| Task | Description | Code Location | UI Demo | Acceptance Metric |
|------|-------------|---------------|---------|-------------------|
| **2.1 Review & Refactor `hypothesis.py`** | Ensure it consumes `TemporalEdgeCounts` and node timestamps. Separate scoring functions for temporal consistency, cross‑modal support, contradiction detection, and evidence completeness. | `backend/services/hypothesis.py` | N/A | Unit‑test coverage ≥ 90 % for each scoring function. |
| **2.2 Add unit tests** | Test each score branch with synthetic graphs (e.g., contradictory edges, missing timestamps). | `backend/tests/test_hypothesis.py` | N/A | All tests pass (`pytest`). |
| **2.3 API endpoint `/verify-claim`** (already stubbed) | Implement request validation, invoke `hypothesis.verify`, return `ClaimVerification` model with gate decision and scores. | `backend/routes.py` | N/A | Endpoint returns 200 OK and correct JSON schema. |
| **2.4 Front‑end button** | Add “Verify Claim” button in `Investigation.jsx` that calls `api.verifyClaim` and displays a modal with gate decision, score breakdown, and color‑coded edge highlights (e.g., red for contradictions). | `frontend/src/components/Investigation.jsx` | Click button → modal with scores and highlighted edges. | Manual UI test: contradictions highlighted in red, scores displayed correctly. |
| **2.5 Documentation** | `docs/hypothesis_verification.md` describing scoring logic, thresholds, and UI flow. | `docs/` | N/A | Docs build without errors. |

---

## Phase 3 – Adaptive Investigation & Safe Abstention

| Task | Description | Code Location | UI Demo | Acceptance Metric |
|------|-------------|---------------|---------|-------------------|
| **3.1 Extend `investigation_policy.py`** | Incorporate new edge types (`supports`, `contradicts`) into deficit detection and abstention logic. Return `NextStepResult` with suggested modality/service and rationale. | `backend/services/investigation_policy.py` | N/A | Unit‑test coverage ≥ 90 %. |
| **3.2 Add tests** | Scenarios: missing modality, contradictory evidence, safe‑abstain trigger. | `backend/tests/test_investigation_policy.py` | N/A | All tests pass. |
| **3.3 API `/next-step`** (stub) | Implement logic to call policy, return suggestion and confidence. | `backend/routes.py` | N/A | Endpoint returns correct schema. |
| **3.4 UI panel** | Add “Next Step” sidebar in `InvestigationGraph.jsx` showing suggestion, confidence, and action button to request next step. Highlight nodes that caused abstention (e.g., orange). | `frontend/src/components/InvestigationGraph.jsx` | Sidebar updates after “Verify Claim”. | UI shows suggestion, button works, highlighted nodes match policy output. |
| **3.5 Documentation** | `docs/investigation_policy.md` covering adaptive logic and UI integration. | `docs/` | N/A | Docs render correctly. |

---

## Phase 4 – Counterfactual Remediation Validator

| Task | Description | Code Location | UI Demo | Acceptance Metric |
|------|-------------|---------------|---------|-------------------|
| **4.1 Implement `remediation.py`** | Compute causal impact of removing a node, simulate counterfactual graph, produce `RemediationAction` with confidence score. | `backend/services/remediation.py` | N/A | Unit‑test coverage ≥ 90 %. |
| **4.2 Tests** | Cases: deployment rollback, config change, generic node removal; verify confidence calculations. | `backend/tests/test_remediation.py` | N/A | All tests pass. |
| **4.3 API `/remediate`** (stub) | Hook into service, return `RemediationResult` with suggested actions and impact metrics. | `backend/routes.py` | N/A | Endpoint returns expected JSON. |
| **4.4 UI integration** | After a claim is verified, show “Remediate” button in `Investigation.jsx`. Clicking opens a modal listing suggested actions, confidence, and projected symptom reduction. Highlight affected sub‑graph in the graph view. | `frontend/src/components/Investigation.jsx` | Modal with actions appears, graph updates on selection. | UI accurately reflects remediation suggestions; actions can be triggered (mock). |
| **4.5 Documentation** | `docs/remediation_validator.md` describing algorithm, API, UI flow, and interpretation of confidence scores. | `docs/` | N/A | Docs validated. |

---

## Phase 5 – UI Integration & Final Polish

| Task | Description | Code Location | UI Demo | Acceptance Metric |
|------|-------------|---------------|---------|-------------------|
| **5.1 Edge legend & toggle** | Consolidate all edge colors (`depends_on`, `propagates_to`, `precedes`, `supports`, `contradicts`). Add a legend component and a toggle to show/hide temporal edges. | `frontend/src/components/EdgeLegend.jsx` & `InvestigationGraph.jsx` | Legend visible, toggle works. | Visual regression test passes. |
| **5.2 Consistent labeling** | Ensure `relationshipLabel` returns human‑readable text for all new edge types. | `frontend/src/utils/relationshipLabel.js` | Hover tooltip shows correct label. | All tooltips verified. |
| **5.3 End‑to‑end demo script** | Script (`scripts/demo.sh` / PowerShell) that ingests sample logs, finalizes session, runs verification, next‑step, and remediation, asserting UI updates via Cypress tests. | `scripts/` & `frontend/cypress/` | CI run passes. | CI green. |
| **5.4 README & Release Notes** | Update top‑level `README.md` with new API endpoints, UI features, and a quick‑start guide. Add `RELEASE_NOTES.md` summarizing changes. | `README.md`, `RELEASE_NOTES.md` | N/A | Docs build. |
| **5.5 Full test suite** | Run `pytest backend/tests` and `npm test` (or `pnpm test`). Ensure overall coverage ≥ 90 % and no regressions. | N/A | N/A | All tests pass. |

---

## Measurement & Validation
- **Code Coverage**: Use `coverage.py` for backend and `nyc` for frontend; target ≥ 90 %.
- **Performance**: Verify that new endpoints respond within 200 ms for typical payloads (use `ab` or `wrk`).
- **UI Accuracy**: Automated Cypress tests validate that edge colors, legends, and modals reflect the underlying data.
- **User Acceptance**: Conduct a walk‑through with a stakeholder; collect feedback on clarity of UI cues and documentation completeness.

---

## Commit Strategy
1. Create a new branch `feature/phase-2-5-implementation`.
2. Commit each phase’s tasks in logical groups (backend, API, UI, docs) with descriptive messages (e.g., `feat: add hypothesis scoring & tests`).
3. Open a PR, run CI, address review feedback, then merge.

---

*Prepared by Antigravity – ready for you to review and start work.*

## Canonical close-out status · 19 November 2026

Use [LOGSYS_IMPLEMENTATION_PLAN.md](../LOGSYS_IMPLEMENTATION_PLAN.md) for the current Phase 0–1 completion checklist and canonical phase numbering, and [TESTING.md](../TESTING.md) section 5 for verification. The implementation and offline regressions are in place. Clean installation, Docker boot/restart, final live providers/extraction-v2, the restarted-backend browser pass and branch review/merge remain open. Experimental claim, policy and remediation scaffolds do not close those gates.
