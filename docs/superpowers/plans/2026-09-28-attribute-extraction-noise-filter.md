# Attribute Extraction Noise Filter Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Reduce obvious attribute noise with deterministic filtering and prevent singleton new attributes from entering open-discovery ontology drafts.

**Architecture:** Keep the current optional third LLM call and review pipeline intact. Add a narrow validation/ranking stage to `extract_attributes`, then constrain only newly induced datatype properties in `_induce`; existing ontology properties and raw discovery candidates remain available.

**Tech Stack:** Python 3.12, Pydantic 2, pytest, RDFLib, Semantica 0.7, FastAPI test client

---

### Task 1: Filter redundant and excessive extracted attributes

**Files:**
- Modify: `tests/service/test_typed_reviews.py`
- Modify: `knowledge_service/services/attribute_extraction.py`

- [ ] **Step 1: Write a failing service-boundary test**

Add a test beside the existing attribute-provider tests. Stub only `semantica.semantic_extract.providers.create_provider`. Return proposals containing one string value equal to its entity text, six valid proposals for the same entity with distinct confidences, and one proposal for a second entity. Assert:

```python
assert [(item.entity_index,item.attribute) for item in result.attributes] == [
    (0,'a2'),(0,'a3'),(0,'a4'),(0,'a5'),(0,'a6'),(1,'other')]
assert result.diagnostics['skipped_redundant_value'] == 1
assert result.diagnostics['skipped_attribute_limit'] == 1
```

Use confidence values so `a1` is the lowest-confidence non-redundant proposal and must be the capped item. Also assert the provider prompt says that most entities may have no attributes and that actions, prohibitions, responsibilities, ownership/classification, and document structure are not attributes.

- [ ] **Step 2: Run the focused test and verify RED**

Run:

```powershell
& 'D:\anaconda\envs\llm_model\python.exe' -m pytest tests/service/test_typed_reviews.py::test_attribute_provider_filters_redundant_values_and_caps_each_entity -q
```

Expected: FAIL because redundant values are retained, the per-entity cap is absent, and the new diagnostic keys do not exist.

- [ ] **Step 3: Implement the minimal extraction filter**

In `extract_attributes`:

- strengthen the prompt with the approved ontology-modeling rules;
- keep existing schema, entity-index, and evidence-location behavior;
- reject a string value when `value.strip().casefold() == entity.text.strip().casefold()`;
- group remaining proposals by `entity_index`, select at most five by descending confidence with model-return order as the tie-breaker, and return selected proposals in their original order;
- add `skipped_redundant_value` and `skipped_attribute_limit` to diagnostics.

Do not reject unverified evidence and do not add another model call.

- [ ] **Step 4: Run attribute tests and verify GREEN**

Run:

```powershell
& 'D:\anaconda\envs\llm_model\python.exe' -m pytest tests/service/test_typed_reviews.py -q
```

Expected: all tests pass. Update the existing exact diagnostics assertion only to include the two new zero-valued counters.

- [ ] **Step 5: Commit Task 1**

```powershell
git add -- knowledge_service/services/attribute_extraction.py tests/service/test_typed_reviews.py
git commit -m "fix: filter noisy attribute proposals"
```

### Task 2: Keep singleton new properties out of ontology drafts

**Files:**
- Modify: `tests/service/test_ontology_discovery.py`
- Modify: `knowledge_service/services/ontology_discovery.py`

- [ ] **Step 1: Write failing induction tests**

Add focused tests around `_induce`:

1. Supply one entity, one singleton attribute named `章节标题`, and two candidates named `发布日期`. Assert the mappings and ontology summary contain `发布日期` but not `章节标题`.
2. Supply a baseline ontology containing an existing datatype property labeled `发布日期`, then pass only one `发布日期` candidate. Assert the mapping reuses the baseline IRI.

Also add a draft-API test with a stubbed `SemanticaExtractor.discover` returning a singleton attribute. Assert the singleton attribute is still present in `candidate_snapshot` while absent from `draft['mappings']['attributes']`.

- [ ] **Step 2: Run the focused tests and verify RED**

Run:

```powershell
& 'D:\anaconda\envs\llm_model\python.exe' -m pytest tests/service/test_ontology_discovery.py -k "singleton or low_frequency or existing_attribute" -q
```

Expected: FAIL because every discovered attribute currently becomes an `owl:DatatypeProperty` and receives a mapping.

- [ ] **Step 3: Implement the minimal induction threshold**

In `_induce`:

- count attribute candidates by stripped `proposed_type`, while retaining every original spelling as a mapping key so materialization can still resolve the unchanged candidate;
- expose only names with at least two candidate occurrences to Semantica inference;
- when constructing mappings, always reuse a matching property from `baseline_turtle`, even when seen once;
- create a new datatype property and mapping only for names meeting the two-occurrence threshold;
- leave the input candidates and entity/relation induction unchanged.

Do not change `OntologyGenerator(min_occurrences=1)` globally because that would also alter entity and relation induction.

- [ ] **Step 4: Run discovery tests and verify GREEN**

Run:

```powershell
& 'D:\anaconda\envs\llm_model\python.exe' -m pytest tests/service/test_ontology_discovery.py -q
```

Expected: all tests pass. If an existing test intentionally checks readable attribute IRIs, give it two observations of the same attribute so it continues to test readability rather than singleton induction.

- [ ] **Step 5: Commit Task 2**

```powershell
git add -- knowledge_service/services/ontology_discovery.py tests/service/test_ontology_discovery.py
git commit -m "fix: require repeated attributes in discovery drafts"
```

### Task 3: Verify the integrated small fix

**Files:**
- No production changes expected

- [ ] **Step 1: Run the related regression suite**

```powershell
& 'D:\anaconda\envs\llm_model\python.exe' -m pytest tests/service/test_typed_reviews.py tests/service/test_ontology_discovery.py -q
```

Expected: 0 failures.

- [ ] **Step 2: Run formatting/static sanity checks**

```powershell
git diff --check HEAD~2..HEAD
git status --short
```

Expected: no whitespace errors; only expected ignored environment files remain outside Git status.

- [ ] **Step 3: Review the branch diff against the approved design**

Confirm no database schema, frontend, historical data, relation induction, or additional LLM calls changed.
