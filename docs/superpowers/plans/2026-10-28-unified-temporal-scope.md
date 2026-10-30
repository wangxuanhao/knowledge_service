# Unified Temporal Scope Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [x]`) syntax for tracking.

**Goal:** 图谱、检索、问答共用可解释的业务时间与系统时间筛选，问答请求和历史保留同一份提交范围。

**Architecture:** 复用既有 `valid_at / known_at / include_unknown` API。时间表单逻辑独立为小模块，共享筛选负责调用，问答移除重复日期入口并冻结单次请求范围。必要时修正问答未知时间参考补读的旁路。

**Tech Stack:** Native JavaScript/CSS, FastAPI/Python 3.12, PostgreSQL, node:test, pytest, Playwright。

**Spec:** `docs/superpowers/specs/2026-10-28-unified-temporal-scope-design.md`

---

### Task 1: 共享时间筛选与问答集成（一个连续实现任务）

**Files:**
- Create: `knowledge_service/web/temporal-scope.js`
- Modify: `knowledge_service/web/index.html`, `workspace-context-ui.js`, `workbench.js`, `workspace.js`, `evidence-inspector.js`
- Optional focused styles: `knowledge_service/web/workspace-context.css`
- Create: `tests/service/temporal-scope.test.cjs`
- Modify existing Node contracts only where resource versions or scope semantics change; preserve unrelated changes.

- [x] Write and run failing behavioral tests for all/current/at selection, missing/invalid date preserving previous applied scope, UTC conversion, include_unknown on/off, restore/copy/reset and combined scope preservation.
- [x] Write regressions that mutate UI/scope while `ensureActive` or history save is pending and prove transmitted body, saved user scope and retry remain identical to the captured submission. Verify loaded old turns keep their original time and unknown flag.
- [x] Cover explicit `known_at` with semantic/hybrid rejection and unchanged applied scope, plus successful keyword retrieval using both independent time axes.
- [x] Implement pure helpers in `temporal-scope.js`, returning only supported API fields. Current mode resolves once at apply. Current mode must validate the supplied clock rather than persist a literal `now`.
- [x] Add the three modes, conditional date input and unknown checkbox to the existing shared filter; maintain apply/cancel semantics. Include a visible explanation of the two time axes and frozen current time.
- [x] Remove `qa-as-of`; `submit` captures one scope and uses the same values for body, user history and submitted-scope note. Keep retries bound to that request. Preserve request serial/project checks.
- [x] Rename the graph timeline to system revision time. Use common temporal labels in inspector and history notes; show only source metadata that actually exists. No dates are written by a renderer.
- [x] Bump all changed resource versions in `index.html`, update exact-version contracts and load helpers before consumers. Run focused Node tests, then the full `node --test tests/service/*.test.cjs` once.

### Task 2: 问答参考补读与服务验证

**Files:**
- Inspect: `knowledge_service/services/answer_validity.py`, `answers.py`, `qa_policy.py`, `repository/core.py`
- Add regression tests to the existing relevant answer-validity test file or a focused `tests/service/test_temporal_scope_filters.py`.

- [x] Verify unknown exclusion through current keyword/semantic/hybrid request scopes and QA direct/context/reference candidates. Use real classification seams and bounded fixtures.
- [x] If unknown reference fallback bypasses explicit strict time selection, first write a failing regression, then filter only that bypass at qualification/classification. Leave reference lookup capable of finding labeled expired/future material when requested; do not promote it to eligible evidence.
- [x] Run one isolated pytest session using `D:/anaconda/envs/llm_model/python.exe -m pytest -q` on relevant unrestricted scope, answer validity, source scope and temporal provenance tests. No .env import and no simultaneous pytest sessions.

### Task 3: 浏览器、审查与交付

**Files:**
- Add or extend a focused browser test under `tests/service/` using the repository's existing browser fixture.
- Update the comparison note `docs/research/2026-10-28-semantica-temporal-reference.md` with implemented scope and validation evidence.

- [x] In a real browser, apply current/date/all filters on search and QA, inspect outgoing JSON, verify missing-date handling, unknown control, page restore and absence of duplicate QA override. Use a fixed clock and fixture API; no real LLM needed.
- [x] Have a reviewer assess spec compliance, then code quality; fix actionable findings and rerun impacted checks.
- [x] Verify changed source syntax, local links, actual test outputs and this task's diff. Report passing counts and material limitations; do not claim production end-to-end LLM validation.
- [x] Preserve unrelated working changes. No commit/push/deployment required by this plan.

Validation: Python 134 passed; related Node checks passed; full Node snapshot 813/815 with two separate graph failures recorded in docs/research/2026-10-28-unified-temporal-scope-verification.md. Spec and quality reviews approved.
