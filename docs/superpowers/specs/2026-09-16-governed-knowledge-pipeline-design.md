# Governed Knowledge Pipeline Design

## Objective

Deepen the existing ontology knowledge service without replacing its RDF/OWL/SHACL model or its SQLite revision store. The resulting pipeline must keep open discovery unconstrained, govern every path into the formal graph through one interface, preserve source assertions separately from canonical facts, make entity resolution reviewable and reversible, expose document readiness independently from graph readiness, and provide reusable hybrid retrieval.

## Scope and chosen approach

Three approaches were considered:

1. **Incremental adjacent tables (chosen).** Keep canonical entities and relations in `record_versions`. Add first-class assertion, ingest-run, resolution-review, and merge-ledger tables beside it. This preserves the current API and bitemporal record behavior while removing governance state from document metadata.
2. **Fully normalized SQLite graph.** Move entities, facts, evidence, and histories into separate relational tables. This gives stronger SQL constraints, but would force a broad migration of all readers and the RDF projection before the domain model is proven.
3. **PostgreSQL/pgvector rewrite.** Adopt Utopia-like storage immediately. This solves scale concerns the project has not yet demonstrated and mixes infrastructure migration with semantic redesign.

The incremental approach is selected. `record_versions` remains the canonical revision store for documents, chunks, entities, and formal relations. In this design a current `kind=relation` record is the canonical **Fact**. A source-scoped **Assertion** is stored separately and may point to a Fact after acceptance.

## Non-goals

- Open discovery is not constrained to the current formal ontology.
- The project will not migrate to Rust, PostgreSQL, pgvector, or a graph database.
- No general-purpose rule engine or materialized inference layer is introduced.
- The change will not redesign the existing ontology candidate UI beyond adapting it to the new formal-write seam.
- Existing record history remains the source of valid-time and known-time graph queries.

## Core terminology

### Assertion

An Assertion is one source occurrence claiming a possible entity, relation, or attribute. It owns source-specific information:

- project, document version, and chunk;
- original subject, predicate, object, or literal;
- exact quote and character offsets;
- extracted validity interval;
- extraction confidence, backend, and ontology version;
- lifecycle status and decision reason;
- optional link to the accepted canonical record.

Assertion statuses are `pending`, `accepted`, `rejected`, `contradicting`, and `superseded`. Rejected assertions remain durable so that extraction failures can be measured and a later ontology version can reprocess them.

### Fact

A Fact is a canonical formal-graph relation after entity resolution and ontology validation. In the first implementation it remains a versioned `record_versions` row with `kind=relation`. Multiple accepted Assertions can reference one Fact. Removing one document retracts its Assertions but does not remove a Fact while other accepted Assertions still support it.

Entities and attribute-bearing entity records remain versioned records. Attribute assertions are first-class Assertions even though an accepted attribute currently updates the canonical entity record.

### Evidence

The Assertion is the first-class evidence-bearing claim. It contains the source anchor and decision state; a separate evidence table would duplicate it in the current scope. Callers may use the term evidence when reading accepted assertions for a Fact.

## Storage model

### `assertions`

The table stores immutable source identity plus mutable governance state:

- `id`, `project_id`, `kind`;
- `document_id`, `document_version_id`, `chunk_id`;
- `source_hash`, `start_char`, `end_char`, `quote`;
- `payload` JSON containing raw extracted terms, candidate IDs, value, confidence, validity, ontology version, and extractor metadata;
- `status`, `canonical_record_id`, `decision_reason`, `decision_version`;
- `created_at`, `decided_at`.

`canonical_record_id` may name a canonical entity or relation; attributes name the canonical entity whose properties contain the accepted value. Assertion IDs are deterministic source-occurrence IDs derived from storage namespace, document version, chunk ID, assertion kind, stable output ordinal, and a hash of normalized raw terms. A uniqueness constraint on that source-occurrence key makes replay idempotent; an unlike payload at the same key is an explicit collision failure. Indexes cover project/status, document, canonical record, and chunk. Repository methods own serialization and status transitions.

Legal transitions are:

```text
pending       -> accepted | rejected | contradicting | superseded
accepted      -> contradicting | rejected | superseded
contradicting -> accepted | rejected | superseded
rejected      -> pending only through an explicit reprocess operation
superseded    -> terminal
```

Every transition compares `decision_version`, increments it, and appends an audit event containing old state, new state, reason, actor, and time. `accepted` is the only support-counting state. `contradicting` may retain `canonical_record_id` to name the Fact it contradicts but does not support it. `rejected` clears the canonical reference unless the audit event needs to name the rejected target. `superseded` retains the reference for history but does not support it.

