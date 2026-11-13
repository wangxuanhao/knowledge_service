# Entity identity and attribute workflow implementation plan

> **For agentic workers:** Use subagent-driven-development for isolated tasks and two-stage review. Use the current checkout on a feature branch; preserve earlier mindmap work and user data files. Only one pytest session may run at a time.

**Goal:** Unify stable entities across document dates without losing source-qualified time, and make typed/text attributes readable, reviewable and searchable without drawing attribute nodes or lines.

**Architecture:** Keep canonical entity IDs and the formal writer transaction. Add one source-context and identity policy module shared by resolving, merging and scoped reads. Attribute facts remain independently versioned, supported and scoped; graph and search consume bounded authority reads rather than joining all attributes into entity text.

**Tech Stack:** Python 3.12/FastAPI/PostgreSQL; existing Semantica adapter; Milvus projection/outbox; native JavaScript/CSS and existing ECharts/WebGL graph.

## Approved behavior

Specification: `docs/superpowers/specs/2026-11-13-entity-identity-attributes-design.md`. User confirmed clarity, stability, clean pages and bounded performance on 2026-11-13. Graph attributes are hover summaries and sidebar groups only: no property nodes, lines or overlay. Existing production data will not be bulk merged or reindexed automatically.

## Task 1 — Identity policy and correlated source support

Files: new domain/service identity and source-context modules; `services/reconciliation.py`, `entity_resolution.py`, `governance.py`, `formal_writes.py`; `repository/scoped_reads.py`, formal executor and one numbered SQL migration as required. Tests: entity resolution, reconciliation, merge, temporal/scoped read fixtures.

- [x] Write failing cases for reference entities with disjoint/overlapping document intervals, unchanged source payloads, scoped event identities, ambiguous exact names, and correlated time/platform qualification.
- [x] Run only these cases; establish the expected failure before implementation.
- [x] Add explicit published class policy (`reference`/`scoped`), retaining legacy conservative defaults. Reference identity uses stable type plus explicit identity scope/key, not source validity/applicability. Resolve only a unique exact candidate; strong-key conflict is never overridden by similarity.
- [x] Maintain bounded, indexed source-context support from assertions and decision events. A source context must satisfy all scope predicates together; manual identity declarations are also supported. Known-at, withdrawal and source-version rules must match the authoritative event history.
- [x] Implement mandatory numbered migration and version checks plus idempotent event-aware support backfill, count reconciliation and missing/invalid-support diagnostics, including interrupted-backfill retry. Documentation alone does not satisfy this step.
- [x] Wire resolving and merge compatibility to the same contract, under the formal-write dependency lock. Do not merely delete date equality checks. Preserve relationship/attribute periods and support mappings; rebuild endpoint eligibility from qualified identity support.
- [x] Verify tests and report integration hooks for source-context-aware vector ranking before enabling cross-time reference writes.
- [x] Hard-gate every cross-time write (automatic resolution and confirmed identity merge) until source-qualified entity AND relation projections from Tasks 4–5 are fully built, audited, caught up and active. Test old indexes, interrupted/incomplete builds and rollback; each must leave cross-time writes blocked with an actionable reason.

## Task 2 — Existing-data reconciliation and conflict transactions

Files: `services/governance.py`, formal planner/executor; existing governance routes and web controls. Tests: merge preview and reversal, typed reviews, concurrency CAS.

- [x] Test deterministic paginated duplicate grouping, stale preview rejection, batch group independence, and source-preserving reversal.
- [x] Add bounded duplicate scan and group preview using existing governance entrance, per-group fingerprints and explicit confirmation. Do not scan/merge the corpus on page load.
- [x] Introduce conflict-set quarantine for identity merges: accepted support becomes contradicting, original facts/periods remain in history, current ordinary retrieval excludes disputed values; QA reads scoped unresolved-conflict markers independently.
- [x] Only the specialized confirmed identity merge can acknowledge enumerated SHACL deficits caused by quarantine; unrelated validation remains strict. Keep existing manual winner-based behavior compatible where explicitly selected.
- [x] Validate batch retry, source withdrawal and historical decisions.
- [x] Implement the server-side conflict-set decision endpoint: validate exact member/decision versions, explicitly accept/reject or split approved validity ranges, revalidate, publish formal fact revisions/outbox updates and close the set atomically. Test successful resolution, stale decisions, replays, source withdrawal and rollback.

