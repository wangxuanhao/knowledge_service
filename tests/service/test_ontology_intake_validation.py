"""Pure intake regressions; run directly without PostgreSQL or app imports."""
from contextlib import nullcontext
from copy import deepcopy
import importlib
from pathlib import Path
import sys
from types import ModuleType, SimpleNamespace
import unittest


# Keep the real service modules and SHACL engine, while avoiding the application
# package's eager API/encoder imports. The private namespace cannot affect other
# tests that import knowledge_service normally.
ROOT = Path(__file__).resolve().parents[2] / 'knowledge_service'
PACKAGE = '_intake_validation_regression'
for suffix in ('', '.services', '.repository', '.core', '.domain', '.utils'):
    module = ModuleType(PACKAGE + suffix)
    module.__path__ = [str(ROOT.joinpath(*suffix.lstrip('.').split('.')))] if suffix else [str(ROOT)]
    sys.modules[module.__name__] = module

intake_module = importlib.import_module(PACKAGE + '.services.ontology_intake')
publication_module = importlib.import_module(PACKAGE + '.services.ontology_intake_publication')
discovery = importlib.import_module(PACKAGE + '.services.ontology_discovery')
review = importlib.import_module(PACKAGE + '.services.ontology_drafts_review')
OntologyIntake = intake_module.OntologyIntake
ValidationFailed = intake_module.ValidationFailed


TURTLE = '''@prefix owl: <http://www.w3.org/2002/07/owl#> .
@prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .
@prefix sh: <http://www.w3.org/ns/shacl#> .
@prefix xsd: <http://www.w3.org/2001/XMLSchema#> .
<urn:C> a owl:Class ; rdfs:label "Thing" .
<urn:A> a owl:DatatypeProperty ; rdfs:label "Code" ;
    rdfs:domain <urn:C> ; rdfs:range xsd:string .
<urn:Shape> a sh:NodeShape ; sh:targetClass <urn:C> ;
    sh:property [ sh:path <urn:A> ; sh:maxCount 1 ] .'''
REQUIRED_TURTLE = TURTLE.replace('sh:maxCount 1', 'sh:minCount 1 ; sh:maxCount 1')
MAPPINGS = {'entity_types': {'Thing': 'urn:C'}, 'attributes': {'Code': 'urn:A'}}
CANDIDATES = [
    {'id': 'shared', 'kind': 'entity', 'text': 'Shared', 'proposed_type': 'Thing'},
    {'id': 'first', 'kind': 'attribute', 'entity_id': 'shared',
     'proposed_type': 'Code', 'value': 'one'},
    {'id': 'second', 'kind': 'attribute', 'entity_id': 'shared',
     'proposed_type': 'Code', 'value': 'two'},
]


def materialize(ids):
    candidates = [{**item, 'document_id': 'doc', 'document_version_id': 'doc-v1'}
                  for item in CANDIDATES if item['id'] in ids]
    records, skipped = discovery._materialize_candidates('p', {
        'id': 'draft', 'candidate_snapshot': candidates, 'mappings': MAPPINGS}, 'ontology')
    accepted, skipped, validation = discovery._validated_materialization(TURTLE, records, skipped)
    assert not skipped and len(accepted) == len(ids)
    return accepted, validation


