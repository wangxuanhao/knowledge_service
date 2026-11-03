# Workflow UX Clarity Implementation Plan

> **For agentic workers:** Use bounded subagent implementation, followed by independent spec and quality review. Preserve the dirty current checkout, no commits or real business writes. Browser interaction was stopped by the tool; do not bypass it with another live-browser technology. Repository offline regression tests are the verification surface.

**Goal:** Make pending work and draft destinations obvious, and repair verified misleading controls across primary product workflows.

**Architecture:** Reuse existing OntologyModel/Intake owners and shared theme tokens; expose essential work above optional navigation. Keep all established API, approval, publication and inference defaults. Repair state and feedback at existing UI seams.

**Tech Stack:** Native JS/CSS, node:test, existing Python frontend/static and deterministic offline browser fixtures.

## Task 1: Visible modeling work and controls

Files: `web/ontology-model.js/.css`, `ontology-intake.js/.css`, `theme.css`, `index.html`; affected Node/static tests.

- [x] Add failing behavior/structure tests that candidates and associated draft work exist outside closed hierarchy details for unpublished/published/empty/loading states; reuse accurate status counts and existing owner actions.
- [x] Mount a permanent first-screen candidate/draft host above the catalogs; leave hierarchy details as hierarchy only. Preserve readonly, project and multi-draft rules and restore current selections.
- [x] Add failing tests and implement visible new/existing draft destination options; existing-only dropdown, labels and confirmation-time creation explanation. No create request from selecting/reading/previewing.
- [x] Strengthen shared native form affordance using current tokens and real CSS cascade; preserve multi-selects, checkbox/radio, dark project selector, disabled state and keyboard semantics. Bump only edited resource versions and update narrow contracts.
- [x] Run focused Node/static tests and inspect the diff.

## Task 2: Consistent state and local feedback

Files: `records-view.js`, `workbench.js`, `projects-view.js`, `projects.css`, `index.html`, related UI tests.

- [x] Reproduce unsupported all-kind value in the hidden compatibility selector; remove it so view and query cannot disagree, and verify the actual visible category tabs. Retain established single-kind views.
- [x] Reproduce late/stale snapshot results across A→B→A; clear on switch, guard reads and restore by captured project/context identity. Reuse existing restore behavior after explicit action, no automatic restoration.
- [x] Reproduce modal errors hidden behind overlay; add inline status/error and invalid-field focus for project create/settings; preserve entered fields on failed request.
- [x] Move the single generate input out of collapsed settings into visible answer-mode UI without changing false default or request construction. Give readonly index failures an actionable role-appropriate explanation; preserve admin entry.
- [x] Add meaningful behavioral regressions, update relevant cache versions, run focused Node and required static tests.

## Task 3: Truthful next actions

Files: `sources-view.js`, `task-review.js`, `ontology-model.js`, `ontology-model-panel.js`, related tests and asset versions.

- [x] Reproduce formal-review source runs incorrectly opening discovery intake. Classify using formal assertion count and exact current-run mode/readiness; never reinterpret historical run from a newer document. Formal action is explicitly project-wide; discovery action retains exact scope.
- [x] Add discovery next-action on task completion using durable result run/document metadata, with an explicit project-wide fallback if exact identity is absent. Test project switching and no implicit writes.
- [x] Add visible text explaining existing validation/submission/bulk approval behavior, without altering the workflow already approved in the 2026-10-26 design.
- [x] Preserve index status from publication receipt and show it separately with existing task navigation; unknown/not-requested is not reported as complete. Do not start jobs during navigation.
- [x] Run focused regressions, then full Node and related Python frontend static tests in one coordinated pytest session. Verify the minimal stale manual-token correction separately without unrelated database fixtures.

## Task 4: Audit and verification

- [x] Record evidence-backed product UX audit findings, actual repairs and the one deferred source-wide pending-status read model in `docs/research/2026-11-02-product-ux-audit.md`.
- [x] Use production assets with deterministic repository fixtures at 1440/900/390 to check always-visible pending work without opening hierarchy, new/existing destination cards, form affordance, inline errors and no overflow/hidden close buttons. Never control the blocked live user browser.
- [x] Independently review spec compliance then code quality; repair material issues and rerun affected checks.
- [x] Confirm all 16 changed cache versions and offline asset hashes; run `git diff --check`, preserve prior hunks and leave work uncommitted. No backend restart for frontend-only updates.
- [x] Runtime-only follow-up: after the user started Docker on 2026-11-03, confirmed PostgreSQL/Milvus/MinIO/etcd healthy and started the local application with the existing interpreter and normal configuration (PID 32460). Health returned 200; the served index and all 16 versioned asset hashes matched the workspace. Evidence: `.benchmarks/ux-clarity/static-delivery.json`. This confirms HTTP delivery, not a live-browser interaction walkthrough.

## Verification record — 2026-11-03

Full Node: 906 passed, zero failed/skipped. Combined related Python run: 74 passed and 2 failures, both caused by the manual validator's stale “逐条审核” token. Only that token was aligned to the existing “按定义审核” UI/manual; all 12 documentation tests then passed with `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python -m pytest -q --noconftest tests/service/test_docs_contract.py`. The six new offline UI cases and all frontend static contracts passed in the combined run. The standard docs-only pytest rerun was interrupted after a prolonged wait without results; the replacement pure-static run does not use unrelated database fixtures.

Reviewed latest 1440 desktop pending-work, 390 draft-choice, QA mode and inline-error images. The three implementation areas received independent spec/quality reviews; final Task 3 review approved. Offline asset/preservation checks and manual validator passed. Runtime HTTP delivery was subsequently verified after Docker became available. The live-browser walkthrough restriction remains recorded in the audit.

## Follow-up bug fix — same-draft dependency reuse, 2026-11-03

- [x] Reproduce the user's real 54-candidate selection with a read-only application-role connection: 23 required entities were already among the 100 candidates attached to the original draft.
- [x] Add failing frontend regressions; reuse only same-draft, same-source/version entity dependencies, retain recorded explicit mappings and invalidate stale context. Show selected/reused/total counts without confirming automatically.
- [x] Add failing API/store regressions for all-formal append. Allow explicit append to a real existing draft; retain the new-definition requirement for new drafts and require immutable intent plus the matching draft-side intake reference in storage.
- [x] Verify 913 frontend tests, 201 intake/store tests, the earlier 105 related frontend/static/offline checks and independent source review. The production controller now yields 54 selected + 23 reused = 77 reviewed, with zero conflicts/dependencies; real draft revision and counts remain unchanged.
- [x] Verify no active service tasks; restart only the local process owned by this chat (32460 → 42144), retain its interpreter/cwd/normal configuration, and confirm health 200 plus all 16 served assets. Cache version: `draft-dependency-20261103-2`. Preserve prior dirty changes and leave uncommitted.
