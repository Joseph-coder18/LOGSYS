# Implementation Plan: Exact Calculated Confidence % for Answers & UI

## 1. Overview & Objectives
This plan outlines how to compute and display an **exact, evidence-grounded confidence percentage** (e.g. `95%`, `78%`, `35%`) across both:
1. **Incident Chat Replies** (`IncidentChat.jsx` / `ChatMessage.jsx`).
2. **Main Diagnosis / Root Cause Card** (`Investigation.jsx`).

The score will be strictly calculated from verified evidence (passed hypothesis checks, citation coverage, cross-modal support, and contradiction penalties)—not randomly generated or hallucinated by an LLM.

---

## 2. Mathematical Calculation Formulas

### A. For Incident Verification & Root Cause Diagnosis (`verification.py` / `VerifiedRCA`)
For a candidate root cause with $N$ required verification checks:
$$\text{Base Score} = \frac{\sum \text{check points}}{\text{Max possible points}}$$
Where:
- Check outcome `supports`: $+1.0$
- Check outcome `missing`: $0.0$
- Check outcome `contradicts`: $-1.0$

We add a cross-modal corroboration bonus ($+0.05$ if distinct modalities corroborate the finding) and clamp between $0.10$ and $0.98$ (never $100\%$ absolute certainty in distributed observability, preserving rigor):
- **Full Demo (`user-db` pool limit reduction)**: 4/4 checks pass + cross-modal telemetry $\rightarrow$ **$0.96$ ($96\%$)**
- **Partial / Missing Metrics**: 1/4 checks pass $\rightarrow$ **$0.25$ ($25\%$)**
- **Missing Logs / Changes / Topology**: $\rightarrow$ **$0.20$ – $0.30$ ($20\% - 30\%$)**
- **CPU hypothesis with contradiction**: $2/4$ pass minus contradiction penalty $\rightarrow$ **$0.25$ ($25\%$)**

### B. For Incident Chat Answers (`grounded_chat.py` / `GroundedReply`)
1. **Extractive / Peak Lookups (`answer_mode = "extractive"`)**:
   - The query router matched exact telemetry and verified the observation ID in the session store.
   - Confidence: **$0.95$ ($95\%$)** (e.g. exact connection limit $24$ or p99 latency $850\text{ ms}$).
2. **Model-Generated Answers (`answer_mode = "generated"`)**:
   - Ratio of claims that have valid, approved observation citations:
     $$\text{Confidence} = \left( 0.70 \times \frac{\text{Claims with citations}}{\text{Total claims}} \right) + \left( 0.25 \times \text{Diagnosis Confidence} \right)$$
   - Typically yields **$88\% - 94\%$** when all claims cite telemetry.
3. **Local Fallback / Insufficient Evidence (`answer_mode = "local"`)**:
   - If evidence is missing or query is ungrounded: **$0.30$ – $0.45$ ($30\% - 45\%$)**.

---

## 3. Code Changes Required

### Backend Changes

| File | Change |
|---|---|
| `backend/models.py` | Add `confidence_score: float = 0.0` to `RCAOutput` (which automatically equips `VerifiedRCA` and `GroundedReply`). Existing `confidence: str` ("high" / "medium" / "low") remains for backward compatibility. |
| `backend/services/verification.py` | Compute `confidence_score` in `_check_reduction`, `_check_cpu`, and `verify_incident` based on hypothesis checks and verification decision. |
| `backend/services/grounded_chat.py` | In `answer_incident`, compute `confidence_score` based on `mode` (extractive vs generated vs local fallback) and attach to `GroundedReply`. |
| `backend/sample_incident.py` | Add `confidence_score: 0.94` to sample incident payload so offline/demo preview renders the badge properly. |

### Frontend Changes

| File | Change |
|---|---|
| `frontend/src/components/Investigation.jsx` | On the main Diagnosis Card (around line 352), display a confidence badge next to the decision badge (e.g. `<Badge tone="positive">{Math.round(verifiedDiagnosis.confidence_score * 100)}% confidence</Badge>`). |
| `frontend/src/components/IncidentChat.jsx` | In the `Reply` component (around line 35), render a confidence badge alongside the `replyLabels` pill: e.g. `<Badge tone={confTone}>{Math.round(content.confidence_score * 100)}% confidence</Badge>`. |
| `frontend/src/components/ChatMessage.jsx` | If `confidence_score` is present, display `{Math.round(confidence_score * 100)}% confidence` instead of generic `{confCfg.label} confidence`. |

---

## 4. Verification & Testing Steps

1. **Unit Tests**:
   - Run existing suite (`pytest backend/tests/test_live_timeline.py backend/tests/test_hypothesis.py backend/tests/test_investigation_policy.py`).
   - Add assertion verifying `confidence_score` is populated, numeric, and in range $[0.0, 1.0]$.
2. **Evaluation Suite**:
   - Run `python backend/evaluate.py`. Verify all 10 retrieval cases and 5 incident variants output valid `confidence_score` metrics.
3. **UI Check**:
   - Verify that the chat replies show e.g. `[Source-backed reply] [95% confidence]` or `[Model reply] [92% confidence]`.
   - Verify that the main diagnosis card shows `[Supported by evidence · 96% confidence]` on complete runs and `[Insufficient evidence · 25% confidence]` on missing runs.

---

## 5. Execution Order
1. Update `backend/models.py`.
2. Update `backend/services/verification.py` and `backend/services/grounded_chat.py`.
3. Update `backend/sample_incident.py` and frontend sample data.
4. Update frontend components (`Investigation.jsx`, `IncidentChat.jsx`, `ChatMessage.jsx`).
5. Run tests (`pytest`) and `backend/evaluate.py` to ensure all tests pass.
