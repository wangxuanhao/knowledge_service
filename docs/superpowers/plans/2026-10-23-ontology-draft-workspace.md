# Ontology Draft Workspace Implementation Plan

> Execute in this already-authorized shared checkout using subagent-driven-development with disjoint file ownership. Preserve existing uncommitted work. Do not commit, create a worktree, restart the backend, or perform live business writes for this frontend change.

**Goal:** Implement the approved right-hand draft workspace and two-way immutable version/draft lineage.

**Architecture:** Keep the model as the only editing-state owner. Reuse existing governance controls inside a dedicated workspace, and read archived drafts through an independent presentation. Existing GET endpoints and durable publication links are sufficient.

**Tech Stack:** Native JavaScript/CSS, existing Node test contracts, Python Playwright with system Edge. Graph discovery was attempted; its function locations are stale, so known-file source is the implementation authority.

## Tasks

- [x] Plan/spec review for scope, module contracts, read-only isolation, and behavior preservation.
- [x] Add a lazy read-only `ontology-draft-history.js` and behavioral Node tests. Own only this new module and its test. Follow the interface in the spec; no live API mutations or edits to current model state. Guard project changes, superseded reads, closed targets, and failures.
- [x] Extend version details in `ontology-model-panel.js` with an exact source-draft link, missing-history state, and `openHistory(ontologyId)` selection. Add the explicit action-placement hook to `put` when replacing `actionsHost`. Own this file and a dedicated new test; preserve all governance logic.
- [x] Root: add `ontology-model-workspace.js/.css`, move draft hosts and existing action host into its shell, add toolbar/controller integration, make right-side modes mutually exclusive, and preserve focus/state across close and expand. Add meaningful layout/state regressions first.
- [x] Root: compact term cards to the approved name/description/parent/count/view-detail hierarchy; put create/edit actions in definition details. Keep searching and selection behavior.
- [x] Root: register new scripts before model initialization and bump every changed resource. Synchronize pinned test versions and changed presentation contracts.
- [x] Review implementation against the spec, then review correctness/maintainability. Address substantive findings before final checks.
- [x] Run relevant Node contracts, full Node suite, frontend static contracts, and offline production-browser fixtures. Inspect screenshots at 1440/900/390 px; verify visible fixed controls, no overflow, background selection/draft preservation, and no live writes.
- [x] Verify localhost:8100 serves current versioned resource bytes; final diff check and record validation evidence.

## Ownership

- Root: `ontology-model.js`, `ontology-model-workspace.js/.css`, `ontology-model-catalog.js/.css` if necessary, `index.html`, root integration tests, browser fixtures, docs.
- History implementer: `ontology-draft-history.js`, `ontology-draft-history.test.cjs`.
- Panel implementer: `ontology-model-panel.js`, `ontology-version-lineage.test.cjs`.
- Reviewer: read-only plan/spec review and later independent implementation review; no file changes.

## Validation evidence — 2026-10-23

- `node --test tests/service/*.test.cjs`: 515 passed, zero failed or skipped.
- `D:/anaconda/envs/llm_model/python.exe -m pytest -q tests/service/test_frontend_static_contract.py`: 58 passed.
- `.benchmarks/ontology-workspace-browser-check.py`: production HTML/CSS and model/catalog/panel/history/workspace modules passed at 1440, 900 and 390 px with 60 definitions, 188 operations and 118 linked candidate facts. Screenshots inspected for default cards, changes, archive and delayed validation. The operation row stays fixed and full width; warnings remain in the scrolling section. Mocked APIs performed zero live business writes.
- Regression red/green: moving the actual `.om-ab-ops` preserves the original note/actions and leaves warning acknowledgement in place; subsequent synchronization cannot move the remaining warning wrapper.
- Visual regression red/green: the relocated busy button retains its spinner pseudo-element; the browser assertion failed before adding the footer selector and passed afterward at all three widths, including reduced-motion rendering.
- `.benchmarks/verify-ontology-workspace-assets.py`: localhost index and all six changed versioned assets return HTTP 200, carry `draft-workspace-20261023-2`, and match saved bytes.
- `git diff --check`: passed; Git reports only existing LF/CRLF normalization notices.
- Spec review: approved after correcting intake-receipt identity, frozen source versions and working-draft summaries during historical preview. Final quality review approved the operation-row layout fix with no blocking findings.
