"""Offline contracts for the real HTTP pipeline acceptance command."""
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

SCRIPT = Path(__file__).resolve().parents[2] / 'scripts' / 'verify_platform_rule_pipeline.py'


def module():
    if not SCRIPT.is_file():
        raise AssertionError('The real platform pipeline command is missing')
    spec = importlib.util.spec_from_file_location('platform_pipeline', SCRIPT)
    loaded = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(loaded)
    return loaded


def candidates():
    return [
        {'id': 'a', 'kind': 'entity', 'text': '平台', 'proposed_type': '平台', 'document_version_id': 'v1'},
        {'id': 'b', 'kind': 'entity', 'text': '创作者', 'proposed_type': '主体', 'document_version_id': 'v1'},
        {'id': 'r', 'kind': 'relation', 'subject_id': 'a', 'object_id': 'b', 'proposed_type': '管理', 'document_version_id': 'v1'},
    ]


class PipelineContracts(unittest.TestCase):
    def test_selection_requires_explicit_relation_and_both_same_version_endpoints(self):
        api = module()
        self.assertEqual(api.select_relation(candidates()), ['a', 'b', 'r'])
        rows = candidates()
        rows[1]['document_version_id'] = 'v2'
        with self.assertRaisesRegex(RuntimeError, '端点'):
            api.select_relation(rows)
        with self.assertRaisesRegex(RuntimeError, '端点'):
            api.select_relation(candidates()[1:])

    def test_selection_is_deterministic_and_skips_incomplete_relation(self):
        api = module()
        rows = [{'id': 'missing', 'kind': 'relation', 'subject_id': 'absent', 'object_id': 'b'}] + candidates()
        self.assertEqual(api.select_relation(rows), ['a', 'b', 'r'])
        rows.insert(0, {'id': 'self', 'kind': 'relation', 'subject_id': 'a', 'object_id': 'a', 'document_version_id': 'v1'})
        self.assertEqual(api.select_relation(rows), ['a', 'b', 'r'])

    def test_state_resume_rejects_different_input_without_overwriting_evidence(self):
        api = module()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            saved = {'base_url': 'http://127.0.0.1:8100', 'input_sha256': 'aaa',
                     'project_id': 'owned', 'created_for_validation': True}
            api.write_json(root / 'state.json', saved)
            self.assertEqual(api.load_state(root, saved['base_url'], 'aaa')['project_id'], 'owned')
            with self.assertRaisesRegex(RuntimeError, '输入|服务'):
                api.load_state(root, saved['base_url'], 'bbb')
            self.assertEqual(json.loads((root / 'state.json').read_text(encoding='utf-8')), saved)

    def test_batch_attention_is_explicit_failure_and_never_silently_ignored(self):
        api = module()
        api.check_batch_items([{'id': 'ok', 'status': 'completed'}])
        with self.assertRaisesRegex(RuntimeError, 'ocr_low_confidence'):
            api.check_batch_items([{'id': 'bad', 'status': 'needs_attention', 'error_code': 'ocr_low_confidence'}])

    def test_records_use_public_records_page_contract(self):
        api = module()
        pipeline = api.Pipeline(None, '.', {'project_id': 'p'}, 10)
        calls = []
        def call(method, path, body):
            calls.append(body)
            return {'records': [{'id': str(len(calls))}], 'next_cursor': 'next' if len(calls) == 1 else None}
        pipeline.call = call
        self.assertEqual(pipeline.records('entity'), [{'id': '1'}, {'id': '2'}])
        self.assertEqual(calls[1]['cursor'], 'next')

    def test_accepted_draft_resumes_from_its_published_ontology(self):
        api = module()
        with tempfile.TemporaryDirectory() as directory:
            pipeline = api.Pipeline(None, directory, {'project_id': 'p', 'draft_id': 'd'}, 10)
            pipeline.call = lambda *args: {'id': 'd', 'status': 'accepted', 'published_ontology_id': 'onto'}
            pipeline.publish()
            self.assertEqual(pipeline.state['ontology_id'], 'onto')

    def test_lineage_requires_each_selected_assertion_and_exact_formal_endpoints(self):
        api = module()
        rows = candidates()
        records = [
            {'id': 'fa', 'kind': 'entity', 'ontology_id': 'onto'},
            {'id': 'fb', 'kind': 'entity', 'ontology_id': 'onto'},
            {'id': 'fr', 'kind': 'relation', 'subject_id': 'fa', 'object_id': 'fb', 'ontology_id': 'onto'},
        ]
        assertions = [{'id': name, 'status': 'accepted', 'canonical_record_id': 'f' + name,
                       'document_version_id': 'v1', 'chunk_id': 'chunk', 'quote': '原文证据'} for name in ['a', 'b', 'r']]
        self.assertEqual(api.verify_lineage(['a', 'b', 'r'], rows, records, assertions, 'onto'), {'a': 'fa', 'b': 'fb', 'r': 'fr'})
        for row in rows:
            row['assertion_document_version_id'] = 'v1'
            row['document_version_id'] = 'final-ready-version'
        self.assertEqual(api.verify_lineage(['a', 'b', 'r'], rows, records, assertions, 'onto'), {'a': 'fa', 'b': 'fb', 'r': 'fr'})
        with self.assertRaisesRegex(RuntimeError, '断言'):
            api.verify_lineage(['a', 'b', 'r'], rows, records, assertions[:-1], 'onto')
        assertions[-1]['document_version_id'] = 'other'
        with self.assertRaisesRegex(RuntimeError, '来源'):
            api.verify_lineage(['a', 'b', 'r'], rows, records, assertions, 'onto')
        assertions[-1]['document_version_id'] = 'v1'
        records[-1]['object_id'] = 'fa'
        with self.assertRaisesRegex(RuntimeError, '端点'):
            api.verify_lineage(['a', 'b', 'r'], rows, records, assertions, 'onto')

    def test_index_resume_reuses_saved_job_without_second_mutation(self):
        api = module()
        with tempfile.TemporaryDirectory() as directory:
            api.write_json(Path(directory) / 'index-job.json', {'id': 'existing'})
            pipeline = api.Pipeline(None, directory, {'project_id': 'p'}, 10)
            pipeline.call = lambda *args: self.fail('A saved index job must be reused')
            self.assertEqual(pipeline.index_job()['id'], 'existing')

    def test_legacy_assertion_is_verified_against_frozen_text_and_exact_chunk(self):
        api = module()
        text = '平台管理创作者。'
        assertion = {'document_version_id': 'v1', 'quote': '平台'}
        evidence = {'document': {'version_id': 'v1'},
            'chunk': {'start_char': 0, 'end_char': len(text), 'text': text},
            'location': {'mode': 'exact', 'start_char': 0, 'end_char': 2},
            'integrity': {'complete': True, 'source_hash_status': 'unavailable'},
            'source_location': {'status': 'available', 'spans': [{'block_id': 'block'}]}}
        frozen = {'version_id': 'v1', 'text': text}
        self.assertEqual(len(api.verify_evidence(assertion, evidence, frozen)), 64)
        with self.assertRaisesRegex(RuntimeError, '哈希|切片'):
            api.verify_evidence(assertion, evidence, {**frozen, 'text': '被修改的原文'})
        with self.assertRaisesRegex(RuntimeError, '哈希'):
            api.verify_evidence({**assertion, 'source_hash': 'wrong'}, evidence, frozen)


if __name__ == '__main__':
    unittest.main()
