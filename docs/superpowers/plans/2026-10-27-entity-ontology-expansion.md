# Entity Ontology Expansion Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development. Steps use checkbox syntax for tracking.

**Goal:** Enable explainable scoped entity→type expansion and repair the document-QA regression setup.

**Architecture:** ScopedReads qualifies name/alias entities; a small ontology query helper resolves published types and descendants. Retrieval preserves direct query results while merging bounded secondary recall. Public diagnostics flow into existing search/QA/history rendering.

**Tech Stack:** Python/PostgreSQL/Milvus, native JS, pytest, node:test.

## Tasks

- [x] Reproduce and repair9 document-policy failures in existing test fixtures, then run shared policy/OAG regressions. Only this worker holds pytest initially.
- [x] Add failing unit/PG contracts for scoped entity mention lookup and deterministic type expansion.
- [x] Implement ScopedReads.mentioned_entities and services/entity_ontology_expansion.py; test with unittest while pytest lock is held.
- [x] Extend KnowledgeService expansion with scope and public entity paths. Add retrieval tests for unchanged original query and bounded supplementary ranking, preserving anchors/quotas.
- [x] Integrate secondary label-query recall and type recall, retaining index/authority fences.
- [x] Update QA/search status, snapshot allowlist, tooltip/help and cache versions; run failed-first Node contracts.
- [x] Run focused entity/scope/retrieval/QA Python regressions after lock transfer, then Node contracts and browser smoke.
- [x] Review security/scope/ranking; fix issues; restart real service and verify semantic/hybrid 美团 off/on plus class compatibility.
- [x] Record evidence, explain test failure root cause and final scope to user. No commit requested.

## Acceptance evidence

- `.benchmarks/entity-expansion-live-result.json`: normal authenticated semantic/hybrid 美团 on/off passed. Both modes resolve two same-name entity records to互联网企业/互联网平台,8 type candidates,2 final supplements (美团知识产权维权平台 and its链接 ipr.meituan.com relation). Direct entity anchors retained and per-channel quotas preserved.
- Parent-class互联网平台 remains compatible:7 type candidates in semantic/hybrid. SSE entity path and actual-used count survive account-owned server snapshot save and second-login restore. Only the generated acceptance chat was deleted.
- Node contract suite:619 passed. Edge browser integration/layout:7 passed, including entity-path display and history restore.
- Initial broadened Python run:136 passed,10 stale parity-test failures. These invoke retired projection_readiness/search_dense/search_hybrid APIs or omit current-index fixtures; the old fallback expectation contradicts current fail-closed policy. Assigned test maintenance without reintroducing production fallbacks. Final rerun recorded below when complete.
- Original document-policy9 failures: stale fixture preparation after implicit current-index pinning, not historical business data. Related shared fixture tests were repaired and59 tests passed before the broadened run.

## Final result

- Consolidated relevant Python run:157 passed,3 third-party dependency deprecation warnings (`.benchmarks/entity-expansion-python-final.log`). Includes the original document-policy regressions, original class-expansion tests, current-index/scope/channel parity, entity expansion, snapshots and QA formatting browser regression.
- Node:619 passed (`.benchmarks/entity-expansion-node-final.log`). Separate Edge interaction/layout:7 passed (`.benchmarks/entity-expansion-browser.log`).
- All original stale-test failures in the exercised files were repaired without skips or production fallbacks. Only external index/ranking boundaries are simulated in these test fixtures; real Milvus acceptance is independently recorded in the live result above.
- Normal service is running on8100 with the updated backend; runtime asset versions are bumped. No commit requested or created. Unrelated workspace edits retained.
