# Ontology draft workspace and version lineage

The user approved the recommended side-panel design and explicitly requested implementation after confirming version/draft lineage and non-destructive restoration semantics.

## Outcome

- Keep the main modeling area available to cards and graphs. Replace the above-canvas draft change section and permanent bottom publication block with a toolbar “草案变更” entry.
- Open changes in the right-hand region on demand. Definition inspection and draft workspace are mutually exclusive. Closing, switching views, and expanding review preserve the working draft, selection, and unsaved publication note.
- Provide definition changes, associated knowledge, source records, and historical drafts. Counts are sourced from the selected draft; definition counts and low-level operation counts remain distinct. Source runs are not mislabeled as tasks without an actual task reference.
- Existing validation, review, warning acknowledgement, note, fingerprint, revision, and idempotency behavior remains authoritative. Reuse the actual action controls, without parallel write handlers.
- A wider/full review mode serves concentrated review without creating a second business workflow.
- Historical drafts open read-only in a separate presentation; do not assign them to `OntologyModel.S.draft`, change canvas identity, or expose approval/publication controls for the active working draft beside an archived draft.
- Draft details link to their base publication and result publication by unique ontology ID. Version details link to `metadata.publication.draft_id`. Legacy versions without a source draft show an honest missing-history state.
- Version restoration continues creating a new revert draft, followed by review and a new publication. Existing version, draft, and knowledge history is preserved. No live approval, publication, or restore is part of development validation.

## Module boundaries

- `ontology-model.js`: mounted skeleton, compact version context, one-region panel mode and controller entry points.
- New `ontology-model-workspace.js/.css`: change panel, active tab, source/knowledge summaries, full-review layout, action-control placement, keyboard/focus handling, and read-only history module integration.
- New `ontology-draft-history.js`: lazy read-only draft directory/detail, readable final operations/decisions/source context, base/result version links. Uses existing GET endpoints, with explicit project/identity guards and retry. No writes and no model-state mutation.
- `ontology-model-panel.js`: existing governance controls; an explicit `OntologyWorkspace.syncActions()` hook after replacing action content; version detail source-draft link and optional exact-ID selection in `openHistory(ontologyId)`.

## Interaction contracts

`OntologyModel.openWorkspace(tab='definitions')` opens the active-draft workspace. `openDraftHistory(draftId)` opens the archive tab and reads that draft without changing active editing identity. `OntologyWorkspace.mount(controller)`, `render(state)`, `open(tab)`, `close({focus})`, `openDraft(id)` and `syncActions()` are optional in headless/legacy fixtures. `OntologyDraftHistory.mount(host,{model,onVersion})`, `show(draftId)`, `showList()`, `reset()` are read-only. Version links use `OntologyPanel.openHistory(ontologyId)` and never merely match display version numbers.

## Verification

Meaningful regressions cover panel exclusivity, close/reopen preservation, no eager archive reads, A→B→A/project response races, archive viewing without writes or active-draft mutation, exact-ID version/draft navigation, and existing gate/idempotency contracts. Browser fixtures use production HTML/CSS/JS at desktop and narrow widths with 60 definitions/188 operations/118 linked facts, delayed reads, and no live writes.
