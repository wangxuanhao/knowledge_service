# Definition Review Workspace Implementation Plan

The user approved implementing the previously presented bulk-review and definition-grouped workflow. Execute in this shared checkout on its feature branch; preserve the local service and business draft. Do not approve or publish real business data. Use the existing server authorization, revision, baseline and validation-fingerprint guards.

**Goal:** Resume bulk approval of submitted drafts, explain review counts, and review a whole business definition without clicking each implementation operation.

**Architecture:** Keep OntologyModel as the only active-draft owner. Load review eligibility separately from publication readiness. Extract the manual dialog into a bounded review workspace with a small grouping/controller seam. Add a server-owned definition decision command so one explicit group decision is atomic; ordinary caller-supplied decision batches retain their 100-item and risk restrictions.

**Stack:** Native JS/CSS, FastAPI, PostgreSQL, Node contracts and offline Edge/Playwright fixtures.

## Approved interaction

- Submitted drafts expose a visible bulk action with the server-reported eligible operation count. Resuming this action does not resubmit the draft or discard existing decisions.
- Opening review is read-only. The default list contains definitions with undecided operations; each definition groups exactly its current target IRI, with readable names, kind, changes and risk reasons. Already-decided rows remain inspectable through a filter.
- A global bulk action approves only the existing server-selected auto_approvable set. Blocked items, high-risk items and warnings never silently enter that set.
- A definition decision explicitly approves or rejects all its still-undecided operations in one transaction. Required reasons and individual warning acknowledgements remain visible. Mixed blocked groups cannot be approved. The same reason is attached to each decision in that definition.
- The active definition shows readable before/after fields, evidence when available, and collapsed technical identity/operations. Fixed controls provide approve/reject and approve/reject then next, plus explicit previous/next navigation.
- Network failures leave the result uncertain and require a read-only reload before another decision. Project, draft, revision, history and closed-dialog changes invalidate in-flight responses. Never overwrite another project's active draft.

## Tasks

- [x] Reproduce the review/publication-loading coupling and submitted-draft resume behavior in Node tests. Add tests for delayed/stale gate responses and relocated action controls.
- [x] Add service/API regressions for exact definition membership, reasons/warnings, stale revisions/fingerprints, blocked groups, idempotent re-opening, complete rollback, and unchanged ordinary 100-item/risk caps.
- [x] Implement the definition decision route. It accepts target IRI, action, reason and existing optimistic guards; service selects the pending operations. A private trusted-definition channel bypasses only the unattended-batch restriction, retaining all per-operation approval and transaction checks.
- [x] Add a review workspace grouping/controller module with behavior tests: 60 definitions/188 operations, exact group boundaries, bulk eligibility, progress, filtering, next-item behavior, rejected/unknown requests and cross-context races.
- [x] Build the responsive review dialog using existing studio tokens, bounded list/details and fixed actions. Add accessible keyboard navigation without accepting plain Enter from an editing field. Wire the panel to it and make action captions and pending hints explicit.
- [x] Decouple stage/review-plan reads from publication readiness and expose retryable read failures. Resume submitted approval without validate/submit repetition. Maintain historical read-only behavior and preserve active editor values.
- [x] Bump all changed resource versions and synchronize pinned contracts. Run related tests, then all Node contracts and the necessary Python API/static suites in a single pytest session.
- [x] Inspect production-CSS/JS browser fixtures at 1440/900/390 px with delayed reads/writes and mocked APIs; perform zero live business approvals/publications. Independently review, fix substantive findings and run final diff checks. If backend reload is needed, audit all durable work first and preserve interpreter/cwd/environment.

## Read-only observations

The user's current draft has 188 pending operations, all server-classified as auto_approvable, no required per-item reasons, no validation warnings and zero saved decisions. Review stage is allowed. The current UI couples review gate loading to publication checks and exposes only operation-by-operation manual controls; these are the boundaries to reproduce and improve. The draft must remain unchanged during implementation and verification.

## Verification completed

- 539 Node contract tests passed, including submitted bulk resume, delayed gates, exact grouping, warning/reason/blocker guards, stale responses, terminal recovery and uncertain bulk no-op protection.
- The scoped Python/API/static/browser run passed 204 tests and exposed one existing outbox-consumer race in an intake transaction test. That test now pauses only its background consumer, retaining its complete facts/runs/outbox equality assertions. The subsequent 62-test run passed, including the corrected case, all frontend static checks and all three browser sizes.
- A separate 21-test backend run passed the 16 new definition decision regressions and five existing strict/assisted batch cases, including 100/101/188 operations.
- Production JS/CSS browser checks passed at 1440, 900 and 390 pixels. All writes used offline APIs; no real approval or publication was performed.
- Independent specification and quality reviews approved integration after fixing remote-revision and terminal-recovery issues.
- The local service was reloaded only after a full persisted-work idle audit, preserving its interpreter, command, environment and working directory. Health, OpenAPI endpoint and served resource hashes verified. The business draft remains pending at revision 5 with 188 operations and zero decisions; all 188 remain server-eligible for bulk approval.
- Repeated read-only business draft snapshots have identical ontology/operation content, confirming decision-response snapshot checks are compatible with the current API.
