# Separate Ontology Expansion Display Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development. Steps use checkbox syntax.

**Goal:** Make toggle effects visible in independent search and QA supplement regions.

**Architecture:** Keep existing hit payloads/order; explicit channels identify supplements. A shared presentation helper partitions rows and explains zero states. Literal-class retrieval adds accurate original-query attribution without changing final ranking. Existing frozen evidence/history contracts persist.

**Tech Stack:** Native JS/CSS, Python, node:test, pytest, Edge.

- [x] Shared presentation helpers and tests; scoped styles and runtime version bumps (root).
- [x] Search visible direct/supplement regions plus provenance context; focused tests (search worker).
- [x] QA direct/supplement E/reference regions, empty states, citation/history actions; focused tests (QA worker).
- [x] Literal-class attribution markers, evidence summary preservation; isolated tests and original ranking checks (backend worker).
- [x] Integrate, run Node + targeted Python serially, inspect Edge desktop/mobile/history screenshots and actual service acceptance.
- [x] Review final behavior, record evidence and deliver.

## Verified result

- Search renders independent direct and supplemental regions, separate deduplicated provenance context and visible zero-state diagnostics. Empty per-kind columns are omitted only in the expanded presentation so supplement content is visible sooner; expansion-off layout remains unchanged.
- QA supplemental E/R/F/U rows render outside the collapsed direct panel with unchanged citation IDs, validity state, frozen-source navigation and graph actions. Grouped results prefer direct membership on duplicate versions; graph-context merging preserves channel/rank fields only for exact retrieved id+version matches, retaining authority fields.
- Python broad related run:167 passed (`.benchmarks/ontology-sections-python.log`). After the final graph/duplicate fixes, fresh delta:50 passed (`.benchmarks/ontology-sections-python-delta.log`).
- All Node contracts:655 passed (`.benchmarks/ontology-sections-node-final.log`); final runtime-cache/section checks:46 passed (`.benchmarks/ontology-sections-node-delta.log`).
- Edge integration/layout:8 passed (`.benchmarks/ontology-sections-browser-final.log`), plus final expanded-mobile/history/composer check1 passed (`.benchmarks/ontology-sections-mobile.log`). Screenshots: `.benchmarks/ontology-sections/search-desktop.png`, `search-mobile.png`, `qa-desktop.png`, `qa-mobile.png`.
- Normal authenticated live service acceptance passed (`.benchmarks/ontology-sections-live-result.json`): semantic/hybrid entity and class attribution counts match final marked rows; QA stream keeps one offered supplemental item in the exercised question, ledger shows2 retrieved additions, and a second login restores the saved snapshot. Only its generated acceptance conversation was removed.
- Read-only review found two attribution defects; both fixed and re-reviewed with no remaining P1/P2 findings. Backend restarted on8100. No commit requested or made; unrelated graph/source work preserved.
