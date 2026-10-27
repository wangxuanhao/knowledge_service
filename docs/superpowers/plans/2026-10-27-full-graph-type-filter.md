# Full graph progressive type filtering implementation plan

**Goal:** Restore the entity-type category row in full graph mode, with click-to-filter behavior across progressive loading.

**Architecture:** The authoritative session dataset retains all loaded records and canonical positions. Deduplicated additions update per-canonical-type node/within-type edge indexes. One active projection supplies renderer geometry, labels, picking and locating, while the loader/progress retain full dataset counts. Clearing the filter uses the original dataset. This avoids rescanning every loaded record on every batch or frame.

**Tech stack:** Native JavaScript/CSS, existing ECharts local graph and software-only WebGL full renderer, Node contracts, real Edge browser fixtures.

## Required behavior

- Type counts represent loaded unique entities, with an explicit partial/complete label.
- Canonical type IDs determine identity and colors; translated equal names stay separate.
- A selected type shows only matching nodes and edges whose two endpoints match.
- New pages and isolated-entity sweep append into the selected view; hidden elements cannot be picked or located.
- Pause/cancel retain current data/filter. Restart or project/scope changes reset both.
- All-types mode preserves current buffers/loading semantics and record identities. Camera changes do not build projections or scan type indexes.
- Bounded category paging keeps the number of rendered controls bounded when many types exist.

## Implementation / validation

- [x] Add failing Node tests for unique type indexes, within-type edge membership, incremental projection, filter clearing and hidden picking/locating.
- [x] Implement dataset indexes and active projection in `knowledge_service/web/full-graph.js`; route all renderer geometry/cache/label/picking access through its displayed dataset. Preserve full public loaded counts.
- [x] Add full-mode type controls to `full-graph-view.js`, scoped styles in `full-graph.css`, and version only changed assets in `index.html`. Retain canonical labels/colors and distinguish partial counts.
- [x] Add browser regression for first batch, filtering, append, hidden picking, name colors, clearing, completion and reset; verify desktop/mobile controls.
- [x] Run relevant then complete Node contracts and real software-only browser regressions. Measure new-index and filter costs on 300,000 entities, without claiming complete 300,000-node workflow acceptance.
- [x] Review final state and validate served resource bytes.

Results and the retained first-switch timing spike are recorded in `docs/research/2026-10-27-full-graph-type-filter-validation.md`.

Work in the current checkout; preserve unrelated dirty changes. No commits, service restarts, backend schema, scope or paging changes are required.