### `assertion_events`

This append-only table records every assertion transition. It makes reconsidering an accepted assertion compatible with the rule that one decision request cannot be applied twice: a request with a stale `decision_version` fails, while a later explicit reconsideration with the current version creates a new event.

### `ingest_runs`

Each ingest request has a durable run with:

- run and document identity;
- extraction mode;
- current active stage and run status;
- orthogonal `keyword_ready`, `semantic_ready`, `search_ready`, `candidate_ready`, `graph_ready`, and `document_ready` booleans;
- stage progress, error information, and timestamps.

The active stage is `received`, `parsing`, `indexing`, `extracting`, or `finalizing`. Run status is `queued`, `running`, `completed`, `failed`, or `interrupted`. Readiness is monotonic and independent. `document_ready` becomes true when the document and chunks are durable in every mode. `keyword_ready` and `semantic_ready` describe successful FTS and embedding outputs; `search_ready` is their logical OR and remains true after graph completion. If one backend fails during ingest, the other may still make the document searchable and the failed backend is retryable. `candidate_ready` and `graph_ready` mean the requested graph path completed. Pending review counts are fields, not readiness states.

Runs carry `job_id`, an `attempt` number, and `retry_of`. Retrying creates a new run pointing at the previous run and reuses the existing document/chunks when their content hash and stage outputs match. Cross-attempt stage outputs are keyed by `(document_version_id, chunk_id, stage, input_hash)`, while each run records which output keys it consumed. This lets a new run reuse a prior successful stage without confusing its own progress. Deterministic source-occurrence Assertion IDs prevent a retry of a partially successful ingest from duplicating claims. On restart, `running` becomes `interrupted`; a user or recovery job may retry from the last durable readiness boundary. Synchronous endpoints return the run plus the available result; background endpoints return `job_id`, `run_id`, and readiness fields using the same response shape.

### `entity_resolution_reviews`

The table records ambiguous pairs without merging them:

- left and right entity IDs;
- exact-name, alias, embedding, type-compatibility, and temporal signals;
- reason and score snapshot;
- `pending`, `merged`, or `kept_separate` decision plus `decision_version`;
- reviewer note and timestamps.

The new entity remains independently queryable while review is pending. Decisions compare and increment `decision_version`; merge operations require `expected_resolution_review_version` alongside record and Assertion expectations.

### `entity_merges`

Every merge is one explicit operation containing:

- survivor and merged entity IDs;
- source review, actor/reason, and status;
- before and after version IDs for every changed entity or relation;
- creation and reversal timestamps.

Merge application is one repository transaction: validate review version, compare every affected record version, create survivor/merged/relation revisions, reassign Assertions, consolidate colliding Facts, insert the ledger, and decide the review. If endpoint rewriting makes canonical Facts identical, the stable survivor is the lexicographically smaller pre-existing Fact ID; all accepted Assertions move to it, the duplicate Fact is superseded, and the ledger records both the Fact consolidation and every Assertion reassignment.

Reversal is a new append-only ledger operation linked by `reversal_of`, not an in-place erasure. It is allowed only when affected records, Assertion decisions/references, and the original merge ledger remain at the versions produced by the merge. The compare-and-set check and all restoration writes occur in one transaction. Later edits produce a conflict response rather than being overwritten. Reversal recreates the original entity/Fact revisions and Assertion references using the IDs recorded in the ledger.

### Full-text index

SQLite FTS5 indexes the current text of searchable chunks and entities. Index maintenance is derived infrastructure and does not create business revisions. Updates occur in the same repository transaction as current-record replacement where possible, and a rebuild operation repairs drift.

## Module interfaces and seams

### `AssertionRepository`

Owns assertion persistence and legal transitions. Callers do not edit document metadata to manage review state.

### `FormalFactWriter`

This is the single external seam for creating or changing formal knowledge. Its interface accepts a source Assertion or a deliberate manual assertion plus the current ontology and write policy. It returns a structured outcome:

```text
accepted_records
assertion_updates
review_items
ontology_proposals
diagnostics
```

It owns the repository transaction for a formal-write operation and performs:

1. structural validation;
2. ontology term and datatype resolution;
3. entity resolution;
4. canonical Fact matching or creation;
5. final domain/range and SHACL validation with canonical entity types;
6. atomic canonical-record, Assertion, audit-event, proposal, and review persistence.