class Repository:
    def __init__(self, existing=()):
        self.records = deepcopy(list(existing))
        self.document = {'id': 'doc', 'kind': 'document', 'version_id': 'doc-v1',
                         'metadata': {'discovery_candidates': deepcopy(CANDIDATES)}}
        self.runs, self.artifacts, self.writes = [], [], []
        self.turtle = TURTLE

    def current_records(self, project, *, kinds, **_):
        assert project == 'p'
        return deepcopy([self.document] if kinds == ['document'] else self.records)

    def get_project(self, project):
        assert project == 'p'
        return {'id': project}

    def list_ontologies(self, project):
        return [{'id': 'ontology', 'turtle': self.turtle}]

    def get_ontology(self, project, ontology_id):
        assert ontology_id == 'ontology'
        return self.list_ontologies(project)[0]

    def get_record_version(self, project, version):
        assert version == 'doc-v1'
        return deepcopy(self.document)

    def list_assertions(self, project):
        owners = {candidate: record['id'] for record in self.records
                  for candidate in discovery._materialized_candidate_ids([record])}
        return [{'id': candidate['id'], 'kind': candidate['kind'], 'document_id': 'doc',
                 'document_version_id': 'doc-v1', 'payload': candidate,
                 'canonical_record_id': owners.get(candidate['id']),
                 'status': 'accepted' if candidate['id'] in owners else 'pending',
                 'decision_version': 2 if candidate['id'] in owners else 1}
                for candidate in CANDIDATES]

    def formal_write_snapshot(self, project):
        snapshot = {'project_id': project, 'storage_namespace': 'test',
            'ontology_id': 'ontology', 'ontology': {'turtle': self.turtle},
            'records': [{**x, 'version': 1, 'version_id': x.get('version_id', x['id']+'-v1')}
                        for x in self.records],
            'assertions': self.list_assertions(project), 'assertion_events': [],
            'record_version_assertions': [], 'fact_keys': [],
            'resolution_reviews': [], 'merge_operations': []}
        snapshot['fingerprint'] = intake_module._fingerprint(snapshot)
        return snapshot

    def formal_write_context(self, context):
        return None

    def list_intake_runs(self, project):
        return deepcopy(self.runs)

    def intake_transaction(self):
        return nullcontext()

    def read_snapshot(self):
        return nullcontext()

    def get_discovery_run(self, project, run_id):
        if not run_id.isdigit():
            raise KeyError(run_id)
        return {'candidate_snapshot': [dict(x, assertion_id=x['id']) for x in CANDIDATES]}

    def execute_formal_write(self, plan):
        rows = [deepcopy(action['record']) for action in plan['actions']
                if action['kind'] == 'record_put']
        self.writes.extend(x['id'] for x in rows)
        self.records.extend(rows)
        return {'accepted_records': rows}

    def insert_if_absent(self, project, record):
        self.writes.append(record['id'])
        self.records.append(deepcopy(record))
        return deepcopy(record)

    def save_artifact(self, kind, artifact):
        self.artifacts.append((kind, deepcopy(artifact)))

    def create_discovery_run(self, project, run):
        self.runs.append(deepcopy(run))

    def transition_discovery_run(self, project, run_id, before, after, **changes):
        self.runs[0].update(status=after, **changes)


class FixturePageReads:
    """Supply the repository projection at the pure service test boundary."""
    def __init__(self, repository):
        self.repository = repository

    def discovery_inputs(self, project):
        assert project == 'p'
        return {'documents': [deepcopy(self.repository.document)], 'assertions': self.repository.list_assertions(project),
                'materialized': deepcopy(self.repository.records)}


# This module imports services under its private package name, so replacing its
# storage adapter cannot alter any production-package or PostgreSQL tests.
intake_module.PageReads = FixturePageReads


def intake_with_existing():
    records, _ = materialize({'shared', 'first'})
    for record in records:
        record['version_id'] = record['id'] + '-v1'
    repository = Repository(records)
    intake = OntologyIntake(SimpleNamespace(
        data=repository, ontology_drafts=lambda **_: SimpleNamespace()))
    # The run snapshot is downstream of the gate under test and would import
    # model adapters; use a tiny ledger value so a missing gate still reaches
    # its observable, incorrect write and terminal transition.
    intake._run = lambda *args, **kwargs: {'id': args[1], 'status': 'ready_to_finalize'}
    request = {'candidate_ids': ['shared', 'second'],
               'expected_ontology_id': 'ontology', 'create_new_draft': False,
               'confirmed_mappings': {'shared': 'urn:C', 'second': 'urn:A'}}
    return intake, repository, request


