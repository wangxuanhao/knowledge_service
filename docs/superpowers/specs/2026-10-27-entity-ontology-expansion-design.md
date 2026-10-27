# Entity-grounded ontology expansion

User approved entity → confirmed type → related type content, beyond class-name-only matching. Use existing checkout and preserve pending QA/source/graph work.

## Behavior

- The existing default-off ontology_expansion flag enables both literal class matching and scoped entity-name/alias matching. Off must make no new reads or change ranking.
- Resolve entity mentions deterministically, preferring the longest name/alias, bounded to8. Respect project, valid/known time, applicability, metadata filters and ontology scope; default missing seed business time to the current clock without imposing it on document ranking; ignore invisible/deleted/unconfirmed records. Resolve types only to unambiguous active published classes, by full IRI or exact class surface. Read seeds afresh each request.
- Merge literal class seeds with entity-derived types and descendants. Preserve existing pure class expansion behavior. Entity expansion starts from unchanged original query retrieval and adds a separately bounded expanded-query/type channel. Keep matched entity anchors and most original hits; supplement each channel within its existing quota (at most one third, minimum1 only when quota>1); quota1 keeps an available original result. Deduplicate by exact version.
- Public diagnostics show entity/name/alias → type label, class matches, subclasses, candidate and actually-used supplemental counts. Preserve diagnostics in JSON/SSE evidence and server conversation snapshots. Show the path in both search and QA; do not imply same-type records are facts about the named entity.
- Ambiguous names may expose multiple matching entities/types as explicit paths, never guess a single entity. Missing entity/type gives a meaningful diagnostic. Expired/hidden/out-of-scope entity records must not trigger a path.

## Regression diagnosis

Reproduce9 document-policy failures. Repair fixture/index preparation or real defects at their source; do not bypass current-index pin, silently fall back, skip tests or alter implicit date expectations. Tests run in the isolated knowledge_test DB, one pytest session only.

## Validation

Failing-first tests for names/aliases/unknown types/scope, ranked direct-hit protection, flag-off identity, public status and persistence. Focused PG/retrieval/QA suites and all Node contracts; real authenticated test_1022_开放 queries 美团 with semantic/hybrid off/on plus a class-query compatibility check. Restart port8100 after backend edits. Report exact IDs and path/counts rather than count-only evidence.

## Primary references

- https://microsoft.github.io/graphrag/query/local_search/
- https://neo4j.com/docs/neo4j-graphrag-python/current/user_guide_rag.html
