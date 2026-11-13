"""受限范围图探索、项目快照与确定性评估。"""
from collections import defaultdict
import logging
from time import perf_counter
from uuid import uuid4
from ..utils.diagnostics import timed
from .service import public
from .governance import writable
from .retrieval import RetrievalEngine
from ..core.time import utc_now


LOG = logging.getLogger('knowledge_service.explorer')


class Explorer:
    def __init__(self,service):
        self.service=service
        self.repo=service.repository

    def _native_graph_rows(self, project_id, scope, node_id, hops,
                           attribute_mode):
        """Legacy native decision; local views require a separately bounded read."""
        adapter=getattr(self.service,'neo4j_projection',None)
        readiness=getattr(self.repo,'projection_ready',None)
        reason=None
        if adapter is None:reason='neo4j_unconfigured'
        elif not node_id:reason='unseeded_graph'
        elif not isinstance(hops,int) or not 0<=hops<=2:reason='unsupported_hops'
        elif (scope.get('known_at') is not None or scope.get('filters')
                or scope.get('entity_type') or scope.get('predicate')
                or scope.get('ontology_scope','all')!='all'):
            reason='unsupported_scope'
        if reason:
            return {'rows':None,'backend':'postgres_bfs','degraded_reason':reason}
        try:
            ontology_reader=getattr(self.service,'structure_pending_ontology',None)
            if callable(ontology_reader):
                ontology=ontology_reader(project_id)
                policies=[ontology.identity_policy(str(class_iri)) for class_iri in ontology.classes]
                if any(policy.mode=='reference' or policy.keys for policy in policies):
                    return {'rows':None,'backend':'postgres_bfs',
                            'degraded_reason':'source_contexts_unsupported'}
            if not callable(readiness) or not readiness(project_id,'neo4j',3):
                return {'rows':None,'backend':'postgres_bfs',
                        'degraded_reason':'readiness_stale'}
            version_ids=adapter.expand(
                project_id,[node_id],hops,
                applicability=scope.get('applicability'),
                valid_at=scope.get('valid_at'),
                include_unknown=scope.get('include_unknown',True))
            rows=self.repo.current_records_by_version_ids(project_id,version_ids)
            if len(rows)!=len(dict.fromkeys(version_ids)):
                return {'rows':None,'backend':'postgres_bfs',
                        'degraded_reason':'current_backfill_mismatch'}
            return {'rows':rows,'backend':'neo4j_native','degraded_reason':None}
        except Exception:
            LOG.warning('Neo4j scoped expansion failed; using PostgreSQL BFS',
                        exc_info=True)
            return {'rows':None,'backend':'postgres_bfs',
                    'degraded_reason':'neo4j_read_failed'}

    def graph(self,p,scope,node_id=None,hops=1,attribute_mode='summary'):
        """Read the entity/relation network; selected properties load on demand."""
        from ..repository.scoped_reads import ScopedReads
        from .graph_neighborhood import present_neighborhood
        started=perf_counter()
        if attribute_mode not in {'none','summary','expanded'}:
            raise ValueError('不支持的属性展示模式')
        # Preserve old request values while retiring attribute canvas expansion.
        attribute_mode='none' if attribute_mode=='none' else 'summary'
        entity_type=scope.get('entity_type') or None
        predicate=scope.get('predicate') or None
        record_scope={key:value for key,value in scope.items()
                      if key not in {'node_id','hops','attribute_mode','entity_type','predicate'}}
        with timed('图谱探索', seeded=bool(node_id), hops=hops) as record:
            # Neo4j V3 expansion has neither source-context eligibility nor an
            # explicit result budget. Local views use the authoritative BFS.
            backend={'backend':'postgres_bfs','degraded_reason':
                     'bounded_authority_neighborhood' if node_id else 'unseeded_graph'}
            reads=ScopedReads(self.repo)
            if node_id:
                result=reads.neighbors(p,record_scope,[node_id],hops=hops,direction='both',
                    max_nodes=500,max_edges=1000,entity_type=entity_type,predicate=predicate)
            else:
                rows=self.service.scoped(p,{**record_scope,'kinds':['entity','relation']})
                nodes={row['id']:row for row in rows if row['kind']=='entity'
                       and (not entity_type or row['type']==entity_type)}
                edges=[row for row in rows if row['kind']=='relation'
                       and (not predicate or row['type']==predicate)
                       and row['subject_id'] in nodes and row['object_id'] in nodes]
                result={'records':[*nodes.values(),*edges],'truncated':False,'continuation':None}
            if node_id and not any(row['kind']=='entity' and row['id']==node_id
                                   for row in result['records']):
                raise KeyError(node_id)
            presented=present_neighborhood(self.service,reads,p,record_scope,result,
                                           [node_id] if node_id else [])
            nodes=[row for row in presented['records'] if row['kind']=='entity']
            edges=[row for row in presented['records'] if row['kind']=='relation']
            record.update(backend=backend['backend'],rows=len(result['records']),
                          graph_nodes=len(nodes),graph_edges=len(edges))
            return {key:value for key,value in presented.items() if key!='records'} | {
                'nodes':nodes,'edges':edges,'attribute_mode':attribute_mode,
                'selected_node_id':node_id,**backend,
                'budgets':{'max_nodes':500,'max_edges':1000} if node_id else None,
                'timing_ms':{'total':round((perf_counter()-started)*1000,1)}}

    def dashboard(self,p,scope):
        from ..repository.page_reads import PageReads
        return PageReads(self.repo).dashboard(p,scope)

    def sources(self,p,scope,*,limit=50,cursor=None,summary_only=False,state='all',document_id=None,query='',retrieval_mode='keyword',search_steps=None):
        from ..repository.page_reads import PageReads
        if search_steps and document_id is None:
            from .source_progressive_search import SourceProgressiveSearch
            return SourceProgressiveSearch(self.service).search(p,scope,query,
                retrieval_mode=retrieval_mode,search_steps=search_steps,limit=limit,
                cursor=cursor,summary_only=summary_only,state=state)
        if retrieval_mode not in {'keyword','semantic'}:
            raise ValueError('资料检索模式必须是 keyword 或 semantic')
        if not isinstance(query,str) or len(query)>2000:
            raise ValueError('资料搜索词必须是最多 2000 字符的字符串')
        if retrieval_mode=='semantic' and query.strip() and document_id is None:
            from .source_semantic_search import SourceSemanticSearch
            return SourceSemanticSearch(self.service).search(p,scope,query.strip(),
                limit=limit,cursor=cursor,state=state)
        return PageReads(self.repo).sources(p,scope,limit=limit,cursor=cursor,
            summary_only=summary_only,state=state,document_id=document_id,
            query='' if document_id is not None else query)

    def mindmap(self,p,scope,root_id,depth=3):
        from ..repository.scoped_reads import ScopedReads
        if type(depth) is not int or not 0<=depth<=5:
            raise ValueError('脑图深度必须是 0 到 5 之间的整数')
        scope={key:value for key,value in (scope or {}).items() if key not in {'root_id','depth'}}
        result=ScopedReads(self.repo).neighbors(p,scope,[root_id],hops=depth,
                                               direction='both',max_nodes=500,max_edges=1000)
        nodes={r['id']:r for r in result['records'] if r['kind']=='entity'}
        if root_id not in nodes:
            raise ValueError('脑图根实体不存在或不在当前范围内')
        adj=defaultdict(list)
        for edge in result['records']:
            if edge['kind']!='relation':continue
            adj[edge['subject_id']].append((edge['object_id'],edge.get('type') or edge['text']))
            adj[edge['object_id']].append((edge['subject_id'],edge.get('type') or edge['text']))
        count=[0];tree_truncated=[False]
        def branch(node,level,path):
            count[0]+=1
            item={'id':node,'text':nodes[node]['text'],'type':nodes[node].get('type'),'children':[]}
            if level==0 or node in path:return item
            children=[(target,predicate) for target,predicate in adj[node] if target not in path|{node}]
            if len(children)>40:tree_truncated[0]=True
            for target,predicate in children[:40]:
                if count[0]>=500:
                    tree_truncated[0]=True
                    break
                child=branch(target,level-1,path|{node});child['predicate']=predicate;item['children'].append(child)
            return item
        tree=branch(root_id,depth,set())
        return {'tree':tree,'bounded':True,
                'truncated':result['truncated'] or tree_truncated[0],
                'neighborhood_truncated':result['truncated'],'tree_truncated':tree_truncated[0],
                'continuation':result['continuation'],
                'budgets':{'max_nodes':500,'max_edges':1000,'max_tree_nodes':500,'max_children':40}}

    def snapshot(self,p,name,kind='manual'):
        with self.service.lock:
            records=self.repo.current_records(p)
            item={'id':str(uuid4()),'project_id':p,'name':name,'kind':kind,'created_at':utc_now(),
                  'record_count':len(records),'records':records,'ontologies':self.repo.list_ontologies(p)}
            self.repo.save_artifact('snapshot',item)
            return {k:v for k,v in item.items() if k not in ('records','ontologies')}

    def restore_snapshot(self,p,snapshot_id):
        with self.service.lock:
            snapshot=self.repo.get_artifact('snapshot',snapshot_id)
            if snapshot['project_id']!=p:raise KeyError(snapshot_id)
            current={r['id']:r for r in self.repo.current_records(p)}
            target={r['id']:writable(r) for r in snapshot['records']}
            for i,row in current.items():
                if i not in target and not row.get('metadata',{}).get('_audit'):
                    target[i]={**writable(row),'metadata':{**row.get('metadata',{}),'_deleted':True}}
            # 先保存恢复点；恢复会创建新版本，绝不抹除历史。
            backup=self.snapshot(p,'恢复前自动备份 · '+snapshot['name'],kind='auto')
            for row in target.values():row['metadata']={**row.get('metadata',{}),'restored_from_snapshot':snapshot_id}
            saved=self.service.write(p,list(target.values()),expected_versions={i:current[i]['version'] if i in current else 0 for i in target})
            return {'restored':len(saved),'recovery_snapshot_id':backup['id'],'recovery_snapshot_name':backup['name']}

    def evaluate(self,p,scope,entities,relations):
        graph=self.graph(p,scope,attribute_mode='none')
        names={r['id']:r['text'] for r in graph['nodes']}
        actual_entities=set(names.values())
        actual_relations={(names[r['subject_id']],r['type'].rsplit('#',1)[-1].rsplit('/',1)[-1],names[r['object_id']]) for r in graph['edges']}
        def score(actual,gold):
            tp=len(actual&gold);precision=tp/len(actual) if actual else int(not gold);recall=tp/len(gold) if gold else int(not actual)
            return {'precision':precision,'recall':recall,'f1':2*precision*recall/(precision+recall) if precision+recall else 0,
                    'true_positive':tp,'predicted':len(actual),'gold':len(gold),'missing':list(gold-actual),'extra':list(actual-gold)}
        return {'entities':score(actual_entities,set(entities)),'relations':score(actual_relations,{tuple(x) for x in relations})}

    def linked_explore(self, p, request):
        """工作台链接探索：检索 + 实体子图收窄（原 api.workspace.explore）。

        query 存在时按检索命中选种子节点、按跳数扩边；无 query 时返回整个范围图谱。
        """
        from .ontology import Ontology
        start = perf_counter()
        requested_mode = request.get('retrieval_mode') or ('semantic' if request.get('semantic') else 'keyword')
        query = request.get('query', '').strip()
        watermark = (self.repo.projections.project_watermark(p)
                     if query and request.get('known_at') is None else None)
        rows = self.service.scoped(p, request)
        types = {request['entity_type']} if request.get('entity_type') else set()
        if types and request.get('include_subclasses', True):
            for version in self.repo.list_ontologies(p):
                ontology = Ontology(version['turtle'])
                try:
                    parent = ontology.resolve(request['entity_type'], ontology.classes)
                except ValueError:
                    continue
                types.update(str(c) for c in ontology.classes if parent in ontology.parents(c))
        nodes = {r['id']: r for r in rows if r['kind'] == 'entity' and (not types or r.get('type') in types)}
        edges = [r for r in rows if r['kind'] == 'relation' and r.get('subject_id') in nodes and r.get('object_id') in nodes
                 and (not request.get('predicate') or r.get('type') == request['predicate'])]
        chunks = [r for r in rows if r['kind'] == 'chunk']
        candidates = [*nodes.values(), *edges, *chunks]
        candidate_versions = {row['id']: row['version_id'] for row in candidates}
        ontology_labels = self.service.ontology_labels(p)
        filtered_ms = (perf_counter() - start) * 1000
        hits = []
        timings = {}
        retrieval_result = None
        if query:
            retrieval_scope = {key: request[key] for key in RetrievalEngine.PAGE_SCOPE if key in request}
            # scoped() already validated and evaluated the original predicate.
            # Materialize that exact version frame without increasing AST depth;
            # pair checks and the final watermark fence reject intervening writes.
            retrieval_scope['filters'] = {'field': 'id', 'op': 'in', 'value': list(candidate_versions)}
            if request.get('kinds') is not None:
                retrieval_scope['kinds'] = request['kinds']
            channels = [kind for kind in ('entity', 'chunk', 'relation')
                        if request.get('kinds') is None or kind in request['kinds']]
            # Class labels are a bounded ontology channel, independent of the
            # native text index. Keep the already-qualified graph candidate gate.
            label_types = {row.get('type') for row in nodes.values()
                           if query.casefold() in ontology_labels.get(row.get('type', ''), '').casefold()}
            if label_types and requested_mode != 'semantic':
                retrieval_scope['_ontology_expansion'] = {'type_iris': sorted(label_types)}
            retrieval_result = RetrievalEngine(
                self.repo, self.service.encoder,
                getattr(self.service, 'milvus_store', None)).search(
                p, query, retrieval_mode=requested_mode, scope=retrieval_scope,
                content_channels=channels, k=request.get('k', 15),
                content_k={kind: 5 for kind in channels})
            hits = retrieval_result['hits']
            if any(hit.get('id') not in candidate_versions
                   or candidate_versions[hit['id']] != hit.get('version_id') for hit in hits):
                raise RuntimeError('探索期间项目知识已变化，请重新探索')
            selected = {r['id'] for r in hits if r['kind'] == 'entity'}
            selected |= {r[key] for r in hits if r['kind'] == 'relation' for key in ('subject_id', 'object_id')}
            for hit in hits:
                if hit['kind'] == 'chunk':
                    selected |= {i for i, node in nodes.items()
                                 if node.get('metadata', {}).get('chunk_id') == hit['id']
                                 or (node['text'] and node['text'] in hit['text'])}
            seeds = selected.copy()
            for _ in range(request.get('hops', 1)):
                selected |= {r[k] for r in edges
                             if r['subject_id'] in selected or r['object_id'] in selected
                             for k in ('subject_id', 'object_id')}
            nodes = {i: r for i, r in nodes.items() if i in selected}
            edges = [r for r in edges if r['subject_id'] in nodes and r['object_id'] in nodes]
        else:
            seeds = set()
        present = lambda row: {**public(row), 'type_label': ontology_labels.get(row.get('type', ''), row.get('type', ''))}
        result = {'nodes': [present(r) for r in nodes.values()], 'edges': [present(r) for r in edges],
                'hits': [{**r, 'type_label': ontology_labels.get(r.get('type', ''), r.get('type', ''))} for r in hits],
                'type_labels': {key: value for key, value in ontology_labels.items()
                                if key.startswith(('urn:', 'http://', 'https://'))},
                'matched_ids': sorted(seeds), 'candidate_count': len(candidates),
                'mode': retrieval_result['active_mode'] if retrieval_result else requested_mode,
                'requested_mode': requested_mode,
                'degraded': retrieval_result['degraded'] if retrieval_result else False,
                'backends': retrieval_result['backends'] if retrieval_result else {},
                'timing_ms': {'scope': round(filtered_ms, 1), **timings,
                              'total': round((perf_counter() - start) * 1000, 1)}}
        if watermark is not None and self.repo.projections.project_watermark(p) != watermark:
            raise RuntimeError('探索期间项目知识已变化，请重新探索')
        return result
