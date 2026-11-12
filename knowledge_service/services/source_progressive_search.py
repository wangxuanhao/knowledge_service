"""Replay source conditions against one authoritative snapshot."""
from types import SimpleNamespace

from ..repository.page_reads import PageReads
from ..repository.scoped_reads import _snapshot_read
from .source_semantic_search import SourceSemanticSearch


class SourceProgressiveSearch:
    def __init__(self, service):
        self.repo = service.repository
        self.encoder = service.encoder
        self.milvus = getattr(service, 'milvus_store', None)

    @_snapshot_read
    def search(self, project, scope, query, *, retrieval_mode='keyword', search_steps,
               limit=50, cursor=None, summary_only=False, state='all'):
        if not isinstance(search_steps, list) or len(search_steps)>7:
            raise ValueError('此前检索条件最多 7 个')
        steps=[]
        for step in [*search_steps, {'query':query,'retrieval_mode':retrieval_mode}]:
            if not isinstance(step,dict) or set(step)!={'query','retrieval_mode'}:
                raise ValueError('无效的递进检索条件')
            term=step['query'];mode=step['retrieval_mode']
            if not isinstance(term,str) or not term.strip() or len(term)>2000:
                raise ValueError('递进搜索词必须为 1 到 2000 字符')
            if mode not in {'keyword','semantic'}:
                raise ValueError('资料检索模式必须是 keyword 或 semantic')
            steps.append({'query':term.strip(),'retrieval_mode':mode})
        scoped=dict(scope or {})
        # The service and all nested reads share this decorator's repository.
        semantic=SourceSemanticSearch(SimpleNamespace(repository=self.repo,
            encoder=self.encoder,milvus_store=self.milvus))
        identities=[]
        complete=True
        incomplete_reason=None
        for step in steps[:-1]:
            if step['retrieval_mode']=='keyword':
                scoped['_source_keyword_queries']=[*scoped.get('_source_keyword_queries',[]),step['query']]
            else:
                result=semantic.search(project,scoped,step['query'],limit=100,
                    state='all',_identity=identities)
                complete=complete and result['complete']
                if not result['complete']:
                    incomplete_reason=result.get('incomplete_reason')
                # These are bounded semantic parents, never all keyword IDs.
                # An empty list remains an explicit FALSE gate in SQL.
                scoped['_source_document_ids']=[d['id'] for d in result['documents']]
        scoped['_source_progressive_identity']=[steps,identities]
        final=steps[-1]
        if final['retrieval_mode']=='semantic':
            result=semantic.search(project,scoped,final['query'],limit=limit,cursor=cursor,state=state)
        else:
            result=PageReads(self.repo).sources(project,scoped,query=final['query'],
                limit=limit,cursor=cursor,state=state,summary_only=summary_only)
        bounded=any(step['retrieval_mode']=='semantic' for step in steps)
        result['bounded_by_semantic']=bounded
        result['complete']=complete and result.get('complete',True)
        if bounded:
            result['total_is_exact']=False
        if not result['complete'] and incomplete_reason:
            result.setdefault('incomplete_reason',incomplete_reason)
        return result
