import unittest
from test_identity_binding_validation_pure import snapshot,entity,plan


class AttributeBindingTests(unittest.TestCase):
    def fact(self):return dict(id='a',kind='attribute',subject_id='canonical',type='description',text='value',
        value='原始值',datatype='http://www.w3.org/2001/XMLSchema#string')

    def test_accept_and_transition_cannot_bind_a_different_literal(self):
        source=dict(id='s',kind='attribute',payload={'kind':'attribute','value':'另一个值'},actor='reviewer')
        with self.assertRaisesRegex(ValueError,'属性.*值|原生'):
            plan(snapshot([entity(),self.fact()]),assertions=[{**source,'canonical_record_id':'a'}])
        source.update(status='pending',decision_version=1,canonical_record_id=None)
        with self.assertRaisesRegex(ValueError,'属性.*值|原生'):
            plan(snapshot([entity(),self.fact()],[source]),decisions=[dict(id='s',expected_version=1,status='accepted',
                canonical_record_id='a',reason='review',actor='reviewer')])

    def test_legacy_missing_datatype_is_inferred_without_coercing_value(self):
        source=dict(id='s',kind='attribute',payload={'value':'原始值'},canonical_record_id='a')
        result=plan(snapshot([entity(),self.fact()]),assertions=[source])
        self.assertTrue(any(action['kind']=='assertion_accept' for action in result['actions']))


if __name__=='__main__':unittest.main()
