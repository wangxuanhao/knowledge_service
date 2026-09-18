"""链接图谱工作台使用的紧凑、受范围限制的探索契约。"""
from time import perf_counter
import json
import re
from typing import Literal as TypingLiteral
from pydantic import Field
from rdflib import BNode, RDF, RDFS, URIRef, Literal
from rdflib.collection import Collection
from rdflib.namespace import OWL, XSD

from .models import Scope, Request
from .service import public
from .ontology import Ontology, generated_term_iri, local_name
from .retrieval import RetrievalEngine, has_vector


class Explore(Scope):
    query: str = Field(default='', max_length=2000)
    semantic: bool = False
    retrieval_mode: TypingLiteral['hybrid','semantic','keyword'] | None = None
    k: int = Field(default=15, ge=1, le=100)
    hops: int = Field(default=1, ge=0, le=3)
    entity_type: str = ''
    predicate: str = ''
    include_subclasses: bool = True


class TermWrite(Request):
    kind: str
    uri: str = Field(default='', max_length=500)
    label: str = Field(min_length=1, max_length=200)
    label_zh: str = Field(default='', max_length=200)
    parent: str = ''
    domain: str = ''
    range: str = ''
    domains: list[str] = Field(default_factory=list,max_length=500)
    ranges: list[str] = Field(default_factory=list,max_length=500)
    expected_ontology_id: str


class TermUpdate(Request):
    label: str = Field(min_length=1,max_length=200)
    label_zh: str = Field(default='', max_length=200)
    description: str = Field(default='',max_length=2000)
    parent: str = ''
    domain: str = ''
    range: str = ''
    domains: list[str] = Field(default_factory=list,max_length=500)
    ranges: list[str] = Field(default_factory=list,max_length=500)
    expected_ontology_id: str


class TermRetire(Request):
    expected_ontology_id: str
    confirm_references: bool = False


def _absolute_iri(value):
    return value.startswith(('urn:','http://','https://')) and not any(c.isspace() or c in '<>"{}|^`\\' for c in value)


def _term_kind(ontology,node):
    return 'class' if node in ontology.classes else 'relation' if node in ontology.relations else 'attribute' if node in ontology.attributes else None


def _term_impact(service,p,uri,ontology):
    node=URIRef(uri);rows=service.repository.current_records(p)
    records=[r for r in rows if r.get('type')==uri or uri in r.get('properties',{})]
    constraints=[{'subject':str(s),'predicate':str(pred)} for s,pred in ontology.graph.subject_predicates(node)]
    pending=[]
    for doc in rows:
        for candidate in doc.get('metadata',{}).get('review_candidates',[]):
            names={candidate.get(k) for k in ('target_type','proposed_type','predicate')}
            if candidate.get('status')=='pending' and ({uri,local_name(uri)} & names):pending.append(candidate['id'])
    return {'record_count':len(records),'record_ids':[r['id'] for r in records[:50]],'record_ids_truncated':len(records)>50,
            'constraint_count':len(constraints),'constraints':constraints[:50],
            'pending_review_count':len(pending),'pending_review_ids':pending[:50]}


def _set_term_constraints(ontology,node,kind,parent='',domain='',range_='',domains=None,ranges=None):
    for predicate in (RDFS.subClassOf,RDFS.domain,RDFS.range):
        for old in list(ontology.graph.objects(node,predicate)):
            head=ontology.graph.value(old,OWL.unionOf)
            if head:
                Collection(ontology.graph,head).clear();ontology.graph.remove((old,None,None))
        ontology.graph.remove((node,predicate,None))
    if kind=='class' and parent:
        parent_node=ontology.resolve(parent,ontology.classes)
        if parent_node==node:raise ValueError('类不能继承自身')
        if node in ontology.parents(parent_node):raise ValueError('类继承会形成循环（cycle）')
        ontology.graph.add((node,RDFS.subClassOf,parent_node))
    domain_values=list(dict.fromkeys(value for value in (domains or ([domain] if domain else [])) if value))
    range_values=list(dict.fromkeys(value for value in (ranges or ([range_] if range_ else [])) if value))
    def add_allowed(predicate,values):
        resolved=[ontology.resolve(value,ontology.classes) for value in values]
        if len(resolved)==1:ontology.graph.add((node,predicate,resolved[0]))
        elif resolved:
            union=BNode();head=BNode();ontology.graph.add((union,OWL.unionOf,head));Collection(ontology.graph,head,resolved)
            ontology.graph.add((node,predicate,union))
    if kind in ('relation','attribute'):
        add_allowed(RDFS.domain,domain_values)
    if kind=='relation':
        add_allowed(RDFS.range,range_values)
    if kind=='attribute' and range_:
        datatype=URIRef(range_)
        allowed={XSD.string,XSD.boolean,XSD.integer,XSD.decimal,XSD.double,XSD.date,XSD.dateTime,RDFS.Literal}
        if datatype not in allowed:raise ValueError('属性的取值范围（range）必须是受支持的字面量数据类型')
        ontology.graph.add((node,RDFS.range,datatype))