## Task 3 — Attribute extraction and authority detail reads

Files: `services/attribute_extraction.py`, `integrations/semantica_adapter.py`, ontology summary, explorer and new bounded detail/source reader; thin API routes. Root owns any `models.py` / `service.py` integration unless explicitly transferred.

- [x] Test declared descriptive string properties, complete multiline values, typed booleans/numbers, unsupported object values, evidence checks and explicit budget diagnostics.
- [x] Keep extract_attributes default false and documents-mode disablement. Use published property IRI/domain/range/description to guide extraction; allow declared paragraph fields without blanket responsibility-text rejection. Preserve conservative handling for undeclared facts.
- [x] Expose bounded property groups/values and per-value frozen source evidence, with proper current/history scope. Keep source document counts distinct from assertion counts and escape all content in UI.
- [x] Avoid attribute/evidence N+1 queries and all-corpus list_assertions in selected-entity paths; verify query and response budgets.

## Task 4 — Attribute retrieval projection and ranking

Files: `domain/search_projection.py`, filter catalog, projection input/receipt/sync/runtime and native Milvus adapter; `services/retrieval.py`, `scoped_ranking.py`; root integrates public Search/QA models, service defaults and response grouping.

- [x] Add tests proving attribute value-only tokens are indexed, including a long paragraph tail; candidate/withdrawn/out-of-scope values never rank as accepted facts.
- [x] Add attribute channel to build, catchup, audit, readiness, quotas and response paths. Search text derives from owner name/property identity/value, not display text alone. Preserve datatype and authority fact/version IDs.
- [x] Index deterministic bounded long-text windows and correlated source support contexts; vector IDs include context/window identity. Group by canonical fact before top-k, with honest bounded-refill completeness.
- [x] Increment projection capability/version, refuse to claim attribute completeness on an old index, support safe explicit rebuild and old-index rollback. Never silently query current vectors for historical semantic requests.
- [x] Test update/tombstone/relabel/merge dependency invalidation and receipts across rebuild/catchup.

## Task 5 — Search/QA and scope integration

Files: `models.py`, `services/service.py`, answers and retrieval context, public API routes and search UI. Root-owned integration.

- [x] Add k_attributes and allowed attribute content channel with bounded defaults, keeping existing callers valid.
- [x] Return owner, property and matched value/window; group duplicate windows and budgets by fact, preserve supported value and source versions.
- [x] Extend historical keyword, per-platform retrieval, ontology expansion and answer evidence to attribute facts. Include unresolved conflict warnings without treating candidates as true facts.
- [x] Ensure source-qualified entity vector ranking is ready before cross-time identity feature activation.

## Task 6 — Graph/sidebar/review user flow

Files: new shared attribute panel JS/CSS; `web/evidence-inspector.js`, graph presentation/workspace/full graph hooks, mindmap click bridge, record editor/create, task review and entity governance; update only exact changed index asset versions and matching contracts.

- [x] Write behavioral Node tests for groups, multiple values, escaping, long text, paginated sources, query-generation invalidation, errors/retry and unified conflict action.
- [x] Reuse a single attribute reader/view in full graph, local graph, mindmap and retrieval detail. Hover has small scoped summaries; click opens complete grouped facts in a stable sidebar. No new graph nodes/edges.
- [x] Implement source expansion and paragraph reading, multiline editors, extraction toggle help and stale/read-only protections. Review action preselects the correct conflict in the shared knowledge-review flow.
- [x] Add existing duplicate scan/preview to the existing entity governance section. Users select explicit groups, inspect impacts, confirm and receive per-group results; no manual ID entry required.
- [x] Real browser checks at desktop/phone sizes: stable graph positions, no overflow, no property lines, keyboard/touch operation, source/full-text access, empty/loading/failure/retry states.

## Task 7 — Review, performance and delivery

