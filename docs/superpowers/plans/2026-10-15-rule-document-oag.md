# Rule document OAG implementation plan

> **For agentic workers:** REQUIRED: Use subagent-driven-development. Preserve existing staged changes; never stage or commit other tasks' files. Run only one pytest process at a time.

**Goal:** Enhance existing multi-platform rule document QA with temporal evidence eligibility, historical references, qualified unknown-date answers, consistent provenance and usable UI; document actual behavior.

**Architecture:** Keep current search and applicability semantics. Add an internal authority reference-read path that bypasses only business-validity filtering while retaining knowledge time, project, ontology and applicability checks. Both QA transports use common context/evidence classification. Expired/future references never enter the generation prompt. Unknown dates support qualified document explanations, not assertions of current validity. No external database integration, mandatory extraction or generic query engine.

**Tech Stack:** Python/FastAPI/PostgreSQL; vanilla JS/CSS; pytest and node:test; existing HTML manual generator.

## Task 1 — backend evidence lifecycle and QA

Files: services/answers.py, focused new services/answer_validity.py (or equivalent), services/service.py (minimal seam), repository/core.py (bounded optional internal query mode), models.py; provenance/answer_evidence only as necessary. Tests: new tests/service/test_rule_document_oag.py plus existing applicability/provenance/QA tests.

- [x] Write failing tests for explicit effective, expired boundary, future, publication fallback and unknown dates; freeze one request as_of and known_at.
- [x] Add independent bounded reference retrieval retaining filters/ontology/applicability/known_at. Default /search unchanged. Question fields: include_references (bool, default true), reference_k (1..20, default 5).
- [x] Context exposes as_of, known_at, historical_references, future_references, uncertain_references, coverage_warnings; each reference has citation R/F/U, validity_status, validity_basis, answer_eligible=false, usage and exclusion_reasons plus original metadata and source versions. evidence contains confirmed-effective E only; unknown-date qualified items use U with usage=qualified_quote and answer_eligible=false. Extend frozen provenance to U or map private frozen references to public U explicitly; never label unknown as E just for compatibility. Use deterministic marked U excerpts if semantic qualification cannot be reliably enforced.
- [x] Preserve source assertions, classify source eligibility per occurrence, test effective entity with expired source and mixed effective/expired assertions, and fail closed on missing historical provenance instead of guessing record source_id. Exclude conflict sources from generation. No arbitrary metadata.status business revocation mapping: absent a pre-existing authoritative business status contract, expose source/date conflict and date-based expiry, document manual governance for revocation rather than claiming automatic status interpretation.
- [x] Exclude expired/future references from all LLM inputs. Add explicit qualified-quote framing and missing-platform/context limitations. Missing attributes/bare chunks remain usable.
- [x] JSON/SSE share eligibility and frozen run contracts; fail/cancel states and citation audit cannot mark invalid-reference answers successful.
- [x] Add directed, bounded path consistency only on the actual qualified evidence edge set; no broad graph read on native retrieval. Absence of paths must not block text QA.
- [x] Run targeted pytest serially, then relevant regression suite; record evidence and limitations.

## Task 2 — frontend temporal references and comparison context

Files: web/workbench.js, workbench.css (or focused companion module), web/index.html. Tests: new node:test behavior fixture plus affected existing static/browser contracts.

- [x] Write failing UI tests for independent current/expired/future/unknown sections, escaped content, source viewing, final done replacing streamed draft.
- [x] Consume agreed backend fields from evidence event; retain across chat history. Display dates, validity and exclusions clearly; unknown-date material marked effective status pending; reference citations R/F/U not counted as current answer evidence.
- [x] Expose reference visibility and a QA-only historical as_of control (the existing global scope has known_at but no valid_at input), without overriding search default scope. Show missing platform/coverage warnings. Reuse frozen source reader/provenance drawer.
- [x] Bump every changed asset version and synchronize version-sensitive contracts.
- [x] Run relevant Node tests and JS syntax checks. Do not run pytest concurrently with backend worker.

## Task 3 — README and operation manual

Files: README.md, docs/2026-10-01-本体工作台操作手册.md, docs/assets/manual-v110/manual-template.html, generated docs/知识图谱操作手册.html; scripts/build_knowledge_service_manual.py only if required by generator.

- [x] Describe multi-platform document QA, attributes optional, source tracing, effective/expired/future/unknown semantics and historical valid_at vs known_at.
- [x] Document actual API parameters and UI actions after code delivery; avoid claiming unsupported exhaustive comparisons or automatic compliance decisions.
- [x] Rebuild self-contained manual with existing interpreter; verify embedded images and section anchors remain intact; preserve unrelated staged manual changes.

## Task 4 — integration review and verification

- [x] Review backend and UI against spec, then quality review with independent agent; resolve material findings.
- [x] Run only one pytest session; backend-specific integration suite plus entire Node suite where feasible. No real-provider model claims without a live evaluation.
- [x] Verify documentation references, generated manual determinism, compile/syntax and git diff --check. Report what was changed, test outcomes and material limits.

## Verified delivery

- Backend combined regression: 212 passed, 1 xfailed (existing expected failure), 222.62s.
- After final visibility/group filtering changes: complete OAG + native grouping + applicability selection, 52 passed, 78.94s.
- After final asset version update: docs + frontend static contracts, 69 passed, 24.49s. These selections overlap; do not sum counts as unique tests.
- Entire Node suite: 116 passed, 0 failures. Changed JS syntax and nine changed backend modules compile successfully.
- Independent backend and UI reviews: no remaining material findings after exact source occurrence, governance, visibility and draft corrections.
- Edge isolated HTTP fixture: desktop/mobile layout, inert malicious excerpt, validated final replaces draft, R1 frozen provenance and doc-v2 source action. It does not claim live LLM accuracy.
- Manual regenerated deterministically, 33 original + 2 fixture embedded figures preserved, README local links valid.
- Local service restarted using original interpreter on 127.0.0.1:8100; health 200/ok and OpenAPI includes new QA fields.
- No business migration introduced. No Git staging/commit; other tasks' staged changes retained. No real-provider model accuracy evaluation performed.

Compatibility detail: unknown dates remain U and use deterministic qualified excerpts. E alone enters the model prompt; R/F are separate references. Independent reference retrieval reads at most 501 authority candidates before remaining filters and explicitly warns that matching history can be omitted. No complete-rule-count or exhaustive history claim.
