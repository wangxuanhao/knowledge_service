# Self-contained ECharts asset

## Problem

The new knowledge service exposes `/vendor/echarts.min.js`, but the route reads
`data/kg_web/static/echarts.min.js`. The `data/` directory is intentionally ignored by
Git, so a fresh checkout returns HTTP 503 and graph views cannot initialize.

## Design

- Copy the existing, known-good ECharts distribution from the legacy project into a
  package-owned vendor directory under `knowledge_service/web/vendor/`.
- Change the vendor route to resolve the asset relative to the installed
  `knowledge_service` package instead of the repository-level legacy `data/` tree.
- Keep the public URL `/vendor/echarts.min.js` unchanged so the current HTML and graph
  code need no changes.
- Do not add a runtime fallback to the legacy project. Missing packaged assets should
  remain an explicit deployment error rather than silently coupling deployments.

## Verification

- Add an API regression test asserting that `/vendor/echarts.min.js` returns HTTP 200,
  JavaScript content, and a recognizable ECharts distribution marker.
- Demonstrate the test failing before the resource migration and passing afterward.
- Run the focused service tests and replay the live HTTP request after restarting the
  backend.

## Scope

This change only repairs the frontend visualization dependency. It does not alter
legacy project import or knowledge-data conversion behavior.