def explore(service, project_id, request):
    start = perf_counter()
    requested_mode=request.get('retrieval_mode') or ('semantic' if request.get('semantic') else 'keyword')
    request={**request,'_include_embeddings':requested_mode!='keyword'}
    rows = service.scoped(project_id, request)
    types = {request['entity_type']} if request.get('entity_type') else set()
    if types and request.get('include_subclasses', True):
        for version in service.repository.list_ontologies(project_id):
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
    ontology_labels=service.ontology_labels(project_id)
    filtered_ms = (perf_counter()-start)*1000
    query = request.get('query', '').strip()
    hits = []
    timings = {}
    retrieval_result=None
    if query:
        retrieval_scope={key:request.get(key) for key in
            ('filters','valid_at','known_at','include_unknown')}
        retrieval_scope['_candidate_ids']=[row['id'] for row in candidates]
        retrieval_scope['_lexical_aliases']={row['id']:[ontology_labels.get(row.get('type',''),'')]
            for row in candidates}
        retrieval_result=RetrievalEngine(service.repository,service.encoder,getattr(service,'milvus_store',None)).search(
            project_id,query,retrieval_mode=requested_mode,scope=retrieval_scope,
            content_channels=['entity','chunk','relation'],k=request.get('k',15),
            content_k={'entity':5,'chunk':5,'relation':5},candidates=candidates)
        hits=retrieval_result['hits']
        selected = {r['id'] for r in hits if r['kind'] == 'entity'}
        selected |= {r[key] for r in hits if r['kind']=='relation' for key in ('subject_id','object_id')}
        for hit in hits:
            if hit['kind'] == 'chunk':
                selected |= {i for i, node in nodes.items() if node.get('metadata',{}).get('chunk_id') == hit['id'] or (node['text'] and node['text'] in hit['text'])}
        seeds = selected.copy()
        for _ in range(request.get('hops', 1)):
            selected |= {r[k] for r in edges if r['subject_id'] in selected or r['object_id'] in selected for k in ('subject_id','object_id')}
        nodes = {i: r for i, r in nodes.items() if i in selected}
        edges = [r for r in edges if r['subject_id'] in nodes and r['object_id'] in nodes]
    else:
        seeds = set()
    present=lambda row:{**public(row),'type_label':ontology_labels.get(row.get('type',''),row.get('type',''))}
    return {'nodes':[present(r) for r in nodes.values()], 'edges':[present(r) for r in edges],
            'hits': [{**r,'type_label':ontology_labels.get(r.get('type',''),r.get('type',''))} for r in hits],
            'type_labels':{key:value for key,value in ontology_labels.items() if key.startswith(('urn:','http://','https://'))},
            'matched_ids': sorted(seeds), 'candidate_count': len(candidates),
            'mode': retrieval_result['active_mode'] if retrieval_result else requested_mode,
            'requested_mode':requested_mode,
            'degraded':retrieval_result['degraded'] if retrieval_result else False,
            'backends':retrieval_result['backends'] if retrieval_result else {},
            'timing_ms': {'scope':round(filtered_ms,1), **timings, 'total':round((perf_counter()-start)*1000,1)}}


