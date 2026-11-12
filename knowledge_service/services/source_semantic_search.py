"""Bounded semantic source recall using qualified chunks and PG parent summaries."""
import base64
import hashlib
import json
from math import isfinite

from ..repository.page_reads import PageReads
from ..repository.scoped_reads import _snapshot_read
from ..utils.filters import encode_filter_json
from .retrieval import RetrievalEngine


class SourceSemanticSearch:
    def __init__(self, service):
        self.repo=service.repository
        self.encoder=service.encoder
        self.milvus=getattr(service,'milvus_store',None)

    @_snapshot_read
    def search(self, project, scope, query, *, limit=50, cursor=None, state='all', _identity=None):
        if type(limit) is not int or not 1<=limit<=200:
            raise ValueError('资料目录分页上限必须在 1 到 200 之间')
        scope=dict(scope or {})
        PageReads._state_scope(scope,state)
        self.repo.get_project(project)
        if scope.get('known_at') is not None:
            raise ValueError('历史时间视图不支持语义检索')
        counts={'all':0,'ready':0,'processing':0,'failed':0}
        if scope.get('kinds') is not None and 'document' not in scope['kinds']:
            if cursor is not None:
                raise ValueError('资料检索游标已失效，请重新搜索')
            return {'documents':[],'total':0,'counts':counts,'next_cursor':None,
                'search_mode':'semantic','result_limit':100,'matched_chunk_count':0,
                'total_is_exact':False,'complete':True}
        engine=RetrievalEngine(self.repo,self.encoder,self.milvus)
        chunk_scope={key:scope[key] for key in ('valid_at','known_at','applicability','include_unknown') if key in scope}
        chunk_scope.update(filters=None,_source_document_scope=scope)
        # Even an empty recalled set must surface unavailable index/model errors.
        result=engine.search(project,query,retrieval_mode='semantic',scope=chunk_scope,
            content_channels=['chunk'],k=100)
        chunks=result['hits']
        best={}
        for chunk in chunks:
            source=chunk.get('source_id')
            score=float(chunk.get('score') or 0)
            if not isfinite(score):
                raise RuntimeError('Milvus 返回无效的排名分数')
            previous=best.get(source)
            if source and (previous is None or (-score,chunk['id'],chunk['version_id'])<
                    (-float(previous['score']),previous['id'],previous['version_id'])):
                best[source]=chunk
        docs=PageReads(self.repo).source_summaries(project,scope,list(best))
        complete=result.get('complete',False) and len(docs)==len(best)
        docs.sort(key=lambda doc:(-float(best[doc['id']]['score']),doc['id']))
        for doc in docs:
            chunk=best[doc['id']]
            doc['match']={'score':float(chunk['score']),'chunk_id':chunk['id'],
                'chunk_version_id':chunk['version_id'],'text':str(chunk.get('text') or '')[:280]}
            status=doc.get('metadata',{}).get('status')
            category=status if status in {'processing','failed'} else 'ready'
            counts[category]+=1;counts['all']+=1
        # Bind every authoritative chunk version, parent version and score, so
        # source changes or a new ranking cannot silently shift later pages.
        identity=[project,scope,query,'semantic',engine._index,state,
            [(d['id'],d['version_id'],d['match']) for d in docs],
            [(c['id'],c['version_id'],c['score']) for c in chunks]]
        fingerprint=hashlib.sha256(encode_filter_json(identity).encode()).hexdigest()
        if _identity is not None:
            _identity.append(fingerprint)
        offset=0
        if cursor is not None:
            try:
                token=json.loads(base64.urlsafe_b64decode(cursor.encode()).decode())
                offset=token['offset']
                if token['fingerprint']!=fingerprint or type(offset) is not int or not 0<offset<len(docs):
                    raise ValueError()
            except (ValueError,KeyError,TypeError,UnicodeError):
                raise ValueError('资料检索游标已失效，请重新搜索') from None
        selected=[d for d in docs if state=='all' or
            (d.get('metadata',{}).get('status') if d.get('metadata',{}).get('status') in {'processing','failed'} else 'ready')==state]
        page=selected[offset:offset+limit]
        following=offset+len(page)
        next_cursor=(base64.urlsafe_b64encode(json.dumps({'fingerprint':fingerprint,'offset':following}).encode()).decode()
                     if following<len(selected) else None)
        return {'documents':page,'total':len(selected),'counts':counts,'next_cursor':next_cursor,
            'search_mode':'semantic','result_limit':100,'matched_chunk_count':len(chunks),
            'total_is_exact':False,'complete':complete,
            **({'incomplete_reason':result.get('incomplete_reason') or 'source_authority_changed'} if not complete else {})}
