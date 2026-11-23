import unittest
from types import SimpleNamespace
from unittest.mock import patch


class AttributeRetrievalHitsTests(unittest.TestCase):
    def helper(self):
        from knowledge_service.services.retrieval_hits import qualified_hits
        return qualified_hits

    def test_hydrates_supports_only_for_exact_context_candidates(self):
        from knowledge_service.repository.projection_contexts import ProjectionContexts

        plain = dict(id='plain', version_id='plain-v1', kind='entity', text='manual')
        contextual = dict(id='context', version_id='context-v1', kind='entity', text='contextual',
                          metadata={'identity_source_qualified': True})
        rejected = dict(id='rejected', version_id='rejected-v1', kind='entity', text='stale')
        hits = [
            dict(record_id='plain', version_id='plain-v1', score=3),
            dict(record_id='context', version_id='context-v1', score=2,
                 support_context_id='support', support_context_version=4),
            dict(record_id='other', version_id='rejected-v1', score=1,
                 support_context_id='bad', support_context_version=1),
        ]
        support = {'id': 'support', 'version': 4, 'metadata': {'applicability': {
            'platforms': {'mode': 'specific', 'values': ['美团']}}}}
        with patch.object(ProjectionContexts, 'collect', return_value={'context': [support]}) as collect:
            result = self.helper()(SimpleNamespace(_db=object()), 'p', {'known_at': '2026-01-01'},
                                   [plain, contextual, rejected], iter(hits))

        collect.assert_called_once_with('p', [contextual], known_at='2026-01-01')
        self.assertEqual([row['id'] for row in result], ['plain', 'context'])
        self.assertEqual(result[1]['matched_source_context']['version'], 4)

    def test_context_free_search_does_not_read_source_support(self):
        from knowledge_service.repository.projection_contexts import ProjectionContexts

        row = dict(id='plain', version_id='v1', kind='entity', text='manual')
        with patch.object(ProjectionContexts, 'collect', side_effect=AssertionError('unused source read')):
            result = self.helper()(SimpleNamespace(_db=object()), 'p', {}, [row],
                                   [dict(record_id='plain', version_id='v1', score=2)])
        self.assertEqual(result, [{**row, 'score': 2.0}])

    def test_support_context_must_match_all_scope_fields_and_exact_version(self):
        contexts = {'e': [dict(id='a', version=1, valid_from='2020-01-01', valid_until='2021-01-01',
            metadata={'applicability': {'platforms': {'mode':'specific','values':['A']}}}),
            dict(id='b', version=2, valid_from='2024-01-01', metadata={'applicability': {'platforms': {'mode':'specific','values':['B']}}})]}
        repo=SimpleNamespace(current_source_contexts=lambda *a,**k:contexts)
        row=dict(id='e',version_id='v',kind='entity',text='platform',metadata={'identity_policy':'reference'})
        scope={'valid_at':'2024-06-01','applicability':{'platforms':{'mode':'specific','values':['A']}}}
        hits=[dict(record_id='e',version_id='v',support_context_id='a',support_context_version=1,score=1)]
        self.assertEqual(self.helper()(repo,'p',scope,[row],hits),[])
        scope['applicability']['platforms']['values']=['B']
        hits[0].update(support_context_id='b',support_context_version=1)
        self.assertEqual(self.helper()(repo,'p',scope,[row],hits),[])
        hits[0]['support_context_version']=2
        self.assertEqual(len(self.helper()(repo,'p',scope,[row],hits)),1)

    def test_windows_deduplicate_fact_and_return_bounded_matched_value(self):
        repo=SimpleNamespace(current_source_contexts=lambda *a,**k:{})
        row=dict(id='a',version_id='v',kind='attribute',subject_id='e',type='p',value='x'*8000+'TAIL',text='old display')
        hits=[dict(record_id='a',version_id='v',score=1,window_start=7900,window_end=8004),
              dict(record_id='a',version_id='v',score=.9,window_start=0,window_end=6000)]
        result=self.helper()(repo,'p',{},[row],hits)
        self.assertEqual(len(result),1)
        self.assertTrue(result[0]['matched_value'].endswith('TAIL'))
        self.assertLessEqual(len(result[0]['value']),6000)
        self.assertTrue(result[0]['value_truncated'])
        self.assertEqual(result[0]['owner_id'],'e')

    def test_source_qualified_hit_requires_context(self):
        repo=SimpleNamespace(current_source_contexts=lambda *a,**k:{})
        row=dict(id='e',version_id='v',kind='entity',metadata={'identity_source_qualified':True})
        self.assertEqual(self.helper()(repo,'p',{},[row],[dict(record_id='e',version_id='v')]),[])


if __name__=='__main__':unittest.main()
