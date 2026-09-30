# Discovery workbench completion

> Continue the approved Scheme C plan, main agent only as requested. Use test-driven development and verification-before-completion; merge locally to master after verification.

**Goal:** Close the four verified workbench gaps without changing ontology governance policy, existing data, or SWRL scope.

**Architecture:** Keep discovery runs immutable on the server. Derive UI actions from result kind AND lifecycle status. Fetch the selected draft before showing its editor. Reuse the existing assertion resolver and pinned historical-source viewer for conflict evidence; never infer current-document evidence from an opaque candidate ID.

**Tech stack:** Existing JavaScript workbench, pytest/Playwright with system Edge; backend regression under conda llm_model.

## Steps

- [x] Add failing browser regressions in tests/service/test_ontology_workbench_ui.py for terminal/stale next-round actions, cached-draft replacement, finalize refresh/outcomes, raw API run responses, creation 409 guidance, and conflict evidence success/failure/unresolvable cases.
- [x] Fix knowledge_service/web/ontology-workbench.js: lifecycle labels/actions, draft cache replacement, result-kind preservation, refreshed counters, candidate outcome reasons, evidence inspector reuse, stale recovery.
- [x] Run the entire workbench browser suite with the existing Playwright-enabled .venv and real Edge; run conda run -n llm_model --no-capture-output python -m pytest -q -ra. Check JS syntax, asset version, and diff whitespace.
- [x] Self-review changes against the original plan and the verified bugs; record exact evidence, including skipped tests and mock/real boundaries.
- [x] Commit scoped changes, merge current master into the feature branch if needed, verify the integration, then merge the verified feature into master. Preserve unrelated SQLite sidecar changes; do not push or deploy.

## Acceptance

Published/closed/stale runs permit reanalysis; active runs retain their existing continuation. Newly created drafts show their own content even after a prior draft was open. Finalization shows server outcomes and updated lifecycle counts, without losing its mapping-only identity. Conflict evidence opens the fixed assertion/document version or clearly explains missing history. Tests cover successive rounds, not only an initially empty page.

## Two-round verification finding

The real service regression exposed a second-round backend defect: cumulative snapshots contain candidates already materialized in earlier rounds. Mapping-only finalization rejected those as stale even when the source fingerprint matched; a new-type draft attempted to insert their formal records again and failed revision checking.

Keep cumulative evidence and endpoints, but freeze the already-materialized candidate IDs as immutable run context. Do not put mutable processing state in the source fingerprint: replay after successful finalization must return the same run. Bump the generator contract to distinguish new runs from legacy snapshots without this context. At finalization/publication, reuse their actual persisted records for validation and endpoint resolution, write only new records, and retain old record versions. Reject newly concurrent materialization or missing previously frozen materialization. This is a bounded continuation of the approved multi-round workflow, not a data migration or ontology-policy change.

- [x] Verify both second-round paths: mapping-only and newly introduced class with reviewed publication. Assert old record version IDs are unchanged, formal counts and latest-run summaries are correct, and no extra no-op ontology is created.

## Verification before integration

- Browser red phase: 12 failures for the original four gaps; 3 failures for terminal-run replay. Green phase: 44 workbench tests passed in real Edge with mocked HTTP boundaries.
- Backend red phase: second-round mapping finalization failed with stale_source; second-round draft publication failed with record revision conflict. Both paths now pass using real Semantica induction, temporary SQLite, real service/API/governance code, and deterministic extraction results (no live LLM request).
- Backend focused suites: 273 passed under conda llm_model (Semantica 0.7.0); additional persisted-context endpoint-remapping test passed.
- Extra guards: invalid materialized-context IDs and unrelated record-ID collisions failed before the guard fixes. Repeated generation after finalization preserves the run ID.
- Self-review preserved OWL/SHACL validation, atomic publication, immutable run snapshots, and expected-revision writes. No user database, historical artifact, or master-side document was rewritten. SWRL remains out of scope.
- JavaScript syntax and git diff whitespace checks passed. Integrated full-suite results are recorded after merging master into this branch.

## Integrated result

- Integrated code commit: `c7441e1` (includes master's two newer cluster-design documents without conflicts).
- `conda run -n llm_model --no-capture-output python -m pytest -q -ra --tb=short`: **1212 passed, 40 skipped, 1 warning** in 177.85 seconds. Skips are Playwright-unavailable suites in conda plus a Windows symlink capability test; the warning is Starlette's existing AnyIO deprecation.
- Relevant workbench browser suite ran separately using the Playwright-enabled .venv and system Edge: **44 passed**. Conflict/status/outcome rendering was also visually inspected with the project selector in the production sidebar position.
- Local master fast-forwarded from `893cf02` to `c7441e1`; application and test files match the tested integration tree. The pre-existing deleted SQLite `-shm`/`-wal` sidecars remain unstaged and unchanged by this work. No push, service restart, production-data rewrite, or live-LLM acceptance run was performed.
