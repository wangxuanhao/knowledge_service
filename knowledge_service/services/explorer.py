"""受限范围图探索、项目快照与确定性评估。"""
from collections import Counter,defaultdict,deque
import hashlib
import json
from time import perf_counter
from uuid import uuid4
from ..repository import OntologyNotPublished
from ..utils.diagnostics import timed
from .service import public
from .governance import writable
from .retrieval import RetrievalEngine
from ..core.time import utc_now


class Explorer:
    def __init__(self,service):
        self.service=service
        self.repo=service.repository

    @staticmethod
    def _virtual_id(prefix, identity, used):
        """Return a deterministic response-only ID that cannot shadow a real node."""
        encoded=json.dumps(identity,ensure_ascii=False,allow_nan=False,sort_keys=True,
                           separators=(',',':')).encode('utf-8')
        digest=hashlib.sha256(encoded).hexdigest()
        candidate=f'__ks_{prefix}__:{digest}'
        salt=0
        while candidate in used:
            salt+=1
            candidate=f'__ks_{prefix}__:{digest}:{salt}'
        used.add(candidate)
        return candidate

    def _entity_attributes(self,p,attributes,entity_ids,labels):
        """Project current attributes and unresolved conflicts for graph entities."""
        ontology_cache={}
        try:
            from .ontology import Ontology
            current_ontology=self.repo.get_ontology(p)
            ontology=Ontology(current_ontology['turtle'])
            ontology_cache[current_ontology['id']]=ontology
        except (KeyError,OntologyNotPublished):
            ontology=None
        def stable_predicate(value,ontology_id=None):
            if value.startswith(('urn:','http://','https://')):return value
            model=ontology
            if ontology_id and ontology_id not in ontology_cache:
                try:ontology_cache[ontology_id]=Ontology(self.repo.get_ontology(p,ontology_id)['turtle'])
                except (KeyError,OntologyNotPublished):ontology_cache[ontology_id]=None
            if ontology_id:model=ontology_cache.get(ontology_id) or model
            if model is not None:
                try:return str(model.resolve(value,model.attributes))
                except ValueError:pass
            return value
        assertions=self.repo.list_assertions(p)
        assertions_by_id={row['id']:row for row in assertions}
        mappings=self.repo.list_record_version_assertions(p)
        support_by_version=defaultdict(set)
        for mapping in mappings:
            assertion=assertions_by_id.get(mapping['assertion_id'])
            if (assertion and assertion['status']=='accepted' and
                    assertion.get('canonical_record_id')==mapping['record_id']):
                support_by_version[mapping['record_version_id']].add(assertion['id'])

        grouped=defaultdict(list)
        for row in attributes:
            if row.get('subject_id') not in entity_ids:
                continue
            predicate=stable_predicate(row['type'],row.get('ontology_id'))
            grouped[(row['subject_id'],predicate)].append({
                'record_id':row['id'],'assertion_id':None,'candidate_id':None,
                'predicate':predicate,'type':predicate,'value':row['value'],
                'datatype':row['datatype'],'valid_from':row.get('valid_from'),
                'valid_until':row.get('valid_until'),
                'accepted_support_count':len(support_by_version.get(row['version_id'],())),
                'status':'accepted','conflict_state':'clear','conflict':None,
            })

        for assertion in assertions:
            if assertion['kind']!='attribute' or assertion['status']!='contradicting':
                continue
            payload=assertion.get('payload',{})
            subject_id=payload.get('subject_id') or payload.get('entity_id')
            predicate=(payload.get('type') or payload.get('conflict_predicate') or
                       payload.get('target_type') or payload.get('proposed_type'))
            if subject_id not in entity_ids or not predicate or payload.get('value') is None:
                continue
            predicate=stable_predicate(predicate,payload.get('ontology_id'))
            datatype=payload.get('datatype')
            if not datatype:
                from ..utils.attributes import primitive_datatype
                try:datatype=primitive_datatype(payload['value'])
                except ValueError:continue
            grouped[(subject_id,predicate)].append({
                'record_id':assertion.get('canonical_record_id'),
                'assertion_id':assertion['id'],'candidate_id':assertion['id'],
                'predicate':predicate,'type':predicate,'value':payload['value'],
                'datatype':datatype,'valid_from':payload.get('valid_from'),
                'valid_until':payload.get('valid_until'),'accepted_support_count':0,
                'status':'contradicting','conflict_state':'contradicting',
                'conflict':{
                    'code':payload.get('conflict_code','attribute_conflict'),
                    'reason':assertion.get('decision_reason'),
                },
                'provenance':{key:assertion.get(key) for key in (
                    'document_id','document_version_id','chunk_id','source_hash',
                    'start_char','end_char','quote')},
            })

        result=defaultdict(list)
        for (subject_id,predicate),values in grouped.items():
            conflicts=[value['candidate_id'] for value in values
                       if value['status']=='contradicting']
            for value in values:
                if value['status']=='accepted' and conflicts:
                    value['conflict_state']='contested'
                    value['conflict']={'candidate_ids':conflicts}
            values.sort(key=lambda value:(value['status']!='accepted',
                value.get('record_id') or value.get('candidate_id') or ''))
            result[subject_id].append({
                'predicate':predicate,'type':predicate,
                'label':labels.get(predicate,predicate),'values':values,
                'conflict':bool(conflicts),'conflict_candidate_ids':conflicts,
            })
        for groups in result.values():
            groups.sort(key=lambda group:(group['label'],group['predicate']))
        return result

    def graph(self,p,scope,node_id=None,hops=1,attribute_mode='summary'):
        """返回以 ``node_id`` 为中心的局部子图（省略时返回整个范围）。

        PERF：种子点只在读完整范围之后才收窄。带种子的请求只返回几 KB，
        因此计时行显示扫描占主导、构图开销可忽略——不要把小的响应当作廉价请求。
        下面的跳数循环每跳会重新扫描每条边一次，所以 `hops=3` 在扫描之外还要
        多付出三次全量边的遍历。
        """
        started=perf_counter()
        with timed('图谱探索', seeded=bool(node_id), hops=hops) as record:
            # 图始终读取实体和关系；属性模式开启时在同一次范围读取中追加正式属性。
            # chunk/document 载荷仍不参与图谱响应。
            if attribute_mode not in {'none','summary','expanded'}:
                raise ValueError('不支持的属性展示模式')
            record_scope={key:value for key,value in scope.items()
                          if key not in {'node_id','hops','attribute_mode'}}
            graph_kinds=['entity','relation']
            if attribute_mode!='none':graph_kinds.append('attribute')
            rows=self.service.scoped(p,{**record_scope,'kinds':graph_kinds})
            record['rows']=len(rows)
            nodes={r['id']:r for r in rows if r['kind']=='entity'}
            edges=[r for r in rows if r['kind']=='relation' and r['subject_id'] in nodes and r['object_id'] in nodes]
            attributes=[r for r in rows if r['kind']=='attribute' and r['subject_id'] in nodes]
            record['graph_nodes']=len(nodes)
            record['graph_edges']=len(edges)
            if node_id:
                if node_id not in nodes: raise KeyError(node_id)
                selected={node_id};frontier={node_id}
                for _ in range(max(0,min(5,hops))):
                    following={r[k] for r in edges if r['subject_id'] in frontier or r['object_id'] in frontier for k in ('subject_id','object_id')}
                    frontier=following-selected;selected|=frontier
                nodes={i:r for i,r in nodes.items() if i in selected}
                edges=[r for r in edges if r['subject_id'] in selected and r['object_id'] in selected]
                record['selected']=len(selected)
                record['returned_nodes']=len(nodes)
                record['returned_edges']=len(edges)
            labels=self.service.ontology_labels(p) if attribute_mode!='none' else {}
            grouped=(self._entity_attributes(p,attributes,set(nodes),labels)
                     if attribute_mode!='none' else {})
            presented=[]
            for row in nodes.values():
                item=public(row)
                if attribute_mode!='none':item['attributes']=grouped.get(row['id'],[])
                presented.append(item)
            presented_edges=[public(r) for r in edges]
            if attribute_mode=='expanded' and node_id in nodes:
                used={row['id'] for row in presented}|{edge['id'] for edge in presented_edges}
                for group in grouped.get(node_id,[]):
                    for value in group['values']:
                        identity=[node_id,group['predicate'],value['datatype'],value['value'],
                                  value['status'],value.get('record_id'),value.get('candidate_id')]
                        virtual_id=self._virtual_id('attribute',identity,used)
                        virtual={**value,'id':virtual_id,'kind':'attribute_value',
                                 'type':'KS_ATTRIBUTE_VALUE','virtual':True,
                                 'subject_id':node_id,'predicate_label':group['label'],
                                 'text':f"{group['label']}: {json.dumps(value['value'],ensure_ascii=False)}"}
                        presented.append(virtual)
                        edge_id=self._virtual_id('attribute_edge',[node_id,virtual_id],used)
                        presented_edges.append({
                            'id':edge_id,'kind':'attribute_edge','type':'KS_ATTRIBUTE',
                            'type_label':group['label'],'text':group['label'],
                            'subject_id':node_id,'object_id':virtual_id,'virtual':True,
                            'predicate':group['predicate'],'status':value['status'],
                        })
            # 上报该字段以便浏览器在图谱摘要中区分服务端读取与客户端布局；
            # 上面的日志行仍是权威数字（它还包括序列化）。调用方过去从这里读
            # `timing_ms.total`，而 /subgraph 从不返回它，所以 renderGraph 中的该分支是死代码。
            return {'nodes':presented,'edges':presented_edges,
                    'attribute_mode':attribute_mode,'selected_node_id':node_id,
                    'timing_ms':{'total':round((perf_counter()-started)*1000,1)}}

    def dashboard(self,p,scope):
        # PERF：该端点的全部成本来自下面共享的 `service.scoped` 读取（见其打印的计时行）；
        # 之后的计数开销很小。
        rows=self.service.scoped(p,scope)

        # 计算每个文档的派生知识数量
        derived_counts=Counter(r.get('source_id') for r in rows if r['kind'] in ('entity','relation','chunk'))

        # 只统计成功解析的文档（有派生知识的文档）
        documents=[r for r in rows if r['kind']=='document']
        successful_docs=[d for d in documents if derived_counts.get(d['id'],0)>0]

        # 统计成功解析的实体和关系（排除 chunk）
        entity_rows=[r for r in rows if r['kind']=='entity']
        relation_rows=[r for r in rows if r['kind']=='relation']

        return {
            'counts':{
                'document':len(successful_docs),
                'total_documents':len(documents),
                'chunk':sum(1 for r in rows if r['kind']=='chunk'),
                'entity':len(entity_rows),
                'relation':len(relation_rows)
            },
            'types':dict(Counter(r.get('type','') for r in entity_rows)),
            'predicates':dict(Counter(r.get('type','') for r in relation_rows)),
            'unknown_validity':sum(r.get('valid_from') is None for r in rows)
        }

    def sources(self,p,scope):
        # PERF：与 /dashboard 相同的共享扫描。响应很大是因为每个文档携带完整文本，
        # 但延迟来自扫描本身，而不是载荷。
        rows=self.service.scoped(p,scope)
        return {'documents':[{**public(r),'derived_count':sum(x.get('source_id')==r['id'] for x in rows)}
                             for r in rows if r['kind']=='document']}

    def mindmap(self,p,scope,root_id,depth=3):
        graph=self.graph(p,scope,root_id,depth,attribute_mode='none')
        nodes={r['id']:r for r in graph['nodes']};adj=defaultdict(list)
        for edge in graph['edges']:
            adj[edge['subject_id']].append((edge['object_id'],edge['type']))
            adj[edge['object_id']].append((edge['subject_id'],edge['type']))
        visited=set();count=[0]
        def branch(node,level,path):
            count[0]+=1
            item={'id':node,'text':nodes[node]['text'],'type':nodes[node]['type'],'children':[]}
            if level==0 or node in path or count[0]>=500:return item
            for target,predicate in adj[node][:40]:
                if count[0]>=500:break
                if target not in path|{node}:
                    child=branch(target,level-1,path|{node});child['predicate']=predicate;item['children'].append(child)
            return item
        return {'tree':branch(root_id,min(depth,5),set()),'bounded':True}

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
        request = {**request, '_include_embeddings': requested_mode != 'keyword'}
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
        ontology_labels = self.service.ontology_labels(p)
        filtered_ms = (perf_counter() - start) * 1000
        query = request.get('query', '').strip()
        hits = []
        timings = {}
        retrieval_result = None
        if query:
            retrieval_scope = {key: request.get(key) for key in
                ('filters', 'valid_at', 'known_at', 'include_unknown')}
            retrieval_scope['_candidate_ids'] = [row['id'] for row in candidates]
            retrieval_scope['_lexical_aliases'] = {row['id']: [ontology_labels.get(row.get('type', ''), '')]
                for row in candidates}
            retrieval_result = RetrievalEngine(
                self.repo, self.service.encoder,
                getattr(self.service, 'milvus_store', None)).search(
                p, query, retrieval_mode=requested_mode, scope=retrieval_scope,
                content_channels=['entity', 'chunk', 'relation'], k=request.get('k', 15),
                content_k={'entity': 5, 'chunk': 5, 'relation': 5}, candidates=candidates)
            hits = retrieval_result['hits']
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
        return {'nodes': [present(r) for r in nodes.values()], 'edges': [present(r) for r in edges],
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
