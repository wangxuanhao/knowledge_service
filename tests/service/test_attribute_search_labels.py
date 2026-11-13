import unittest
from types import SimpleNamespace
from unittest.mock import patch
from knowledge_service.services.service import KnowledgeService


class AttributeSearchLabelTests(unittest.TestCase):
    def test_value_only_hit_keeps_qualified_owner_name_and_property_in_text(self):
        fact=dict(id='a',version_id='av',kind='attribute',subject_id='owner',owner_id='owner',property_id='opaque',
                  type='opaque',text='退款条件正文',value='退款条件正文',matched_value='退款条件正文',metadata={})
        engine=SimpleNamespace(_index=None,search=lambda *args,**kw:{'hits':[dict(fact)],'candidate_count':1,
            '_backend':'milvus','active_mode':'keyword','complete':True,'backends':{}})
        with patch('knowledge_service.services.llm_models.LLMModels',lambda r:None),\
             patch('knowledge_service.services.service.RetrievalEngine',return_value=engine),\
             patch('knowledge_service.services.retrieval_context.enrich_source_context',lambda *a:None),\
             patch('knowledge_service.repository.scoped_reads.ScopedReads.by_ids',return_value=[{'id':'owner','text':'平台甲'}]):
            service=KnowledgeService(SimpleNamespace(),SimpleNamespace(identity='test',semantic=False))
            service.ontology_labels=lambda p:{'opaque':'业务职责'}
            result=service.search('p',dict(query='退款条件',retrieval_mode='keyword',kinds=['attribute']))
        hit=result['hits'][0]
        self.assertEqual(hit['owner_label'],'平台甲')
        self.assertIn('平台甲',hit['text']);self.assertIn('业务职责',hit['text']);self.assertIn('退款条件正文',hit['text'])


if __name__=='__main__':unittest.main()
