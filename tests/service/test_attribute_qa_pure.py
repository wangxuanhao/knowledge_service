import json
import unittest
from knowledge_service.services.answers import answer_messages,missing_evidence_text,final_answer


class AttributeQaTests(unittest.TestCase):
    def test_identity_provenance_uses_exact_accepted_source_mapping_across_revisions(self):
        from knowledge_service.services.provenance import _SourceCapture
        from types import SimpleNamespace
        record=dict(id='e',version_id='newv',version=2,kind='entity',text='规范名称',type='Platform',
            recorded_at='2025-01-01',metadata={'identity_policy':'reference'})
        old=dict(record,version_id='oldv',version=1,text='原始名称')
        event=dict(id='event1',assertion_id='a1',canonical_record_id='e',to_status='accepted',from_status='pending',
            created_at='2024-01-01',decision_version=1,actor='reviewer',reason='accept')
        assertion=dict(id='a1',kind='entity',status='accepted',quote='原始名称',created_at='2024-01-01')
        repo=SimpleNamespace(get_record_version=lambda p,v:record if v=='newv' else old,
            list_record_version_assertions=lambda p,record_version_id:[] if record_version_id=='newv' else [{'assertion_id':'a1','assertion_event_id':'event1'}],
            get_assertion=lambda *a:assertion,list_assertion_events=lambda *a:[event])
        proof=dict(id='event1',version=1,assertion_id='a1',record_version_id='oldv')
        graph=_SourceCapture(repo,'p','test').capture(dict(record,matched_source_context=proof,_allowed_assertion_ids=['a1']))
        self.assertTrue(any(e['target_ref']=='assertion:a1' and e['relation']=='supported-by' for e in graph['edges']))

    def markers(self):return [{'id':'set1','entity_id':'platform','predicate':'description','version':1,'state':'open','member_count':2}]

    def test_both_prompt_purposes_receive_frozen_conflict_markers(self):
        for purpose in ('document_explanation','rule_judgment'):
            messages=answer_messages('平台职责',[],as_of='2025-01-01',known_at='2025-01-01',
                answer_purpose=purpose,attribute_conflicts=self.markers())
            material=json.loads(messages[1]['content'])
            self.assertEqual(material['query_context']['attribute_conflicts'],self.markers())
            self.assertIn('争议',messages[0]['content'])

    def test_missing_evidence_identifies_unresolved_conflicts(self):
        text=missing_evidence_text({'attribute_conflicts':self.markers()})
        self.assertIn('冲突',text)
        self.assertIn('审核',text)

    def test_evidence_only_answers_keep_conflict_warning_with_other_material(self):
        text,mode,audit=final_answer('', 'evidence_only',
            [{'citation':'E1','text':'另一条已确认的事实'}],{'attribute_conflicts':self.markers()})
        self.assertIn('冲突',text)
        self.assertIn('另一条已确认的事实',text)


if __name__=='__main__':unittest.main()