Operation types are `extract`, `adopt_discovery`, `publish_discovery_draft`, `approve_review`, `manual_write`, `legacy_import`, `merge_rewrite`, `ontology_remap`, `ontology_publish`, `retract_source`, and `restore_snapshot`. Every call supplies the operation type, project, source assertions, expected current-record versions, expected assertion decision versions, expected resolution-review decision version when applicable, and expected ontology parent/version. A stale expectation aborts the whole operation.

The unit of atomicity is one source chunk for extraction and one explicit user operation for all other types. A malformed item is excluded before the chunk transaction and becomes a rejected Assertion where it can be parsed safely; other well-formed items in the chunk commit together. Database or validation failure within a chunk commits none of its formal changes. Multi-chunk ingestion may therefore be partially graph-ready and reports successful and failed chunk counts until finalization.

All paths into the formal graph call this module:

- ontology-guided extraction;
- approval of an open-discovery candidate;
- approval of a constrained extraction review;
- manual formal-record creation;
- formal import;
- entity-merge relation rewrites;
- ontology-change remapping.
- ontology draft publication and its materialized canonical records;
- legacy import.

Open discovery does not call this module until a user adopts a candidate into the formal graph. Discovery performs only output-shape, endpoint-existence, source-anchor, and offset validation.

The call-site migration matrix is:

| Existing entry point | Formal operation | Transaction contents |
|---|---|---|
| ontology ingestion | `extract` | chunk Assertions, canonical records, reviews |
| review approval | `approve_review` | Assertion decision plus canonical mutation |
| single discovery candidate adoption | `adopt_discovery` | existing ontology expectation, Assertion decision, canonical records |
| discovery draft publication/materialization | `publish_discovery_draft` | new ontology version, draft state, Assertions, canonical records |
| direct record endpoint | `manual_write` | manual Assertion and canonical record |
| legacy importer | `legacy_import` | grandfathered Assertions and canonical records |
| entity merge | `merge_rewrite` | record revisions, Assertion links, review, merge ledger |
| ontology proposal/publication | `ontology_publish` | ontology version, proposal decisions, affected Assertions/records |
| ontology remap | `ontology_remap` | affected Assertions, canonical revisions, validation reviews |
| document deletion/replacement | `retract_source` | Assertion transitions, attribute reconstruction, Fact retirement/key mappings |
| snapshot restore | `restore_snapshot` | compensating Assertions, canonical revisions, key mappings, restore ledger |

### `EntityResolver`

Entity resolution distinguishes mention alignment from entity merge:

- exact canonical-name or alias match with compatible type: align the mention;
- high semantic score with compatible type and no temporal contradiction: align the mention;
- review-band score: create a separate entity and a resolution review;
- low score or incompatible type: create a separate entity.

Default thresholds remain configurable per request. Automatic mention alignment never merges two existing entities. Only an explicit review decision or deliberate operator action performs a merge and writes the merge ledger.

### Canonical identity

Entity identity remains the stable entity ID selected by `EntityResolver`. A relation Fact's canonical key in the first release is:

```text
(project_id, predicate IRI, subject entity ID, object entity ID, valid_from, valid_until)
```

Literal attributes continue to materialize on entity records and are governed by attribute Assertions; relation literals and relation qualifiers are outside this release. The ontology version is provenance on Assertions, not part of Fact identity, when the predicate IRI survives unchanged. A changed IRI creates a distinct Fact unless an explicit ontology-remap operation adopts it.

Fact IDs are immutable UUIDs allocated when the Fact is first created. A separate `fact_keys` table enforces one active canonical key per project and maps a deterministic key hash to the immutable Fact ID. Concurrent creation relies on its unique constraint and returns the mapped Fact after verifying equality. A merge rewrite updates the key mapping transactionally while retaining the Fact ID. If the new key already maps to another Fact, the collision is consolidated into the lexicographically smaller Fact ID, Assertions are reassigned, and the other Fact is superseded. Reversal restores the earlier key mappings and Assertion references from the append-only merge/reversal ledgers.

Canonical records contain only normalized semantic fields and aggregate status. Source-specific `source_id`, chunk offsets, extraction text, confidence, backend, and ontology-version provenance live on Assertions. For backward compatibility, existing canonical records may retain legacy source fields, but new writers do not add them.

### `RetrievalEngine`

Provides one reusable interface:

```text
search(query, retrieval_mode, scope, content_channels, k, content_k=None)
```

The public parameter is named `retrieval_mode` everywhere. Retrieval backends are `keyword` and `semantic`; public modes are `keyword`, `semantic`, and `hybrid`. Content channels are `entity`, `chunk`, and `relation`. Hybrid independently retrieves backend ranks and fuses stable record IDs with `score += 1 / (60 + rank)`. Ties break by best individual rank and then stable record ID. Generic search treats `k` as the final total and retrieves up to `3k` candidates per included content channel/backend before fusion. QA supplies `content_k={entity: k_entities, chunk: k_chunks, relation: 0}` and then performs graph expansion; relation evidence comes from that expansion rather than an independent QA seed search.

