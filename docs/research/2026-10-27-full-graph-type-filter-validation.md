# Full graph progressive entity-type filtering validation

## Delivered behavior

Full graph mode has its own entity-type category row below the canvas. Counts represent loaded unique entities, with distinct partial and complete captions. Canonical IDs determine categories and colors, so equal translated labels remain separate. Unknown type is a real category (`''`); all-types uses `null`.

Choosing a type shows only its entities and relationships whose endpoints both belong to that type. Additional pages append into the chosen view. The full session dataset, manifest counts, request scope, page cursor and record identities remain intact. Pause/cancel preserve the type; restart and scope/project changes reset it. Picking, locating, labels, both cached layers and drawn counts use the displayed dataset.

`GraphDataset` updates per-type node/within-type-edge indexes only after deduplication. An active `TypeProjection` processes only newly indexed members. Only one projection is retained. The type UI renders at most 40 categories per page and avoids copying all category entries on each progress event. Full mode can grow to keep its minimum canvas and batch controls inside the panel on short displays.

## Functional evidence

- `node --test tests/service/*.test.cjs`: **705 passed, 0 failed/skipped** on the integrated implementation. Log: ignored `.benchmarks/full-type-node-final.txt`.
- Four real software-only Edge scenarios passed: progressive filtering and cached pixel removal/restoration at DPR 1 and 1.25. Report: `.benchmarks/full-type-final-browser.json`.
- Progressive replay covers A2 first batch → filter A → B2 batch with no visible additions → A1 plus a cross-type edge → isolated unknown entity sweep; all-types restores 6 entities/4 relationships. Filtering never makes another API request.
- Hidden B cannot be located or picked. Repeated equal-size type switches invalidate the previous complete overview and relationship cache. Real pixels from hidden type markers disappear and return after clearing the filter.
- Desktop 1440×728 and mobile 390×844 checks keep all visible batch controls inside the graph panel, with no horizontal page overflow.
- Delayed/failed restart manifest regressions cover the old synchronous cancellation progress race. The new start callback suppresses that old snapshot, so empty new sessions retain no stale categories.
- Read-only implementation review found that race; it was fixed and re-reviewed without outstanding blockers. Changed-file whitespace checks pass.

## Pure CPU synthetic measurements

The actual renderer ran in Edge with hardware acceleration disabled and ANGLE SwiftShader. Its unmasked renderer string was `ANGLE (Google, Vulkan 1.3.0 (SwiftShader Device (Subzero) (0x0000C0DE)), SwiftShader driver)`. Both GL and view-cache error values were zero.

The synthetic dataset has 300,000 entities, 30 evenly sized types and either 600,000 or 1,500,000 within-type relationships. Selecting a type displays 10,000 entities and 20,000/50,000 relationships. Measurements include updating the projection, uploading/drawing and `gl.finish`; they exclude server reads and browser page startup.

| Sample | Type-switch observations | Restore all |
| --- | --- | --- |
| 300k entities / 600k relationships | 46.3–322.7 ms | 218.4 ms |
| 300k / 1.5m, first run | 46.9–1768.8 ms | 510.5 ms |
| 300k / 1.5m, isolated profile | 34.8–230.0 ms | 450.0 ms |

The 1768.8 ms first-switch spike is retained, not discarded or claimed fixed. The isolated profile attributes its observed 34.8–230 ms switching /450 ms restore primarily to the update phase. It does not establish why the earlier spike occurred. CPU-only Node projection tests also confirmed an append of 50 matching entities processes only new indexes; the observed projection-only append was below 0.5 ms in these runs, excluding graph upload/draw.

Timing files are ignored `.benchmarks/full-type-cpu-browser.jsonl`, `full-type-cpu-browser.log`, `full-type-cpu-profile.log` and `full-type-perf.json`. Cached draw-command timings are not end-to-end frame presentation latency and are not reported as FPS.

These synthetic balanced-type samples do not establish a performance guarantee for every 300k-node distribution, cold start, network/database load, or a long production exploration. Selecting a dominant type can still require substantial copying and rendering. No complete 300k workflow acceptance is claimed.

## Delivery

Only frontend runtime assets and tests/notes changed for this feature. The backend session scope and paging protocol are unchanged. Service 8100 serves versioned `full-graph.js`, `full-graph-view.js` and `full-graph.css`; final static byte checks are recorded in the task execution. Browser business APIs are isolated fixtures, not an authenticated production-window replay.

## Layout follow-up and isolated release verification

The follow-up balances the evidence and graph panels using a stretched grid row. Size containment keeps long evidence lists scrolling inside the left panel without determining the row height. Type chips occupy two scrollable rows (64px); progress and batch controls share a compact row when width permits. The canvas retains its minimum size. A 73-entity /34-type real-browser replay verified aligned panel tops/bottoms at1920,1440 and1024 widths and a bounded long evidence list.

The release was assembled separately from independent mindmap, ontology and source-processing-status work, then exported directly from the staged index for verification. Its complete Node suite passed **688/688**. The earlier705 count covers the combined working tree. Another **19 standalone checks** passed against the exported release:6 raw-source recovery tests,3 immutable evidence-mapping tests,2 source-reader browser tests and8 graph/control browser checks.

The normal pytest attempt skipped all19 selected cases because its global PostgreSQL fixture was unavailable. The independent mock-only/source/browser checks do not claim PostgreSQL integration coverage. The first compact browser-runner attempt reused a page with an open settings drawer; its mode-click was intercepted. After adding the fixture-equivalent drawer/graph cleanup, the complete19-check run passed. Raw logs remain in the ignored `.benchmarks/graph-release-*` files.
