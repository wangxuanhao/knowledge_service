"""链接图谱工作台使用的紧凑、受范围限制的探索契约。"""
import json
import re
from typing import Literal as TypingLiteral
from pydantic import Field
from rdflib import URIRef

from ..models import Scope
from ..services.ontology import Ontology, absolute_iri, relation_usage, term_kind, term_impact


class Explore(Scope):
    query: str = Field(default='', max_length=2000)
    semantic: bool = False
    retrieval_mode: TypingLiteral['hybrid','semantic','keyword'] | None = None
    k: int = Field(default=15, ge=1, le=100)
    hops: int = Field(default=1, ge=0, le=3)
    entity_type: str = ''
    predicate: str = ''
    include_subclasses: bool = True


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
        return service.data.timeline_events(p, limit)

    @app.post('/api/projects/{p}/metadata/facets')
    def metadata_facets(p: str, request: Scope):
        # 在所选时间下发现该项目的字段，与当前 metadata 谓词无关，
        # 以便用户能更改该谓词。
        from ..repository.page_reads import PageReads
        rows = PageReads(service.repository).metadata_groups(p, request.model_dump())
        fields = {}
        truncated = False
        records = 0
        def walk(value, path, found, weight, depth=0):
            nonlocal truncated
            if depth>5:
                truncated=True
                return
            if isinstance(value, dict):
                for key, child in value.items():
                    if key.startswith('_') or not re.fullmatch(r'[^\W\d][\w-]*',key):continue
                    walk(child, path+[key], found, weight, depth+1)
                return
            name='metadata.'+'.'.join(path)
            if len(name)>256 or (name not in fields and len(fields)>=200):
                truncated=True
                return
            field=fields.setdefault(name, {'field':name,'records':0,'types':set(),'values':{},'truncated':False})
            if name not in found:field['records']+=weight;found.add(name)
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
        for metadata, weight in rows:
            records += weight
            walk(metadata,[],set(),weight)
        return {'project_id':p,'records':records,'truncated':truncated,
                'fields':[{**f,'types':sorted(f['types']),'values':list(f['values'].values())} for _,f in sorted(fields.items())]}

    @app.get('/api/projects/{p}/ontology/term-impact')
    def term_impact_view(p:str,uri:str):
        latest=service.data.get_ontology(p);ontology=Ontology(latest['turtle'])
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
        latest=service.data.get_ontology(p)
        source,origin=latest,'published'
        if draft_id:
            existing=service.ontology_drafts().get(p,draft_id)
            if not existing:raise KeyError(draft_id)      # 草案不存在就报错，不静默回落到已发布
            source,origin=existing,'draft'
        ontology=Ontology(source['turtle'])
        if not absolute_iri(uri) or term_kind(ontology,URIRef(uri))!='relation':raise KeyError(uri)
        return {'ontology_id':latest['id'],'kind':'relation','source':origin,
                **relation_usage(service,p,uri,ontology)}
