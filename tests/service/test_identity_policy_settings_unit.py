"""Pure policy governance checks; no repository, database, or pytest fixture."""
import copy
import json
from pathlib import Path
import subprocess
import unittest

from rdflib import Graph, Literal, URIRef
from knowledge_service.services.ontology import Ontology, editable_attribute_version
from knowledge_service.services.ontology_operations import apply_operations, build_operation, is_batch_eligible
from knowledge_service.services.ontology_drafts_core import is_annotation_only
from knowledge_service.services.ontology_drafts_commands import DraftCommandMixin

NS = 'https://knowledge.local/schema/'
BASE = '@prefix owl: <http://www.w3.org/2002/07/owl#> . <http://ex/A> a owl:Class . <http://ex/P> a owl:DatatypeProperty . '


def operation(name, value, *, action='add_annotation', target='http://ex/A', **qualifiers):
    spec = {'predicate': NS + name, 'value': value, **qualifiers}
    return build_operation(action, target, **{'after' if action == 'add_annotation' else 'before': spec})


class IdentityPolicySettingsTests(unittest.TestCase):
    def test_reserved_annotations_have_high_server_risk_and_new_release(self):
        for name in ('identityPolicy', 'identityScope', 'identityKey'):
            for action in ('add_annotation', 'remove_annotation'):
                op = operation(name, 'scoped', action=action)
                self.assertEqual(op['risk'], 'high')
                self.assertFalse(is_batch_eligible({**op, 'risk': 'low'}))
                self.assertFalse(is_annotation_only([op]))
        label = build_operation('add_annotation', 'http://ex/A', after={'predicate': 'http://www.w3.org/2000/01/rdf-schema#label', 'value': '名称'})
        self.assertEqual(label['risk'], 'low')
        self.assertTrue(is_annotation_only([label]))

    def test_invalid_modes_ambiguous_fields_and_nonclass_owners_cannot_compile(self):
        cases = [
            [operation('identityPolicy', 'auto')],
            [operation('identityPolicy', 'reference'), operation('identityPolicy', 'scoped')],
            [operation('identityScope', 'one'), operation('identityScope', 'two')],
            [operation('identityKey', 'id', target='http://ex/P')],
            [operation('identityKey', 'http://ex/code', type='iri')],
            [operation('identityKey', '')],
            [operation('identityScope', '   ')],
            [operation('identityKey', '1', datatype='http://www.w3.org/2001/XMLSchema#integer')],
            [operation('identityPolicy', 'reference'), operation('identityPolicy', 'reference', language='zh')],
        ]
        for commands in cases:
            with self.subTest(commands=commands), self.assertRaises(ValueError):
                apply_operations(BASE, commands)

    def test_remove_then_add_validates_final_graph_and_keeps_other_annotations(self):
        original = apply_operations(BASE + '<http://ex/A> <http://ex/note> "preserved" .', [operation('identityPolicy', 'scoped'), operation('identityScope', 'old')])
        commands = [operation('identityPolicy', 'scoped', action='remove_annotation'),
                    operation('identityScope', 'old', action='remove_annotation'),
                    operation('identityPolicy', 'reference'), operation('identityKey', 'code')]
        updated = Ontology(apply_operations(original, commands))
        self.assertEqual(updated.identity_policy('http://ex/A').mode, 'reference')
        self.assertEqual(updated.identity_policy('http://ex/A').keys, ('code',))
        self.assertIn((URIRef('http://ex/A'), URIRef('http://ex/note'), Literal('preserved')), updated.graph)
        current = original
        for command in commands:
            current = apply_operations(current, [command])
        self.assertEqual(Ontology(current).identity_policy('http://ex/A').mode, 'reference')

    def test_exact_identity_read_projection_and_legacy_cache_backfill_do_not_mutate_version(self):
        turtle = BASE + '<http://ex/A> <' + NS + 'identityPolicy> "reference"; <' + NS + 'identityKey> "code"^^<http://www.w3.org/2001/XMLSchema#string> .'
        ontology = Ontology(turtle)
        cls = ontology.summary()['classes'][0]
        self.assertEqual(len(cls.get('identity_annotations', [])), 2)
        key = next(a for a in cls['identity_annotations'] if a['predicate'] == NS + 'identityKey')
        self.assertEqual(key['datatype'], 'http://www.w3.org/2001/XMLSchema#string')
        version = {'id': 'v1', 'turtle': turtle, 'summary': {'classes': [{'id': 'http://ex/A'}], 'attributes': []}}
        frozen = copy.deepcopy(version)
        projected = editable_attribute_version(version)
        self.assertEqual(projected['summary']['classes'][0]['identity_policy']['mode'], 'reference')
        self.assertEqual(len(projected['summary']['classes'][0]['identity_annotations']), 2)
        self.assertEqual(version, frozen)

    def test_legacy_no_policy_is_scoped_and_cached_projection_is_reused_when_complete(self):
        ontology = Ontology(BASE)
        cls = ontology.summary()['classes'][0]
        self.assertEqual(cls['identity_annotations'], [])
        self.assertEqual(cls['identity_policy']['mode'], 'scoped')
        self.assertFalse(cls['identity_policy']['declared'])
        version = {'id': 'v1', 'turtle': BASE, 'summary': ontology.summary()}
        self.assertIs(editable_attribute_version(version), version)

    def test_projected_iri_key_can_be_removed_through_actual_frontend_and_command_adapter(self):
        turtle = BASE + ('<http://ex/A> <' + NS + 'identityPolicy> "scoped"; <'
            + NS + 'identityKey> <http://ex/code>; <' + NS + 'identityScope> "目录"@zh . '
            + '<http://ex/A> <http://ex/note> "preserved" .')
        cls = Ontology(turtle).summary()['classes'][0]
        script = ("const identity=require('./knowledge_service/web/ontology-model-identity.js');"
            "let input='';process.stdin.on('data',part=>input+=part).on('end',()=>"
            "process.stdout.write(JSON.stringify(identity.policyCommands(JSON.parse(input),"
            "{mode:'reference',scope:'',keys:''}))));")
        result = subprocess.run(['node', '-e', script], input=json.dumps(cls),
            text=True, encoding='utf-8', capture_output=True, check=True,
            cwd=Path(__file__).resolve().parents[2])
        commands = json.loads(result.stdout)
        adapted = []
        for command in commands:
            action, before, after = DraftCommandMixin._operation_args(command)
            adapted.append(build_operation(action, command['target_iri'], before=before, after=after))
        key_removal = next(op for op in adapted if (op.get('before') or {}).get('predicate') == NS + 'identityKey')
        self.assertEqual(key_removal['before'].get('type'), 'iri')
        scope_removal = next(op for op in adapted if (op.get('before') or {}).get('predicate') == NS + 'identityScope')
        self.assertEqual(scope_removal['before']['language'], 'zh')
        final = Ontology(apply_operations(turtle, adapted))
        self.assertEqual(final.identity_policy('http://ex/A').mode, 'reference')
        self.assertEqual(final.identity_policy('http://ex/A').keys, ())
        self.assertIn((URIRef('http://ex/A'), URIRef('http://ex/note'), Literal('preserved')), final.graph)


if __name__ == '__main__':
    unittest.main()
