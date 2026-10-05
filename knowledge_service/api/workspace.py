"""链接图谱工作台使用的紧凑、受范围限制的探索契约。"""
from time import perf_counter
import json
import re
from typing import Literal as TypingLiteral
from fastapi import Response
from pydantic import Field
from rdflib import BNode, RDF, RDFS, URIRef, Literal
from rdflib.collection import Collection
from rdflib.namespace import OWL, XSD

from ..models import Scope, Request
from ..services.ontology import (
    Ontology, generated_term_iri, local_name,
    absolute_iri, relation_usage, set_term_constraints, term_kind, term_impact,
)
from ..services.ontology_adapters import (
    add_deprecation_headers,
    compatibility_payload,
    turtle_draft,
    update_turtle_draft,
)
from ..services.ontology_drafts import OntologyDrafts


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
    draft_id: str | None = None
    expected_revision: int | None = Field(default=None, ge=1)


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
    draft_id: str | None = None
    expected_revision: int | None = Field(default=None, ge=1)


class TermRetire(Request):
    expected_ontology_id: str
    confirm_references: bool = False
    draft_id: str | None = None
    expected_revision: int | None = Field(default=None, ge=1)


def install(app, service):
    from ..services.explorer import Explorer

    @app.post('/api/projects/{p}/explore')
    def linked_explore(p: str, request: Explore):
        return Explorer(service).linked_explore(p, request.model_dump())

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

        **维度不一致时会自动删表重建**：Milvus 的向量维度在建表时固定、事后改不了，
        所以"换了模型还保留旧表"这件事物理上做不到。删表是安全动作——Milvus 里全是
        派生数据，本接口马上会全量重编码补回（真值在 PostgreSQL）。放在这个显式动作里
        做，而不是在检索路径上偷偷改表；成功后在返回里带 `recreated` 与 `dim`，
        前端/日志能看出"这一次不只是重灌，而是换了表结构"。
        """
        service.repository.get_project(p)
        def run(progress):
            start = perf_counter()
            # 有意保留默认的 `vectors='list'`：该任务必须看到哪些行已带向量、
            # 由哪个模型产生，因此不能跳过该字段。
            rows = [r for r in service.repository.current_records(p) if r['kind'] in ('entity','relation','chunk')
                    and not r.get('metadata',{}).get('_deleted')]
            if service.milvus_store is not None:
                from ..integrations.milvus_store import VectorDimensionMismatch
                store = service.milvus_store
                recreated = False
                # 先对齐表结构：维度不一致（换过编码器/模型）时删表按当前维度重建，
                # 否则下面每一次 upsert 都会失败，用户只看到"向量维度不对"。
                try:
                    store.ensure_collection()
                except VectorDimensionMismatch as mismatch:
                    progress(f'索引维度不一致（表 {mismatch.collection_dim} 维 → 当前编码器 '
                             f'{mismatch.expected_dim} 维）：删除旧的向量表并按新维度重建', 2)
                    store.recreate_collection()
                    recreated = True
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
                        'recreated': recreated, 'dim': service.milvus_store.dim,
                        'embedding_model': service.encoder.identity,
                        'elapsed_seconds': round(perf_counter()-start, 2)}
            # 无 Milvus 时不再有本地向量后端：`record_versions.vector` 列已随迁移 11
            # 删除，`store_embeddings` 是空实现。此处**明确报错**，而不是返回
            # `indexed=0` 的「假成功」—— 后台任务状态必须如实反映索引未建立，
            # 否则前端会显示「索引已重建」，而检索其实仍然没有语义通道。
            raise RuntimeError(
                '未配置向量后端：向量已迁至 Milvus，本地不再存储向量。'
                '请设置 KG_VECTOR_BACKEND=milvus 并确保 Milvus 可用后重试。')
        return app.state.jobs.submit('semantic_index', run, p)

    @app.post('/api/projects/{p}/ontology/terms', status_code=201)
    def add_term(p: str, request: TermWrite, response: Response):
        with service.lock:
            latest = service.repository.get_ontology(p)
            if latest['id'] != request.expected_ontology_id:
                raise ValueError('版本冲突：请先刷新当前本体再编辑')
            existing = (OntologyDrafts(service.repository).get(p, request.draft_id)
                        if request.draft_id else None)
            ontology = Ontology(existing['turtle'] if existing else latest['turtle'])
            if request.kind not in ('class','relation','attribute'):
                raise ValueError('kind 必须是 class、relation 或 attribute')
            uri=request.uri or generated_term_iri(p,request.label_zh or request.label)
            if not absolute_iri(uri):
                raise ValueError('请提供有效的绝对本体 IRI')
            node = URIRef(uri)
            if node in ontology.classes | ontology.relations | ontology.attributes:
                raise ValueError('术语已存在；请使用 Turtle 编辑器修改')
            ontology.graph.add((node, RDF.type, {'class':OWL.Class,'relation':OWL.ObjectProperty,'attribute':OWL.DatatypeProperty}[request.kind]))
            ontology.graph.add((node, RDFS.label, Literal(request.label)))
            if request.label_zh.strip():
                ontology.graph.add((node, RDFS.label, Literal(request.label_zh.strip(), lang='zh')))
            set_term_constraints(ontology,node,request.kind,request.parent,request.domain,request.range,
                request.domains,request.ranges)
            turtle = ontology.graph.serialize(format='turtle')
            title=f'新增本体术语：{request.label_zh or request.label}'
            result = (update_turtle_draft(
                service.repository, p, request.draft_id,
                request.expected_revision, turtle, reason=title)
                if request.draft_id else turtle_draft(
                    service.repository, p, turtle, source_kind='manual',
                    title=title, actor='api:workspace',
                    expected_ontology_id=request.expected_ontology_id,
                    source_context={'legacy_route': 'POST /ontology/terms'}))
            add_deprecation_headers(response)
            return result

    @app.get('/api/projects/{p}/ontology/term-impact')
    def term_impact_view(p:str,uri:str):
        latest=service.repository.get_ontology(p);ontology=Ontology(latest['turtle'])
        if not absolute_iri(uri) or not term_kind(ontology,URIRef(uri)):raise KeyError(uri)
        return {'ontology_id':latest['id'],'kind':term_kind(ontology,URIRef(uri)),
                **term_impact(service,p,uri,ontology)}

    # 关系类型的两端（domain/range）推断：用户反馈「关系没办法显示」的根因是抽出来的关系
    # 没有声明 rdfs:domain/range，画布就画不出线。这里给出「本体现在声明的」与
    # 「实际入库用法反推的候选」两份读数，写回仍走草案命令通道（add_domain/add_range）。
    #
    # draft_id 必须能传：画布画的是草案时，两端要按**草案**报。否则回填刚写进草案、
    # 这里还按已发布版本回一句"未声明"，界面就成了"改了没反应"——正是本轮要修的病。
    @app.get('/api/projects/{p}/ontology/relation-usage')
    def relation_usage_view(p:str,uri:str,draft_id:str|None=None):
        latest=service.repository.get_ontology(p)
        source,origin=latest,'published'
        if draft_id:
            existing=OntologyDrafts(service.repository).get(p,draft_id)
            if not existing:raise KeyError(draft_id)      # 草案不存在就报错，不静默回落到已发布
            source,origin=existing,'draft'
        ontology=Ontology(source['turtle'])
        if not absolute_iri(uri) or term_kind(ontology,URIRef(uri))!='relation':raise KeyError(uri)
        return {'ontology_id':latest['id'],'kind':'relation','source':origin,
                **relation_usage(service,p,uri,ontology)}

    @app.put('/api/projects/{p}/ontology/term')
    def update_term(p:str,uri:str,request:TermUpdate,response:Response):
        with service.lock:
            latest=service.repository.get_ontology(p)
            if latest['id']!=request.expected_ontology_id:raise ValueError('版本冲突：请先刷新当前本体再编辑')
            existing=(OntologyDrafts(service.repository).get(p,request.draft_id)
                      if request.draft_id else None)
            ontology=Ontology(existing['turtle'] if existing else latest['turtle']);node=URIRef(uri);kind=term_kind(ontology,node)
            if not absolute_iri(uri) or not kind:raise KeyError(uri)
            ontology.graph.remove((node,RDFS.label,None))
            if request.label.strip():ontology.graph.add((node,RDFS.label,Literal(request.label.strip())))
            if request.label_zh.strip():ontology.graph.add((node,RDFS.label,Literal(request.label_zh.strip(),lang='zh')))
            ontology.graph.remove((node,RDFS.comment,None))
            if request.description:ontology.graph.add((node,RDFS.comment,Literal(request.description)))
            set_term_constraints(ontology,node,kind,request.parent,request.domain,request.range,
                request.domains,request.ranges)
            turtle=ontology.graph.serialize(format='turtle')
            title=f'调整本体术语：{request.label_zh or request.label}'
            result=(update_turtle_draft(service.repository,p,request.draft_id,
                    request.expected_revision,turtle,reason=title)
                if request.draft_id else turtle_draft(service.repository,p,turtle,source_kind='manual',
                    title=title,actor='api:workspace',
                    expected_ontology_id=request.expected_ontology_id,
                    source_context={'legacy_route':'PUT /ontology/term','target_iri':uri}))
            add_deprecation_headers(response)
            return result

    @app.post('/api/projects/{p}/ontology/term-retire')
    def retire_term(p:str,uri:str,request:TermRetire,response:Response):
        with service.lock:
            latest=service.repository.get_ontology(p)
            if latest['id']!=request.expected_ontology_id:raise ValueError('版本冲突：请先刷新当前本体再编辑')
            drafts=OntologyDrafts(service.repository,publisher=service.repository)
            existing=drafts.get(p,request.draft_id) if request.draft_id else None
            ontology=Ontology(existing['turtle'] if existing else latest['turtle']);node=URIRef(uri);kind=term_kind(ontology,node)
            if not absolute_iri(uri) or not kind:raise KeyError(uri)
            impact=term_impact(service,p,uri,ontology)
            if (impact['record_count'] or impact['constraint_count'] or impact['pending_review_count']) and not request.confirm_references:
                raise ValueError('术语存在引用；请检查影响并明确确认退休')
            draft=(existing if existing else drafts.create(
                p,latest['id'],'manual',f'停用本体术语：{local_name(uri)}',
                'api:workspace',source_context={'legacy_route':'POST /ontology/term-retire',
                                                'target_iri':uri}))
            revision=(request.expected_revision if existing else draft['revision'])
            if revision is None:raise ValueError('更新现有草案必须提供 expected_revision')
            preview=drafts.command(p,draft['id'],revision,{
                'action':'retire_term','target_iri':uri,'reason':'旧维护接口发起停用'})
            add_deprecation_headers(response)
            return compatibility_payload(preview,retired=uri,impact=impact)