- [x] Spec compliance review, then independent correctness/quality review; address findings before completion.
- [x] Run isolated Python integration/regression tests serially; run full Node contracts separately, relevant real browser interaction suites and syntax/whitespace checks.
- [x] Measure selected entity property response sizes/query counts and duplicate-scan/index batch budgets with many facts. Report actual evidence; do not promise zero errors or inferred scale.
- [x] Write migration/backfill/index-rebuild/activation/rollback instructions and source support diagnostics. Do not run changes against business data without explicit data-operation authorization.
- [x] Update this plan with commands, results and remaining limits. Leave implementation reviewable on the feature branch; do not include unrelated user files.

## Test commands

Use `D:/anaconda/envs/llm_model/python.exe -m pytest -q <explicit test paths>`; never source .env, create .venv, or run concurrent pytest sessions. Node: `node --test tests/service/*.test.cjs`. Browser: bundled Playwright via NODE_PATH with system Edge; no application node_modules. Artifacts/logs: ignored `.benchmarks/`.


## Final execution evidence — 2026-11-13

- Implementation and independent specification/quality reviews completed. Confirmed findings were reproduced and corrected, including exact latest source decisions, correlated QA contexts, type lineage across moves/undo, reject-all required-property validation, first-draft retry, exact IRI annotation removal, target-specific projection versions, and bounded native schema upgrade/audit.
- Final serial PostgreSQL acceptance: **214 passed, 1 existing xfailed** in `.benchmarks/entity-attributes-final-acceptance.log`; governance/API/storage: **153 passed** in `.benchmarks/entity-attributes-final-governance.log`. The retained strict xfail is `test_same_answer_captures_consistent_frozen_ingest_state`: pre-existing READ COMMITTED ingest-run capture behavior, not newly introduced. No new unexplained test failures remain.
- Earlier broad dedicated regression covered 535 passing cases; its 35 failures were subsequently diagnosed and fixed or aligned with changed contracts, then reverified in focused suites. An earlier shared-default-test-DB run was invalidated by another pytest session dropping that database; it is not treated as acceptance evidence. Subsequent pytest sessions used `KG_TEST_POSTGRES_DB=knowledge_entity_attributes_20261113` and ran one at a time.
- Full Node contracts: **1139 passed**, `.benchmarks/entity-attributes-final-node.log`. Real Edge browser suites: panel 6 + duplicate/review 3 + mindmap 21 + identity settings 2 = **32 passed**, desktop and phone sizes. First-draft command-failure recovery additionally exercised unmodified model functions in independent Node review.
- Native Milvus 3.0.2: isolated tail/window/grouping/scope probe passed; project/index count and subset probes passed. A disposable sealed 21-column collection upgraded to 27, retained its exact old row/version with NULL new fields, and reported 1/1 current-schema segments. Business old segments upgraded 4/4, retaining all 255 original native rows before project rebuild.
- Property budget: 2000 facts × 18000-character paragraphs; selected page **9 queries / 30751 bytes / 1079.15 ms**; 100 supports across 10 documents, source page **5 queries / 60116 bytes / 82.15 ms**, under concurrent local load. The ID page is materialized before paragraph decode/source counts. This is measured evidence, not a general latency guarantee.
- User explicitly authorized business synchronization. Verified PostgreSQL backup archive under `.benchmarks/entity-attributes-upgrade-20261113T064322Z/`; migration checksum check passed at schema39 and numbered migration0040 applied to reach **40**. No applied migration checksum was rewritten.
- Three project source backfills completed: **161 contexts, 160 active**, all current missing/invalid counts zero. Explicit Milvus4 project rebuilds validated and activated with **160 / 0 / 72** vectors; every project readiness check true. Milvus3 old-segment schema materialization enabled in Compose; no collection/data-volume wipe performed.
- Read-only live acceptance: 3 projects × keyword/semantic/hybrid with native type filters, **36 native Milvus calls**, attribute channel included. Disabling PostgreSQL keyword ranking did not affect these current searches. Business record-version count stayed **292**, assertion count **478**; no business entity merge or document re-extraction performed.
- Service restarted with final code on original port **8100**, health HTTP200, embedding ready, Milvus connected/collection ready. New assets return HTTP200. Python compileall and git diff --check passed. No commit or unrelated-file cleanup performed.
- Operational instructions: `docs/entity-identity-attributes-operations.md`. Existing unconfigured classes remain conservative; identity configuration must be reviewed/published, existing duplicates require explicit preview/confirmation, and old documents require explicit reprocessing with attribute extraction enabled to produce new candidates.
