# Candidate preview and draft feedback refinement

User explicitly asked to continue the previous unfinished fixes. This refines the approved candidate work area, without changing approval or publication rules.

## Tasks

- [x] Diagnose exact counters and service readiness reasons from the specified draft, read-only. Distinguish ontology definitions, implementation operations, candidate facts, and text chunks.
- [x] Candidate preview becomes a dedicated review step: summary first, bounded/collapsed definition and fact details, visible back/confirm controls. Keep exact selected scope, outcomes and preview fingerprint validation.
- [x] Show immediate progress for preview, adding to draft, direct writes, and refresh. Prevent duplicate requests; uncertain writes keep original durable identity. Success uses a compact result card with next step and explicit unpublished knowledge state.
- [x] Draft controls default to a compact, readable summary and grouped definition changes; full internal identity/operations remain available as technical detail. Show server blocker messages and actionable next steps rather than a bare count. Show local review/publish loading without changing any gate.
- [x] Completed task cards show knowledge counts by entity/relation/attribute and status. Do not label document-wide counts as exact run counts when the API provides only current document assertions.
- [x] Add meaningful Node regressions first; run related contracts, then the Node suite and frontend static checks. Use real CSS/JS browser fixtures with 180+ operations and delayed requests at desktop/narrow widths. Inspect screenshots, bump changed resources, and verify the local server serves them. No live business approvals, publications, commits or unnecessary server restart.

Files owned by root: ontology-intake.js/css, ingest-results.js and corresponding CSS/tests, index.html and this plan. Panel subtask owns ontology-model-catalog.js, ontology-model-panel.js, ontology-model.js version caption, ontology-model.css and corresponding tests.

## Verified data and checks

- Read-only application-role audit of draft `3ef027d4-8f29-4f49-999b-886c4fdf4845`, revision 3: 60 definitions (34 entity types, 26 relation types, 0 attributes), 188 low-level operations, 118 unique pending candidate facts (73 entities, 45 relations, 0 attributes), from two source documents and two extraction chunks. Candidate facts are not paragraphs. No live audit, approval, validation, or publication was performed.
- The two publication conditions are `not_submitted` and `decision_pending`. Full content validation has not yet been evaluated; the UI preserves all service gates.
- Full Node service suite: 481 passed, 0 failed or skipped. Log: `.benchmarks/preview-feedback/node-tests-final.log`.
- Frontend static contract suite: 58 passed.
- Candidate preview browser fixtures: 1280/900/390 px passed summary, visible confirmation controls, bounded details, immediate progress, compact receipt, and one fixture submission per run. Real API writes: 0.
- Draft catalog/panel browser fixtures: 1280/900/390 px passed readable grouped definitions, bounded details, actionable workflow conditions, immediate validation/publication progress, and unchanged publication gating. Screenshots inspected. Real API writes: 0. One screenshot stability timeout did not reproduce; diagnostic samples confirmed stable bounds, active animation frames, and no running layout animations in all three widths. No product change was made for that transient tool failure.
- Local service `/` and all 10 changed versioned resources return 200 and match saved source bytes; cache version `preview-feedback-20261023-2`.