def install(app, service):
    @app.post('/api/projects/{p}/explore')
    def linked_explore(p: str, request: Explore):
        return explore(service, p, request.model_dump())

    @app.post('/api/projects/{p}/entity-options')
    def options(p: str, request: Scope):
        # PERF：下拉只需要实体/关系，下推 kinds 跳过 document/chunk 的 payload 解码
        rows = service.scoped(p, {**request.model_dump(), 'kinds': ['entity', 'relation']})
        return {'entities':[{'id':r['id'],'text':r['text'],'type':r.get('type',''),
                'source':(r.get('metadata') or {}).get('source_file') or (r.get('metadata') or {}).get('title') or ''}
                for r in rows if r['kind']=='entity'],
                'predicates':sorted({r['type'] for r in rows if r['kind']=='relation'}),
                'dates':sorted({r[k][:10] for r in rows for k in ('valid_from','valid_until') if r.get(k)})}

    @app.get('/api/projects/{p}/timeline')
    def timeline(p: str, limit: int = 100):
        return service.repository.timeline_events(p, limit)

    @app.post('/api/projects/{p}/metadata/facets')
    def metadata_facets(p: str, request: Scope):
        # 在所选时间下发现该项目的字段，与当前 metadata 谓词无关，
        # 以便用户能更改该谓词。
        # PERF：又是一次完整的 `service.scoped` 读取（外加下面的深层 metadata 遍历），
        # 在每次项目/范围切换时与 /entity-options 相邻触发。
        rows = service.scoped(p, {**request.model_dump(), 'filters':None})
        fields = {}
        truncated = False
        def walk(value, path, found, depth=0):
            nonlocal truncated
            if depth>5:
                truncated=True
                return
            if isinstance(value, dict):
                for key, child in value.items():
                    if key.startswith('_') or not re.fullmatch(r'[^\W\d][\w-]*',key):continue
                    walk(child, path+[key], found, depth+1)
                return
            name='metadata.'+'.'.join(path)
            if len(name)>256 or (name not in fields and len(fields)>=200):
                truncated=True
                return
            field=fields.setdefault(name, {'field':name,'records':0,'types':set(),'values':{},'truncated':False})
            if name not in found:field['records']+=1;found.add(name)
            values=value if isinstance(value,list) else [value]
            for item in values:
                if isinstance(item,(dict,list)):continue
                typ='null' if item is None else 'boolean' if isinstance(item,bool) else 'number' if isinstance(item,(int,float)) else 'string'
                field['types'].add('array' if isinstance(value,list) else typ)
                encoded=json.dumps(item,ensure_ascii=False,allow_nan=False)
                key=('contains' if isinstance(value,list) else 'eq',encoded)
                if len(encoded)>500 or (key not in field['values'] and len(field['values'])>=50):
                    field['truncated']=True
                    continue
                if key not in field['values']:field['values'][key]={'value':item,'op':key[0]}
        for row in rows:
            walk(row.get('metadata',{}),[],set())
        return {'project_id':p,'records':len(rows),'truncated':truncated,
                'fields':[{**f,'types':sorted(f['types']),'values':list(f['values'].values())} for _,f in sorted(fields.items())]}

    @app.post('/api/projects/{p}/indexes/rebuild', status_code=202)
    def reindex(p: str):
        """重建语义索引（向量 + BM25）。

        「换 embedding 模型后必须调用本接口重建」——Milvus 里的向量是旧模型产的，
        与新模型的 query 向量维度/空间不同，不重建会导致检索维度报错（维度变了）
        或静默返回错误相似度（维度相同但模型不同）。embedding_model 字段只记录向量
        身份（元数据），检索侧不做强校验，靠本接口的「清空分区 + 全量重编码」保证一致。
        """
        service.repository.get_project(p)
        def run(progress):
            start = perf_counter()
            # 有意保留默认的 `vectors='list'`：该任务必须看到哪些行已带向量、
            # 由哪个模型产生，因此不能跳过该字段。
            rows = [r for r in service.repository.current_records(p) if r['kind'] in ('entity','relation','chunk')
                    and not r.get('metadata',{}).get('_deleted')]
            if service.milvus_store is not None:
                # Milvus 重建：清空该 project 分区后批量 upsert 全部当前活跃记录
                # （不按 has_vector 筛选——Milvus 分区是重灌语义，全量覆盖）。
                encoded = []
                for offset in range(0, len(rows), 16):
                    progress(f'向量编码 {offset}/{len(rows)}；图谱浏览仍可用', int(90*offset/max(1,len(rows))))
                    batch = rows[offset:offset+16]
                    vectors = service.encoder.encode([r['text'] for r in batch])
                    for r, v in zip(batch, vectors):
                        r['embedding'] = v
                        r['embedding_model'] = service.encoder.identity
                        r['project_id'] = p
                        encoded.append(r)
                saved = service.milvus_store.rebuild_project(p, encoded)
                return {'indexed': saved, 'backend': 'milvus', 'total': len(rows),
                        'elapsed_seconds': round(perf_counter()-start, 2)}
            # SQLite 本地：只补无向量或模型不匹配的当前版本
            stale = [r for r in rows if not has_vector(r) or r.get('embedding_model') != service.encoder.identity]
            saved = 0
            for offset in range(0, len(stale), 16):
                progress(f'向量编码 {offset}/{len(stale)}；图谱浏览仍可用', int(90*offset/max(1,len(rows))))
                batch = stale[offset:offset+16]
                vectors = service.encoder.encode([r['text'] for r in batch])
                saved += service.repository.store_embeddings(p, batch, vectors, service.encoder.identity)
            return {'indexed': saved, 'backend': 'sqlite', 'skipped_changed_versions': len(stale)-saved,
                    'elapsed_seconds': round(perf_counter()-start, 2)}
        return app.state.jobs.submit('semantic_index', run, p)

    @app.post('/api/projects/{p}/ontology/terms', status_code=201)
    def add_term(p: str, request: TermWrite):
        with service.lock:
            latest = service.repository.get_ontology(p)
            if latest['id'] != request.expected_ontology_id:
                raise ValueError('版本冲突：请先刷新当前本体再编辑')
            ontology = Ontology(latest['turtle'])
            if request.kind not in ('class','relation','attribute'):
                raise ValueError('kind 必须是 class、relation 或 attribute')
            uri=request.uri or generated_term_iri(p,request.label_zh or request.label)
            if not _absolute_iri(uri):
                raise ValueError('请提供有效的绝对本体 IRI')
            node = URIRef(uri)
            if node in ontology.classes | ontology.relations | ontology.attributes:
                raise ValueError('术语已存在；请使用 Turtle 编辑器修改')
            ontology.graph.add((node, RDF.type, {'class':OWL.Class,'relation':OWL.ObjectProperty,'attribute':OWL.DatatypeProperty}[request.kind]))
            ontology.graph.add((node, RDFS.label, Literal(request.label)))
            if request.label_zh.strip():
                ontology.graph.add((node, RDFS.label, Literal(request.label_zh.strip(), lang='zh')))
            _set_term_constraints(ontology,node,request.kind,request.parent,request.domain,request.range,
                request.domains,request.ranges)
            turtle = ontology.graph.serialize(format='turtle')
            return service.repository.save_ontology(p, turtle, Ontology(turtle).summary())

    @app.get('/api/projects/{p}/ontology/term-impact')
    def term_impact(p:str,uri:str):
        latest=service.repository.get_ontology(p);ontology=Ontology(latest['turtle'])
        if not _absolute_iri(uri) or not _term_kind(ontology,URIRef(uri)):raise KeyError(uri)
        return {'ontology_id':latest['id'],'kind':_term_kind(ontology,URIRef(uri)),
                **_term_impact(service,p,uri,ontology)}

    @app.put('/api/projects/{p}/ontology/term')
    def update_term(p:str,uri:str,request:TermUpdate):
        with service.lock:
            latest=service.repository.get_ontology(p)
            if latest['id']!=request.expected_ontology_id:raise ValueError('版本冲突：请先刷新当前本体再编辑')
            ontology=Ontology(latest['turtle']);node=URIRef(uri);kind=_term_kind(ontology,node)
            if not _absolute_iri(uri) or not kind:raise KeyError(uri)
            ontology.graph.remove((node,RDFS.label,None))
            if request.label.strip():ontology.graph.add((node,RDFS.label,Literal(request.label.strip())))
            if request.label_zh.strip():ontology.graph.add((node,RDFS.label,Literal(request.label_zh.strip(),lang='zh')))
            ontology.graph.remove((node,RDFS.comment,None))
            if request.description:ontology.graph.add((node,RDFS.comment,Literal(request.description)))
            _set_term_constraints(ontology,node,kind,request.parent,request.domain,request.range,
                request.domains,request.ranges)
            turtle=ontology.graph.serialize(format='turtle')
            parsed=Ontology(turtle)
            return service.repository.save_ontology(p,turtle,parsed.summary())

    @app.post('/api/projects/{p}/ontology/term-retire')
    def retire_term(p:str,uri:str,request:TermRetire):
        with service.lock:
            latest=service.repository.get_ontology(p)
            if latest['id']!=request.expected_ontology_id:raise ValueError('版本冲突：请先刷新当前本体再编辑')
            ontology=Ontology(latest['turtle']);node=URIRef(uri);kind=_term_kind(ontology,node)
            if not _absolute_iri(uri) or not kind:raise KeyError(uri)
            impact=_term_impact(service,p,uri,ontology)
            if (impact['record_count'] or impact['constraint_count'] or impact['pending_review_count']) and not request.confirm_references:
                raise ValueError('术语存在引用；请检查影响并明确确认退休')
            safe_incoming={RDFS.subClassOf,RDFS.domain,RDFS.range}
            complex_refs=[(s,pred) for s,pred in ontology.graph.subject_predicates(node) if pred not in safe_incoming]
            if complex_refs:raise ValueError('术语被 SHACL 或其他本体语句引用；请使用 Turtle 编辑器迁移这些引用')
            ontology.graph.remove((node,None,None))
            for predicate in safe_incoming:ontology.graph.remove((None,predicate,node))
            turtle=ontology.graph.serialize(format='turtle');parsed=Ontology(turtle)
            saved=service.repository.save_ontology(p,turtle,parsed.summary())
            return {**saved,'retired':uri,'impact':impact}
