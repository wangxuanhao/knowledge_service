"""Compact, scoped exploration contracts used by the linked graph workbench."""
from time import perf_counter
import json
import re
from pydantic import Field
from rdflib import RDF, RDFS, URIRef, Literal
from rdflib.namespace import OWL, XSD

from .models import Scope, Request
from .service import public
from .ontology import Ontology, local_name
from .retrieval import rank_candidates


class Explore(Scope):
    query: str = Field(default='', max_length=2000)
    semantic: bool = False
    k: int = Field(default=15, ge=1, le=100)
    hops: int = Field(default=1, ge=0, le=3)
    entity_type: str = ''
    predicate: str = ''
    include_subclasses: bool = True


class TermWrite(Request):
    kind: str
    uri: str = Field(min_length=1, max_length=500)
    label: str = Field(min_length=1, max_length=200)
    label_zh: str = Field(default='', max_length=200)
    parent: str = ''
    domain: str = ''
    range: str = ''
    expected_ontology_id: str


class TermUpdate(Request):
    label: str = Field(min_length=1,max_length=200)
    label_zh: str = Field(default='', max_length=200)
    description: str = Field(default='',max_length=2000)
    parent: str = ''
    domain: str = ''
    range: str = ''
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


def _set_term_constraints(ontology,node,kind,parent='',domain='',range_=''):
    for predicate in (RDFS.subClassOf,RDFS.domain,RDFS.range):
        ontology.graph.remove((node,predicate,None))
    if kind=='class' and parent:
        parent_node=ontology.resolve(parent,ontology.classes)
        if parent_node==node:raise ValueError('Class cannot inherit from itself')
        if node in ontology.parents(parent_node):raise ValueError('Class inheritance would create a cycle')
        ontology.graph.add((node,RDFS.subClassOf,parent_node))
    if kind in ('relation','attribute') and domain:
        ontology.graph.add((node,RDFS.domain,ontology.resolve(domain,ontology.classes)))
    if kind=='relation' and range_:
        ontology.graph.add((node,RDFS.range,ontology.resolve(range_,ontology.classes)))
    if kind=='attribute' and range_:
        datatype=URIRef(range_)
        allowed={XSD.string,XSD.boolean,XSD.integer,XSD.decimal,XSD.double,XSD.date,XSD.dateTime,RDFS.Literal}
        if datatype not in allowed:raise ValueError('Attribute range must be a supported literal datatype')
        ontology.graph.add((node,RDFS.range,datatype))


def explore(service, project_id, request):
    start = perf_counter()
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
    if query:
        if request.get('semantic'):
            hits = rank_candidates(candidates, query, request.get('k', 15), service.encoder, timings)
        else:
            tokens = query.casefold().split()
            def score(row):
                haystack = (row['text']+' '+str(row.get('type',''))+' '+ontology_labels.get(row.get('type',''),'')+
                    ' '+' '.join(map(str,row.get('metadata',{}).get('aliases',[])))).casefold()
                return sum(token in haystack for token in tokens) / len(tokens)
            hits = [{**public(r), 'score': score(r)} for r in candidates if score(r) > 0]
            hits.sort(key=lambda r: (-r['score'], r['kind'] != 'entity', r['id']))
            # Preserve the original entity / source / relation retrieval channels.
            per_channel = max(1, request.get('k', 15)//3)
            hits = [r for kind in ('entity','chunk','relation') for r in [h for h in hits if h['kind']==kind][:per_channel]]
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
            'mode': 'semantic' if request.get('semantic') else 'keyword',
            'timing_ms': {'scope':round(filtered_ms,1), **timings, 'total':round((perf_counter()-start)*1000,1)}}


