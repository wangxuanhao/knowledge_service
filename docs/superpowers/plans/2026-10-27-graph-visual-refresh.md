# Graph Visual Refresh Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [x]`) syntax for tracking.

**Goal:** Improve graph readability and toolbar consistency on the existing progressive loader and existing ECharts/WebGL renderers, with repeatable pure-CPU before/after checks.

**Architecture:** Add a small shared `GraphAppearance` presentation module, using canonical record type as the stable color key through existing GraphPalette. Apply it to local graph styles and full graph buffers/labels, preserving positions, data counts, endpoints, session loader and authority semantics. Keep the graph toolbar left/right groups and all existing load controls; add full-mode zoom affordances and improve its independent settings styling without introducing a new graph engine or snapshot system.

**Tech Stack:** Native JS/CSS, existing ECharts/WebGL, Node test runner, existing Python Playwright + Edge software rendering, no new dependencies.

**Approved scope:** The scope correction at the top of `../specs/2026-10-27-cpu-progressive-graph-design.md`; user approved starting after clarification that current loading is already progressive. Prior proposal for renderer replacement/tiles/index infrastructure is excluded. Reuse this shared dirty checkout and preserve unrelated work; do not commit unrelated changes or modify production data.

## Task 1: Freeze baseline and test presentation contracts

- [x] Copy current web presentation assets to ignored `.benchmarks/graph-visual-refresh/baseline/` before edits. Keep baseline data and canvas sizes identical to after.
- [x] Add a standalone synthetic browser runner for actual graph display with API fixtures, and full renderer cases: 400/800, 300000/600000, 300000/1500000. Verify SwiftShader and disabled hardware Canvas/compositing, complete counts and GL errors.
- [x] Record same-size local/full screenshots and repeated timing of append/draw, camera movement, node/edge/blank picks and heap; preserve raw samples and report noise. No concurrent performance browsers.
- [x] Add meaningful failing behavioral tests for canonical type color parity independent of display label, order and filtering; selected/zoomed bounded non-overlapping labels; full view zoom controls and existing loader controls retained. Use real browser geometry/state for toolbar/switch regression rather than mirroring CSS strings.

## Task 2: Shared appearance and full renderer (dependent on baseline freeze)

Files: create `knowledge_service/web/graph-appearance.js`; modify `full-graph.js`; tests `graph-appearance.test.cjs`, `full-graph-renderer.test.cjs` and browser pixel checks.

- [x] Shared interface: `GraphAppearance.colorFor(record)` resolves a canonical `record.type` using GraphPalette; `rgbFor(record)` returns a cached 3-byte RGB value; `tokens` exposes background, edge, text, outline and selected hex colors. No ontology lookup, layout or facts are changed.
- [x] Load palette + appearance in graph fixtures and application before either renderer. Preserve compatibility if an isolated legacy fixture loads the renderer alone.
- [x] Full graph: prepare type colors on data sync, never one JS color loop per frame. Initial byte-RGB attribute / shared complex shader trial was measured 23–28% slower in software rendering and rejected. Use bounded uniform-color batches with cached grouped coordinates and a simple legacy relation shader instead; preserve original dataset positions/identity, bounded sparse outlines and selected ring. Keep complete relation texture caching and exact node/edge counts. The group-only trial retained ~10% dense-frame overhead, so additionally reuse a bounded complete-view GL texture only when ALL current geometry fits; use affine nearby camera moves and exact live groups for cropped/deep/unsupported views. This extends existing renderer caching only, not backend tiles or a new engine. Re-measure final variant and retain all rejected/intermediate raw data.
- [x] Keep normal/zoom label budgets, selected-name priority and boundary avoidance; apply the same text colors/spacing. Add overlap avoidance to zoomed labels without full-dataset scans or persistent animation.
- [x] Red/green tests and browser pixels verify actual type colors and relation visibility; inspect append/context-restore/destroy for buffer ownership.

## Task 3: Local graph and workbench controls

Files: `workspace.js` (only graph rendering section), `graph-workbench.js`, `index.html`, `workspace.css`, `full-graph-view.js`, `full-graph.css`; extend existing focused tests as needed.

- [x] Local graph nodes, type buttons and legend use the same canonical type colors as full mode. Keep translated labels as text, attribute virtual-node distinction, matched state and path evidence behavior. Refine neutral edges, white outlines and readable bounded label/edge-label styling; animation remains disabled.
- [x] Toolbar: zoom/fit/relayout grouped on left; path/settings grouped on right with matching button shape. Preserve inspector fixed heading/close and place node actions under identity; preserve right-side settings drawer behavior already covered by tests.
- [x] Full mode gets left-aligned minus/plus/fit controls (canonical camera zoom anchor); loading controls remain complete in their own row. Existing full settings still apply only to the full view, visibly named/styled as graph settings. Do not expose local-only path/settings as if they apply to full data.
- [x] Search ontology expansion becomes an accessible standalone pressed button via the existing checkbox state and change event; keep the checkbox/ID as the source of retrieval parameters. No retrieval or QA semantics change.
- [x] Use readable 12px UI text and consistent light neutral canvas/teal activation; no global shadows/glows/animation on nodes.
- [x] Bump touched frontend asset query versions in index only; preserve other concurrent asset edits.

## Task 4: Integrate, verify, review

- [x] Run relevant Node suites (appearance, full renderer/progressive/worker errors, retrieval view, graph interactions/types/paths, local graph, workspace contracts). Run isolated standalone browser tests; avoid parallel pytest processes.
- [x] Inspect actual screenshots for local/full small graphs, dense/long-name/selection states, sidebar open and narrow screens. Verify all expected controls and left/right positions; actual +/- scaling and fit, toggle behavior and progressive pause/resume/cancel work.
- [x] Run after comparison with exact baseline counts/canvas/flags. Record first batch, append, pan/zoom, picks and memory. If style changes regress repeatably, reduce their cost before finalizing. Existing 300k CPU limits remain reported, never imply 33ms was achieved without proof.
- [x] Obtain focused spec and code-quality reviews of changed graph files; address material findings. Produce a short report with raw-data links and screenshots, and update scope/verification docs.

Commands (repo cwd, no environment sourcing):

```powershell
node --test tests/service/graph-appearance.test.cjs tests/service/full-graph-renderer.test.cjs tests/service/full-graph-progressive.test.cjs tests/service/full-graph-worker-errors.test.cjs tests/service/retrieval-graph-view.test.cjs tests/service/graph-interaction.test.cjs tests/service/graph-types.test.cjs tests/service/graph-paths.test.cjs tests/service/local-graph-picker.test.cjs tests/service/local-graph-neighborhood.test.cjs tests/service/workspace-redesign.test.cjs tests/service/workspace-drawers.test.cjs
& 'D:/anaconda/envs/llm_model/python.exe' -X utf8 .benchmarks/graph-visual-refresh/run.py
git diff --check
```

Documentation-only plan review is advisory; user has already authorized implementation, so continue without an extra permission checkpoint.

## Verification outcome

666 Node contracts passed. Ten real software-browser scenarios have passing evidence; one initial navigation timeout needed an isolated retry and remains recorded. All six large nearby-motion repeats used a complete300k-entity view cache with zero cache/GL errors, P95 approximately18ms. Dense local graphs, append redraws, deep zoom, linear edge picking and host contention remain explicitly limited in the validation report; no universal300k performance promise. Accepted raw fullphase is after-rgb-fixed, local paired phases; earlier trials are retained and rejected/intermediate. No business data/backend/newdependencies changed.