FTS indexes only current versions. Requests without `known_at` use it after applying project, kind, metadata, and valid-time scope. A request with historical `known_at` uses a correctness-first version scan with lexical token scoring rather than current FTS; it never returns present-only rows. Historical semantic search continues to rank the already scoped version candidates. Relation search remains supported through fallback lexical scoring until relation FTS is added.

Responses include `requested_mode`, `active_mode`, `requested_content_channels`, `active_content_channels`, `requested_backends`, `active_backends`, `degraded_backends`, and per-content-channel hit counts. Hybrid degrades to one backend only when the other is unavailable and declares the degradation. An ingest-time missing backend is represented by its readiness flag and retry information; a later query-time backend failure is reported only for that query. Question answering uses hybrid retrieval by default, then expands graph neighbors from the selected entity seeds. Search endpoints expose the mode as an advanced option.

## End-to-end data flow

### Documents-only mode

1. Save the document receipt and ingest run.
2. Parse/chunk and commit chunks.
3. Commit chunks and set `document_ready=true`.
4. Independently build keyword and embedding outputs, set their readiness flags, and derive `search_ready` as their logical OR before completing the run.

### Open-discovery mode

1. Set `document_ready=true`, then complete at least one search backend so `search_ready=true` before discovery extraction.
2. Extract unconstrained candidates from committed chunks.
3. Persist candidates as pending Assertions without a `canonical_record_id`.
4. Mark the run `candidate_ready`.
5. When a user adopts a candidate, pass that Assertion through `FormalFactWriter`; acceptance links it to a canonical record.

### Ontology mode

1. Set `document_ready=true`, then complete at least one search backend so `search_ready=true` before ontology extraction.
2. Extract source-Assertion inputs under the selected ontology.
3. Pass the whole well-formed chunk to `FormalFactWriter`, which atomically creates Assertions, decisions, reviews, and canonical records; nothing is pre-persisted outside its transaction.
4. Persist safely parseable malformed outputs as rejected Assertions in the next independent chunk transaction.
5. Entity mentions are aligned, reviewed, or separated.
6. Accepted Assertions attach to canonical records; constraint failures remain rejected or pending.
7. Mark `graph_ready=true` even when review items remain, and expose the pending count separately. If one or more chunks failed, keep `graph_ready=false`, retain `search_ready=true`, and report retryable chunk failures.

### Deletion and revision

Document deletion or replacement supersedes the document's active Assertions. A canonical Fact remains active while at least one accepted, non-superseded Assertion supports it. If none remain, the service supersedes the Fact rather than erasing its history. The same rule applies when a review changes an Assertion from accepted to rejected.

Entity existence is not support-counted in the first release: an entity may remain as an intentionally created canonical identity after its source Assertions are gone, but it is marked `unsupported` for governance review. Attribute values are reconstructed from accepted attribute Assertions whenever one is retracted or reconsidered. If exactly one normalized value remains, it is materialized on the entity. If none remain, the property is removed in a new entity revision. If competing values remain, no value silently wins: the current value may stay only while at least one remaining accepted Assertion supports that exact normalized value. Otherwise the property is removed and marked unresolved until a conflict review selects one of the supported values.

## Error handling and consistency

- Document receipt creation is committed before parsing.
- Searchability is committed before graph extraction begins.
- A failed extraction leaves `search_ready=true`, preserves committed chunks, and records a retryable extraction boundary.
- One malformed assertion does not discard other assertions from the chunk.
- Fact and Assertion acceptance is atomic.
- Review decisions use expected status/version checks.
- Merge reversal refuses to overwrite records edited after the merge.
- FTS index failures do not create false business revisions; index drift is diagnosable and rebuildable.
- A missing embedding channel degrades hybrid search to keyword results, and a missing FTS channel degrades it to semantic results. The response declares active channels.

## API and UI behavior

- Ingest responses and jobs expose run stage, search readiness, graph readiness, pending review count, and retryable failure stage.
- Review APIs read first-class Assertions rather than scanning document metadata.
- Evidence inspection lists all Assertions supporting or contradicting a Fact.
- Entity-resolution review shows both entities, type and temporal compatibility, evidence excerpts, and the score snapshot.
- Merge history exposes merge and reversal operations.
- Search and question endpoints accept `retrieval_mode`; the default is `hybrid`.
- The UI offers Hybrid, Semantic, and Keyword modes as an advanced search control. Graph expansion remains a separate downstream step, not another mutually exclusive mode.