class IntakeAggregateValidationTests(unittest.TestCase):
    def composite(self, child_records, existing=()):
        plans = {str(index): {'kind': 'discovery', 'mode': 'run', 'run_id': str(index),
                 'records': records, 'accepted_records': records,
                 'candidate_outcomes': [{'candidate_id': candidate,
                    'status': 'materialized', 'reason_code': 'materialized'}
                    for candidate in discovery._materialized_candidate_ids(records)]}
                 for index, records in enumerate(child_records)}
        drafts = SimpleNamespace(repository=Repository(existing),
            _single_publication_effect_plan=lambda p, draft, turtle, approved:
                deepcopy(plans[draft['source_context']['publication_effects']['discovery_run_id']]))
        draft = {'id': 'draft', 'source_kind': 'manual', 'source_context': {
            'intake_bindings': [{'discovery_run_id': run_id} for run_id in plans]}}
        return publication_module.composite_plan(drafts, 'p', draft, TURTLE, [])

    def test_composite_rejects_cross_batch_max_count_violation(self):
        first, _ = materialize({'shared', 'first'})
        second, _ = materialize({'shared', 'second'})
        with self.assertRaises(ValidationFailed) as error:
            self.composite([first, second])
        self.assertFalse(error.exception.details['report']['conforms'])
        self.assertEqual(error.exception.details['report']['errors'][0]['constraint'],
                         'MaxCountConstraintComponent')

    def test_composite_rejects_conflict_with_authoritative_existing_attributes(self):
        existing, _ = materialize({'shared', 'first'})
        second, _ = materialize({'shared', 'second'})
        with self.assertRaises(ValidationFailed):
            self.composite([second], existing)

    def test_reuse_preview_blocks_conflict_with_existing_attribute(self):
        intake, repository, request = intake_with_existing()
        preview = intake.preview('p', request)
        self.assertFalse(preview['can_confirm_reuse'])
        self.assertTrue(any(item['code'] == 'ontology_validation_failed'
                            for item in preview['conflicts']))
        self.assertNotEqual({item['candidate_id']: item['outcome']
                             for item in preview['fact_outcomes']}['second'], 'add')
        self.assertEqual(repository.writes, [])

    def test_reuse_conflict_leaves_facts_runs_and_outbox_untouched(self):
        intake, repository, request = intake_with_existing()
        preview = intake.preview('p', request)
        frozen = {**request, 'source_fingerprint': preview['source_fingerprint'],
                  'preview_fingerprint': preview['preview_fingerprint'],
                  'idempotency_key': 'conflict'}
        before = deepcopy(repository.records)
        with self.assertRaises(ValidationFailed):
            intake.confirm_reuse('p', frozen, actor='author')
        self.assertEqual(repository.records, before)
        self.assertEqual(repository.writes, [])
        self.assertEqual(repository.runs, [])
        self.assertEqual(repository.artifacts, [])

    def test_required_attribute_selection_is_retained_as_one_complete_dataset(self):
        intake, repository, request = intake_with_existing()
        repository.records = []
        repository.turtle = REQUIRED_TURTLE
        preview = intake.preview('p', request)
        self.assertTrue(preview['can_confirm_reuse'])
        self.assertEqual({item['outcome'] for item in preview['fact_outcomes']}, {'add'})
        result = intake.confirm_reuse('p', {**request,
            'source_fingerprint': preview['source_fingerprint'],
            'preview_fingerprint': preview['preview_fingerprint'],
            'idempotency_key': 'complete'}, actor='author')
        self.assertEqual(result['receipt']['facts']['added'], 2)
        self.assertEqual(result['receipt']['facts']['skipped'], 0)
        self.assertEqual(len(repository.records), 2)

    def test_required_attribute_missing_from_selection_cannot_be_written(self):
        intake, repository, request = intake_with_existing()
        repository.records = []
        repository.turtle = REQUIRED_TURTLE
        request.update(candidate_ids=['shared'], confirmed_mappings={'shared': 'urn:C'})
        preview = intake.preview('p', request)
        with self.assertRaises(ValidationFailed):
            intake.confirm_reuse('p', {**request,
                'source_fingerprint': preview['source_fingerprint'],
                'preview_fingerprint': preview['preview_fingerprint'],
                'idempotency_key': 'incomplete'}, actor='author')
        self.assertEqual(repository.records, [])
        self.assertEqual(repository.runs, [])
        self.assertEqual(repository.artifacts, [])

    def test_composite_keeps_required_attribute_before_final_union_validation(self):
        plan = self.required_field_plan([{'shared', 'first'}])
        self.assertEqual(len(plan['entries'][0]['plan']['records']), 2)
        self.assertEqual(plan['receipt']['facts']['added'], 2)
        self.assertEqual(plan['receipt']['facts']['skipped'], 0)

    def test_composite_required_field_can_be_satisfied_by_another_selected_run(self):
        plan = self.required_field_plan([{'shared'}, {'shared', 'first'}])
        facts = plan['receipt']['facts']
        self.assertEqual({key: facts[key] for key in ('selected', 'added', 'reused', 'skipped')},
                         {'selected': 3, 'added': 2, 'reused': 1, 'skipped': 0})
        self.assertEqual(facts['per_batch'][0]['candidate_outcomes'][0]['status'], 'materialized')

    def test_singular_discovery_effects_cannot_publish(self):
        for effects in ({'kind': 'discovery', 'records': []},
                        {'kind': 'discovery', 'discovery_run_id': '0'},
                        {'discovery_run_id': '0'}):
            with self.subTest(effects=effects):
                drafts = review.DraftReviewPublishMixin()
                drafts.repository = Repository()
                draft = {'id': 'draft', 'source_kind': 'discovery',
                         'source_context': {'publication_effects': effects}}
                with self.assertRaises(intake_module.StaleSource):
                    drafts._publication_effect_plan('p', draft, TURTLE, [])
                self.assertEqual(drafts.repository.records, [])
                self.assertEqual(drafts.repository.runs, [])

    def test_attached_run_requires_current_intake_identity(self):
        with self.assertRaises(intake_module.StaleSource):
            self.required_field_plan([{'shared', 'first'}], current_run=False)

    def test_removed_project_import_effects_are_rejected(self):
        records, _ = materialize({'shared', 'first'})
        drafts = review.DraftReviewPublishMixin()
        drafts.repository = Repository()
        with self.assertRaises(intake_module.StaleSource):
            drafts._publication_effect_plan('p', {'id': 'draft', 'source_kind': 'import',
                'source_context': {'publication_effects': {'kind': 'import', 'records': records}}},
                TURTLE, [])

    def test_preview_counts_only_explicit_intake_bindings(self):
        drafts_module = importlib.import_module(PACKAGE + '.services.ontology_drafts')
        drafts = object.__new__(drafts_module.OntologyDrafts)
        drafts._history = lambda *_: ([], [])
        drafts._effective_operations = lambda *_: []
        drafts._ontology_for_draft = lambda *_: ('', [])
        result = drafts._preview('p', {'id': 'draft', 'source_context': {
            'intake_bindings': [{'discovery_run_id': 'current', 'selected_candidate_ids': ['a', 'b']}],
            'publication_effects': {'kind': 'discovery', 'discovery_run_id': 'old',
                                    'summary': {'candidate_count': 90}}}})
        self.assertEqual(result['associated_knowledge']['selected'], 2)
        self.assertEqual([x['run_id'] for x in result['associated_knowledge']['per_batch']], ['current'])

    def test_retired_existing_mapping_skips_entity_and_its_selected_attribute(self):
        final_turtle = TURTLE + '\n<urn:C> owl:deprecated true .'
        operations = [
            {'id': 'create-a', 'action': 'create_term', 'target_iri': 'urn:A'},
            {'id': 'datatype-a', 'action': 'set_datatype', 'target_iri': 'urn:A'},
            {'id': 'retire-c', 'action': 'retire_term', 'target_iri': 'urn:C'},
        ]
        dependencies = publication_module.ApprovedDependencies(
            operations, operations, operations, final_turtle)
        self.assertEqual(dependencies.missing({
            'candidate_id': 'shared', 'target_iri': 'urn:C', 'required_operation_ids': []}), [])
        plan = self.required_field_plan([{'shared', 'first'}], turtle=final_turtle,
            operations=operations, binding_operations={'first': ['create-a', 'datatype-a']})
        child = plan['entries'][0]['plan']
        self.assertEqual(child['records'], [])
        self.assertEqual({item['candidate_id']: (item['status'], item['reason_code'])
                          for item in child['candidate_outcomes']}, {
            'shared': ('skipped', 'ontology_validation_failed'),
            'first': ('skipped', 'ontology_validation_failed')})
        self.assertEqual(plan['receipt']['facts']['added'], 0)
        self.assertEqual(plan['receipt']['facts']['skipped'], 2)
        self.assertIn('已停用或不存在', next(item['reason'] for item in child['skipped']
                                        if item['candidate_id'] == 'shared'))

    def required_field_plan(self, selections, *, current_run=True, turtle=REQUIRED_TURTLE,
                            operations=(), binding_operations=None):
        repository = Repository()
        repository.turtle = turtle
        runs = {}
        for index, selected in enumerate(selections):
            candidates = [{**item, 'document_id': 'doc', 'document_version_id': 'doc-v1', 'assertion_id': item['id']}
                          for item in CANDIDATES if item['id'] in selected]
            run = {'id': str(index), 'status': 'draft_created', 'unified_draft_id': 'draft',
                   'candidate_snapshot': candidates, 'accepted_candidate_ids': sorted(selected),
                   'mappings': MAPPINGS, 'candidate_bindings': [
                       {'candidate_id': item['id'],
                        'target_iri': 'urn:C' if item['kind'] == 'entity' else 'urn:A',
                        'required_operation_ids': (binding_operations or {}).get(item['id'], [])}
                       for item in candidates]}
            if current_run:
                run['intake_request_hash'] = 'frozen'
            runs[str(index)] = run
        repository.get_discovery_run = lambda p, run_id: deepcopy(runs[run_id])
        drafts = review.DraftReviewPublishMixin()
        drafts.repository = repository
        drafts._active_operations = lambda *_: list(operations)
        drafts._history = lambda *_: (list(operations), [])
        draft = {'id': 'draft', 'source_kind': 'manual',
                 'source_context': {'intake_bindings': [
                     {'discovery_run_id': run_id} for run_id in runs]}}
        return drafts._publication_effect_plan('p', draft, turtle, list(operations))

    def test_validation_context_preserves_unrelated_history_and_receipt_scope(self):
        intake, repository, request = intake_with_existing()
        old = {'id': 'unrelated', 'kind': 'entity', 'type': 'urn:Historical',
               'text': 'Historical', 'ontology_id': 'old-ontology', 'version_id': 'old-v1'}
        repository.records = [deepcopy(old)]
        preview = intake.preview('p', request)
        result = intake.confirm_reuse('p', {**request,
            'source_fingerprint': preview['source_fingerprint'],
            'preview_fingerprint': preview['preview_fingerprint'],
            'idempotency_key': 'history'}, actor='author')
        self.assertEqual(repository.records[0], old)
        self.assertEqual(result['receipt']['facts']['selected'], 2)
        self.assertEqual(result['receipt']['facts']['added'], 2)
        self.assertEqual(result['receipt']['facts']['reused'], 0)
        self.assertEqual(result['candidate_ids'], ['second', 'shared'])


if __name__ == '__main__':
    unittest.main()