def install(app, service):
    @app.post('/api/projects/{p}/explore')
    def linked_explore(p: str, request: Explore):
        return explore(service, p, request.model_dump())

    @app.post('/api/projects/{p}/entity-options')
    def options(p: str, request: Scope):
        rows = service.scoped(p, request.model_dump())
        return {'entities':[{'id':r['id'],'text':r['text'],'type':r.get('type','')} for r in rows if r['kind']=='entity'],
                'predicates':sorted({r['type'] for r in rows if r['kind']=='relation'}),
                'dates':sorted({r[k][:10] for r in rows for k in ('valid_from','valid_until') if r.get(k)})}

    @app.post('/api/projects/{p}/metadata/facets')
    def metadata_facets(p: str, request: Scope):
        # Discover this project's fields under the selected times, independent
        # of the current metadata predicate so users can change that predicate.
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
        service.repository.get_project(p)
        def run(progress):
            start = perf_counter()
            rows = [r for r in service.repository.current_records(p) if r['kind'] in ('entity','relation','chunk')
                    and not r.get('metadata',{}).get('_deleted') and (not r.get('embedding') or r.get('embedding_model')!=service.encoder.identity)]
            saved = 0
            for offset in range(0, len(rows), 16):
                progress(f'Embedding {offset}/{len(rows)}; graph browsing remains available', int(90*offset/max(1,len(rows))))
                batch = rows[offset:offset+16]
                vectors = service.encoder.encode([r['text'] for r in batch])
                saved += service.repository.store_embeddings(p, batch, vectors, service.encoder.identity)
            return {'indexed': saved, 'skipped_changed_versions':len(rows)-saved, 'elapsed_seconds':round(perf_counter()-start,2)}
        return app.state.jobs.submit('semantic_index', run, p)

    @app.post('/api/projects/{p}/ontology/terms', status_code=201)
    def add_term(p: str, request: TermWrite):
        with service.lock:
            latest = service.repository.get_ontology(p)
            if latest['id'] != request.expected_ontology_id:
                raise ValueError('Version conflict: reload the current ontology before editing')
            ontology = Ontology(latest['turtle'])
            if request.kind not in ('class','relation','attribute'):
                raise ValueError('kind must be class, relation or attribute')
            if not _absolute_iri(request.uri):
                raise ValueError('Provide a valid absolute ontology IRI')
            node = URIRef(request.uri)
            if node in ontology.classes | ontology.relations | ontology.attributes:
                raise ValueError('Term already exists; use the Turtle editor to revise it')
            ontology.graph.add((node, RDF.type, {'class':OWL.Class,'relation':OWL.ObjectProperty,'attribute':OWL.DatatypeProperty}[request.kind]))
            ontology.graph.add((node, RDFS.label, Literal(request.label)))
            if request.label_zh.strip():
                ontology.graph.add((node, RDFS.label, Literal(request.label_zh.strip(), lang='zh')))
            _set_term_constraints(ontology,node,request.kind,request.parent,request.domain,request.range)
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
            if latest['id']!=request.expected_ontology_id:raise ValueError('Version conflict: reload the current ontology before editing')
            ontology=Ontology(latest['turtle']);node=URIRef(uri);kind=_term_kind(ontology,node)
            if not _absolute_iri(uri) or not kind:raise KeyError(uri)
            ontology.graph.remove((node,RDFS.label,None))
            if request.label.strip():ontology.graph.add((node,RDFS.label,Literal(request.label.strip())))
            if request.label_zh.strip():ontology.graph.add((node,RDFS.label,Literal(request.label_zh.strip(),lang='zh')))
            ontology.graph.remove((node,RDFS.comment,None))
            if request.description:ontology.graph.add((node,RDFS.comment,Literal(request.description)))
            _set_term_constraints(ontology,node,kind,request.parent,request.domain,request.range)
            turtle=ontology.graph.serialize(format='turtle')
            parsed=Ontology(turtle)
            return service.repository.save_ontology(p,turtle,parsed.summary())

    @app.post('/api/projects/{p}/ontology/term-retire')
    def retire_term(p:str,uri:str,request:TermRetire):
        with service.lock:
            latest=service.repository.get_ontology(p)
            if latest['id']!=request.expected_ontology_id:raise ValueError('Version conflict: reload the current ontology before editing')
            ontology=Ontology(latest['turtle']);node=URIRef(uri);kind=_term_kind(ontology,node)
            if not _absolute_iri(uri) or not kind:raise KeyError(uri)
            impact=_term_impact(service,p,uri,ontology)
            if (impact['record_count'] or impact['constraint_count'] or impact['pending_review_count']) and not request.confirm_references:
                raise ValueError('Term has references; review impact and explicitly confirm retirement')
            safe_incoming={RDFS.subClassOf,RDFS.domain,RDFS.range}
            complex_refs=[(s,pred) for s,pred in ontology.graph.subject_predicates(node) if pred not in safe_incoming]
            if complex_refs:raise ValueError('Term is referenced by SHACL or other ontology statements; use the Turtle editor to migrate those references')
            ontology.graph.remove((node,None,None))
            for predicate in safe_incoming:ontology.graph.remove((None,predicate,node))
            turtle=ontology.graph.serialize(format='turtle');parsed=Ontology(turtle)
            saved=service.repository.save_ontology(p,turtle,parsed.summary())
            return {**saved,'retired':uri,'impact':impact}
