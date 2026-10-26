# Term Card Style Implementation Plan

**Goal:** Refine the existing ontology term cards in response to the user's screenshot: readable names, secondary technical identity, clear parent relationships, and stable actions.

**Architecture:** Keep the existing `OntologyCatalog` renderer, selection handlers, and controller commands. Scope presentation rules to ontology cards; leave global buttons and other views unchanged. Keep the approved white/teal workbench visual language.

**Tech Stack:** Native JavaScript/CSS; Node contracts; system Edge via the existing Python Playwright installation.

## Design

The title and parent links currently inherit the global button shadow/filter. The full IRI is permanently visible between the name and description, and 10px actions and metadata are hard to read.

Chosen approach: retain cards, give each a clear kind/name/description hierarchy, explicitly label parent types, collapse the full selectable IRI under “术语标识”, bound long property/relation lists, and align the action footer. Keep every parent/property/relation addressable and preserve historical read-only behavior.

Alternatives considered: CSS-only shadow removal leaves the crowded IRI and unclear hierarchy; replacing cards with a table changes the requested view and scope. A small card presentation refinement directly addresses the screenshot.

## Steps

- [x] Reproduce with production HTML/CSS/JS and deterministic offline ontology fixtures. Baseline fails because the hovered term title inherits the global raised-button shadow.
- [x] Refine `knowledge_service/web/ontology-model-catalog.js` card markup and scoped `ontology-model-catalog.css`. Keep selection and command payloads unchanged.
- [x] Bump the JS and CSS resource versions in `knowledge_service/web/index.html` and synchronize any pinned contracts.
- [x] Run existing Node service contracts and frontend static contracts; inspect browser fixtures at 1440/900/390 px, including long labels, long IRIs, multiple members, parent/title navigation, creation, and read-only history. Do not contact real business APIs.
- [x] Confirm local service serves matching resource bytes, review changes, and record verification. Preserve prior uncommitted work; no commit or backend restart is required.

Browser verification: `D:/anaconda/envs/llm_model/python.exe .benchmarks/term-card-style-check.py`. Artifacts stay in ignored `.benchmarks/term-card-style/`.

## Verification

- Plan review: approved, no blocking omissions.
- Browser baseline reproduced the inherited raised-button hover shadow. After the change, title and parent link shadows/filters are absent; full IRIs remain available under the collapsed identity disclosure. All three widths pass long-label overflow, bounded member lists, title/parent navigation, unchanged create payloads, and read-only historical controls. Real API writes: 0. Desktop and mobile screenshots inspected.
- Existing targeted Node contracts: 39 passed. Full Node service suite: 481 passed, 0 failed/skipped; log `.benchmarks/term-card-style/node-tests.log`.
- Frontend static contracts: 58 passed.
- Local homepage and both changed catalog assets return 200 with the new `term-cards-20261023-1` cache version and matching saved source bytes.
- `git -c core.safecrlf=false diff --check` passed.