## Compatibility and migration

Existing projects remain readable. A transactional schema migration, recorded in `schema_migrations`, backfills all governance state before support-count behavior is enabled:

- pending/approved/rejected `review_candidates` map to pending/accepted/rejected Assertions;
- `discovery_candidates` map to pending Assertions unless their materialized canonical ID is known;
- every existing canonical relation receives at least one accepted Assertion per recoverable `source_id` or `merged_sources` occurrence;
- directly written, imported, or otherwise source-less canonical records receive a deterministic accepted `legacy_grandfathered` Assertion;
- entity properties receive deterministic accepted attribute Assertions when source provenance is recoverable, otherwise grandfathered Assertions;
- existing entity records receive provenance Assertions where recoverable, otherwise an unsupported/grandfathered marker according to creation metadata.
- every live legacy relation with valid predicate and entity endpoints receives a `fact_keys` row computed from its normalized current fields;
- when several legacy relations share one canonical key, the lexicographically smallest Fact ID becomes the mapping target, accepted Assertions are reassigned to it, and the duplicates are superseded in the migration transaction;
- a legacy relation with a missing predicate, missing endpoint, cross-project endpoint, or otherwise invalid canonical key is not silently indexed: it receives `legacy_invalid_fact_key`, stays readable, and enters a validation review before it can participate in canonical matching.

Legacy source anchors may be degraded: unknown offsets are null, quote text uses the stored relation/entity text when available, and `anchor_quality=degraded` makes the limitation explicit. Deterministic IDs use storage namespace plus legacy version/source identity; collisions compare payloads and fail the migration rather than silently merge unlike claims.

The migration is version-marked, idempotent, and all-or-nothing. Readers choose new or legacy behavior from the completed schema-migration version, never from the presence of a particular Assertion row. Legacy metadata remains in the first release. A later cleanup can remove it after export/import compatibility is proven.

Current relation records remain valid Facts. Existing merged entities remain readable even if they lack a merge-ledger row; only new merges are guaranteed reversible through the ledger.

Project export and projection export include Assertions, Assertion events, ingest runs, resolution reviews, merge ledgers, Fact-key mappings, and the migration version. Backward-compatible imports create grandfathered Assertions for canonical records when these sections are absent. Project deletion removes the new rows in a transaction; foreign keys use project-scoped cascade while canonical-record references use restricted or explicit cleanup semantics so cross-project links are impossible. FTS is rebuilt after import instead of being exported as truth.

Snapshot restore never deletes or rewrites append-only governance history. It is the `restore_snapshot` formal operation: canonical records receive new revisions matching the selected snapshot, Fact-key mappings are updated, and merge/reversal history remains intact. A terminal `superseded` Assertion is never revived. When snapshot support must be restored, the operation creates a deterministic new accepted Assertion with `restored_from_assertion_id` and `restore_operation_id`; the original history remains unchanged. Non-terminal Assertions receive ordinary compare-and-set transition events when legal. The restore ledger records every pre-restore and post-restore version/reference. Ingest-run history is historical execution evidence and is not rewound. Because restoration produces new current versions, merge reversal created before the restore will correctly fail its version guard unless the restore operation explicitly includes the matching compensating reversal.

## Verification strategy

Each change follows red-green-refactor tests. Required coverage includes:

- Assertion persistence, transitions, and migration idempotency;
- accepted, rejected, pending, contradicting, and superseded lifecycles;
- Fact survival with multiple Assertions and retirement after the final support disappears;
- discovery bypassing formal ontology constraints until adoption;
- every formal write path invoking the same writer;
- exact, high-score, review-band, and low-score entity resolution;
- review decision, merge ledger creation, successful reversal, and reversal conflict;
- `search_ready`-before-extraction ingest behavior and stage retry;
- keyword, semantic, hybrid RRF, and single-channel degradation;
- question answering using hybrid seeds before graph expansion;
- migration and existing API compatibility;
- migration followed by source deletion without accidental Fact retirement;
- merge-induced Fact consolidation and reversal;
- historical keyword search with `known_at`;
- export/import and snapshot/restore of all governance state;
- complete service and browser test suites.

An end-to-end scenario will ingest the same source in documents, discovery, and ontology modes; adopt one discovery assertion; create an ambiguous entity pair; review and merge it; retrieve the resulting Fact through keyword, semantic, and hybrid modes; reverse the merge; delete one of two supporting documents; and verify that the Fact remains supported by the other source.
