"""Open-schema candidate aggregation, Semantica induction and versioned publication."""
from collections import Counter,defaultdict
import hashlib
import re
from urllib.parse import quote,unquote
from uuid import uuid4

from fastapi import APIRouter
from pydantic import Field
from rdflib import Graph,Literal,Namespace,RDF,RDFS,URIRef
from rdflib.namespace import OWL,XSD

from .models import Request
from .ontology import Ontology
from .time import utc_now


def _literal_language(value):
    return 'zh' if any('\u4e00' <= char <= '\u9fff' for char in str(value)) else 'en'


def _definition(kind,label):
    names={'class':'实体类型','relation':'关系类型','attribute':'属性类型'}
    return f'由开放知识候选归纳的“{label}”{names[kind]}；发布前需人工审核其边界与示例。'


class DraftRequest(Request):
    name:str=Field(default='发现本体',min_length=1,max_length=200)


def _candidates(repository,project_id):
    result=[]
    for document in repository.current_records(project_id):
        if document['kind']!='document' or document.get('metadata',{}).get('_deleted'):continue
        for item in document.get('metadata',{}).get('discovery_candidates',[]):
            result.append({**item,'document_id':document['id'],'document_version_id':document['version_id'],
                'document_title':document['metadata'].get('title',document['id']),
                'valid_from':document.get('valid_from'),'valid_until':document.get('valid_until')})
    return result


def _summary(candidates):
    entity_groups=defaultdict(list)
    for item in candidates:
        if item['kind']=='entity':entity_groups[item['proposed_type']].append(item.get('text',''))
    entities=Counter({key:len(values) for key,values in entity_groups.items()})
    attributes=Counter(x['proposed_type'] for x in candidates if x['kind']=='attribute')
    relations=defaultdict(list)
    for item in candidates:
        if item['kind']=='relation':relations[item['proposed_type']].append(f"{item.get('subject','?')} → {item.get('object','?')}")
    return {'candidate_count':len(candidates),
        'entity_count':sum(entities.values()),'relation_count':sum(len(v) for v in relations.values()),
        'attribute_count':sum(attributes.values()),
        'entity_types':[{'name':name,'count':count,'examples':entity_groups[name][:5]}
            for name,count in sorted(entities.items(),key=lambda x:(-x[1],x[0]))],
        'attribute_types':[{'name':name,'count':count} for name,count in sorted(attributes.items(),key=lambda x:(-x[1],x[0]))],
        'relation_types':[{'name':name,'count':len(values),'examples':values[:3]} for name,values in sorted(relations.items())]}


def _candidate_lifecycle(repository,project_id,candidates,drafts):
    candidate_ids={item['id'] for item in candidates}
    materialized_ids=set()
    for record in repository.current_records(project_id):
        if record['kind'] not in ('entity','relation') or record.get('metadata',{}).get('_deleted'):continue
        metadata=record.get('metadata',{})
        if metadata.get('discovery_candidate_id'):
            materialized_ids.add(metadata['discovery_candidate_id'])
        materialized_ids.update(metadata.get('discovery_candidate_ids') or [])
    published_ids=set();included_ids=set()
    for draft in drafts:
        if draft.get('status')=='published':
            published_ids.update(draft.get('candidate_ids') or [])
        elif draft.get('status')=='draft':
            included_ids.update(draft.get('candidate_ids') or [])
    materialized_ids &= candidate_ids
    published_ids = (published_ids & candidate_ids) - materialized_ids
    included_ids = (included_ids & candidate_ids) - published_ids - materialized_ids
    pending_ids = candidate_ids - materialized_ids - published_ids - included_ids
    states={candidate_id:'pending' for candidate_id in pending_ids}
    states.update({candidate_id:'included_in_draft' for candidate_id in included_ids})
    states.update({candidate_id:'approved' for candidate_id in published_ids})
    states.update({candidate_id:'materialized' for candidate_id in materialized_ids})
    return states,{'pending':len(pending_ids),'included_in_draft':len(included_ids),
        'approved':len(published_ids),'materialized':len(materialized_ids)}


def _candidate_mindmap(candidates,states,limit=500):
    """Aggregate open occurrences for visualization without creating canonical knowledge."""
    entity_groups={};candidate_to_node={};text_to_nodes=defaultdict(list)
    for item in candidates:
        if item['kind']!='entity':continue
        text=str(item.get('text','')).strip();kind=str(item.get('proposed_type','')).strip()
        key=(kind.casefold(),text.casefold())
        node=entity_groups.setdefault(key,{'id':'candidate:'+hashlib.sha256(repr(key).encode()).hexdigest()[:24],
            'text':text or '未命名候选','type':kind or '未分类','candidate_ids':[],'document_ids':[],
            'sources':[],'attributes':[],'status_counts':Counter(),'occurrence_count':0})
        node['candidate_ids'].append(item['id']);node['occurrence_count']+=1
        node['status_counts'][states.get(item['id'],'pending')]+=1
        if item.get('document_id') not in node['document_ids']:node['document_ids'].append(item.get('document_id'))
        if len(node['sources'])<10:
            node['sources'].append({'document_id':item.get('document_id'),'document_title':item.get('document_title'),
                'chunk_id':item.get('chunk_id'),'start_char':item.get('start_char'),'end_char':item.get('end_char'),
                'evidence':str(item.get('evidence',''))[:500],'confidence':item.get('confidence')})
        candidate_to_node[item['id']]=node['id'];text_to_nodes[text.casefold()].append(node['id'])
    ordered=sorted(entity_groups.values(),key=lambda item:(-item['occurrence_count'],item['type'],item['text']))
    selected=ordered[:max(1,min(int(limit),2000))];selected_ids={item['id'] for item in selected}
    node_by_id={item['id']:item for item in selected}
    for item in candidates:
        if item['kind']!='attribute':continue
        node_id=candidate_to_node.get(item.get('entity_id'))
        if node_id in node_by_id and len(node_by_id[node_id]['attributes'])<30:
            node_by_id[node_id]['attributes'].append({'name':item.get('proposed_type'),'value':item.get('value'),
                'candidate_id':item['id'],'status':states.get(item['id'],'pending')})
    relation_groups={};unresolved=0;hidden_relations=0
    for item in candidates:
        if item['kind']!='relation':continue
        subject=candidate_to_node.get(item.get('subject_id'))
        obj=candidate_to_node.get(item.get('object_id'))
        if not subject:
            matches=text_to_nodes.get(str(item.get('subject','')).strip().casefold(),[]);subject=matches[0] if len(set(matches))==1 else None
        if not obj:
            matches=text_to_nodes.get(str(item.get('object','')).strip().casefold(),[]);obj=matches[0] if len(set(matches))==1 else None
        if not subject or not obj:
            unresolved+=1;continue
        if subject not in selected_ids or obj not in selected_ids:
            hidden_relations+=1;continue
        kind=str(item.get('proposed_type','')).strip() or '未分类关系';key=(subject,kind.casefold(),obj)
        edge=relation_groups.setdefault(key,{'id':'candidate-edge:'+hashlib.sha256(repr(key).encode()).hexdigest()[:24],
            'type':kind,'subject_id':subject,'object_id':obj,'candidate_ids':[],'sources':[],
            'status_counts':Counter(),'occurrence_count':0})
        edge['candidate_ids'].append(item['id']);edge['occurrence_count']+=1
        edge['status_counts'][states.get(item['id'],'pending')]+=1
        if len(edge['sources'])<10:
            edge['sources'].append({'document_id':item.get('document_id'),'document_title':item.get('document_title'),
                'chunk_id':item.get('chunk_id'),'start_char':item.get('start_char'),'end_char':item.get('end_char'),
                'evidence':str(item.get('evidence',''))[:500],'confidence':item.get('confidence')})
    priority=('materialized','approved','included_in_draft','pending')
    for item in [*selected,*relation_groups.values()]:
        item['status']=next((status for status in priority if item['status_counts'].get(status)), 'pending')
        item['status_counts']=dict(item['status_counts'])
    return {'nodes':selected,'edges':list(relation_groups.values()),
        'summary':{'candidate_count':len(candidates),'entity_occurrences':sum(x['occurrence_count'] for x in ordered),
            'entity_clusters':len(ordered),'visible_entity_clusters':len(selected),
            'relation_clusters':len(relation_groups),'unresolved_relations':unresolved,
            'hidden_relations':hidden_relations},
        'truncated':len(selected)<len(ordered)}


def _quality_warnings(candidates):
    """Flag weak open vocabularies before they become a published contract."""
    entities=[x for x in candidates if x['kind']=='entity']
    relations=[x for x in candidates if x['kind']=='relation']
    generic={'person','org','gpe','date','event','product','concept','unknown','entity'}
    generic_count=sum(str(x.get('proposed_type','')).strip().lower() in generic for x in entities)
    warnings=[]
    if entities and generic_count/len(entities)>=.35:
        warnings.append({'code':'generic_entity_vocabulary','severity':'warning',
            'message':f'{generic_count}/{len(entities)} 个实体仍使用通用类型，建议重新抽取后再发布'})
    chinese_source=any(any('\u4e00'<=c<='\u9fff' for c in str(x.get('text',''))) for x in entities)
    english_relations=sum(str(x.get('proposed_type','')).replace('_','').isalnum() and
        str(x.get('proposed_type','')).replace('_','').isascii() for x in relations)
    if chinese_source and relations and english_relations/len(relations)>=.5:
        warnings.append({'code':'relation_language_mismatch','severity':'warning',
            'message':f'{english_relations}/{len(relations)} 条关系仍为英文技术名，建议核对业务中文谓词'})
    return warnings


def _iri(base,name):
    return URIRef(base+quote(str(name).strip().replace(' ','_'),safe='_-~.'))


def _machine_name(prefix,value):
    """Create a stable ASCII name for Semantica while labels keep source language."""
    value=str(value).strip()
    if re.fullmatch(r'[A-Za-z_][A-Za-z0-9_-]*',value):return value
    return prefix+'_'+hashlib.sha256(value.encode('utf-8')).hexdigest()[:12]


def _local_name(value):
    text=unquote(str(value))
    return text.rsplit('#',1)[-1].rsplit('/',1)[-1].rsplit(':',1)[-1]


def _term_catalog(graph):
    specs={'classes':OWL.Class,'relations':OWL.ObjectProperty,'attributes':OWL.DatatypeProperty}
    result={}
    for kind,rdf_type in specs.items():
        terms={}
        for subject in graph.subjects(RDF.type,rdf_type):
            labels=[str(value) for value in graph.objects(subject,RDFS.label)]
            terms[str(subject)]=labels[0] if labels else _local_name(subject)
        result[kind]=terms
    return result


def _term_lookup(catalog,kind):
    lookup={}
    for iri,label in catalog[kind].items():
        for value in (iri,label,_local_name(iri)):
            if value:lookup[str(value).strip().casefold()]=URIRef(iri)
    return lookup


def _ontology_diff(before_turtle,after_turtle):
    before=Graph()
    if before_turtle:before.parse(data=before_turtle,format='turtle')
    after=Graph();after.parse(data=after_turtle,format='turtle')
    previous=_term_catalog(before);current=_term_catalog(after);result={}
    for kind in ('classes','relations','attributes'):
        old,new=previous[kind],current[kind]
        item=lambda iri:{'id':iri,'label':new.get(iri,old.get(iri,_local_name(iri)))}
        result[kind]={
            'added':[item(iri) for iri in sorted(set(new)-set(old))],
            'retained':[item(iri) for iri in sorted(set(new)&set(old))],
            'removed':[item(iri) for iri in sorted(set(old)-set(new))],
        }
    return result


def _induce(project_id,name,candidates,baseline_turtle=None):
    from semantica.ontology import OntologyGenerator
    by_id={x['id']:x for x in candidates if x['kind']=='entity'}
    entity_machine={source:_machine_name('EntityType',source) for source in
        {x['proposed_type'] for x in candidates if x['kind']=='entity'}}
    relation_machine={source:_machine_name('RelationType',source) for source in
        {x['proposed_type'] for x in candidates if x['kind']=='relation'}}
    attribute_machine={source:_machine_name('AttributeType',source) for source in
        {x['proposed_type'] for x in candidates if x['kind']=='attribute'}}
    reverse_entity={value:key for key,value in entity_machine.items()}
    reverse_relation={value:key for key,value in relation_machine.items()}
    reverse_attribute={value:key for key,value in attribute_machine.items()}
    attributes=defaultdict(dict)
    for item in candidates:
        if item['kind']=='attribute':attributes[item.get('entity_id')][attribute_machine[item['proposed_type']]]=item.get('value')
    entities=[{'type':entity_machine[x['proposed_type']],'entity_type':entity_machine[x['proposed_type']],
        'name':x['text'],'text':x['text'],
        'confidence':x.get('confidence',1),'properties':attributes.get(x['id'],{})}
        for x in candidates if x['kind']=='entity']
    relationships=[]
    for item in candidates:
        if item['kind']!='relation':continue
        subject=by_id.get(item.get('subject_id'),{})
        obj=by_id.get(item.get('object_id'),{})
        relationships.append({'type':relation_machine[item['proposed_type']],
            'relationship_type':relation_machine[item['proposed_type']],
            'source':item.get('subject') or subject.get('text'),'target':item.get('object') or obj.get('text'),
            'source_type':entity_machine.get(subject.get('proposed_type'),subject.get('proposed_type') or item.get('subject_type')),
            'target_type':entity_machine.get(obj.get('proposed_type'),obj.get('proposed_type') or item.get('object_type'))})
    base=f'urn:knowledge:ontology:{project_id}:'
    inferred=OntologyGenerator(base_uri=base,min_occurrences=1).generate_ontology(
        {'entities':entities,'relationships':relationships},name=name,build_hierarchy=True)
    graph=Graph()
    if baseline_turtle:graph.parse(data=baseline_turtle,format='turtle')
    ns=Namespace(base);graph.bind('disc',ns);graph.bind('owl',OWL);graph.bind('rdfs',RDFS)
    catalog=_term_catalog(graph)
    class_map={};class_lookup=_term_lookup(catalog,'classes')
    for item in inferred.get('classes',[]):
        machine_source=str(item.get('metadata',{}).get('inferred_from') or item.get('name'))
        source=reverse_entity.get(machine_source,machine_source)
        uri=class_lookup.get(str(source).strip().casefold()) or _iri(base,source)
        class_map[str(source)]=str(uri)
        for key in (source,machine_source,item.get('name'),item.get('uri')):
            if key:class_lookup[str(key).lower()]=uri
        graph.add((uri,RDF.type,OWL.Class));graph.add((uri,RDFS.label,Literal(str(source),lang=_literal_language(source))))
        graph.add((uri,RDFS.comment,Literal(_definition('class',source),lang='zh')))
    for source in sorted({x['proposed_type'] for x in candidates if x['kind']=='entity'}):
        if source not in class_map:
            uri=class_lookup.get(str(source).strip().casefold()) or _iri(base,source)
            class_map[source]=str(uri);class_lookup[source.lower()]=uri
            graph.add((uri,RDF.type,OWL.Class));graph.add((uri,RDFS.label,Literal(source,lang=_literal_language(source))))
            graph.add((uri,RDFS.comment,Literal(_definition('class',source),lang='zh')))
    relation_map={};attribute_map={}
    relation_lookup=_term_lookup(catalog,'relations');attribute_lookup=_term_lookup(catalog,'attributes')
    for item in inferred.get('properties',[]):
        machine_source=str(item.get('metadata',{}).get('inferred_from') or item.get('name'))
        is_object=item.get('type')=='object'
        source=(reverse_relation if is_object else reverse_attribute).get(machine_source,machine_source)
        lookup=relation_lookup if is_object else attribute_lookup
        uri=lookup.get(str(source).strip().casefold()) or _iri(base,source)
        graph.add((uri,RDF.type,OWL.ObjectProperty if is_object else OWL.DatatypeProperty))
        graph.add((uri,RDFS.label,Literal(source,lang=_literal_language(source))))
        graph.add((uri,RDFS.comment,Literal(_definition('relation' if is_object else 'attribute',source),lang='zh')))
        target=relation_map if is_object else attribute_map;target[source]=str(uri)
        domains=item.get('domain',[]) if isinstance(item.get('domain',[]),list) else [item.get('domain')]
        ranges=item.get('range',[]) if isinstance(item.get('range',[]),list) else [item.get('range')]
        for value in domains:
            resolved=class_lookup.get(str(value).lower()) if value else None
            if resolved:graph.add((uri,RDFS.domain,resolved))
        for value in ranges:
            if not value:continue
            resolved=class_lookup.get(str(value).lower())
            if resolved:graph.add((uri,RDFS.range,resolved))
            elif not is_object and str(value).startswith('xsd:'):graph.add((uri,RDFS.range,getattr(XSD,str(value).split(':',1)[1])))
    for source in sorted({x['proposed_type'] for x in candidates if x['kind']=='relation'}):
        if source not in relation_map:
            uri=relation_lookup.get(str(source).strip().casefold()) or _iri(base,source);relation_map[source]=str(uri)
            graph.add((uri,RDF.type,OWL.ObjectProperty));graph.add((uri,RDFS.label,Literal(source,lang=_literal_language(source))))
            graph.add((uri,RDFS.comment,Literal(_definition('relation',source),lang='zh')))
    for source in sorted({x['proposed_type'] for x in candidates if x['kind']=='attribute'}):
        if source not in attribute_map:
            uri=attribute_lookup.get(str(source).strip().casefold()) or _iri(base,source);attribute_map[source]=str(uri)
            graph.add((uri,RDF.type,OWL.DatatypeProperty));graph.add((uri,RDFS.label,Literal(source,lang=_literal_language(source))))
            graph.add((uri,RDFS.comment,Literal(_definition('attribute',source),lang='zh')))
    turtle=graph.serialize(format='turtle')
    Ontology(turtle)
    return turtle,{'entity_types':class_map,'relation_types':relation_map,'attributes':attribute_map},inferred


def install(app,service):
    router=APIRouter(prefix='/api/projects/{p}/ontology-discovery')

    @router.get('')
    def overview(p:str):
        service.repository.get_project(p)
        candidates=_candidates(service.repository,p)
        drafts=service.repository.list_artifacts('ontology_discovery_draft',p)
        ontologies=service.repository.list_ontologies(p)
        states,status_counts=_candidate_lifecycle(service.repository,p,candidates,drafts)
        enriched_drafts=[]
        for draft in drafts:
            try:summary=draft.get('summary') or Ontology(draft.get('turtle','')).summary()
            except (ValueError,TypeError):summary={'classes':[],'relations':[],'attributes':[]}
            enriched_drafts.append({**draft,'schema_summary':summary})
        return {**_summary(candidates),
            'drafts':enriched_drafts,
            'quality_warnings':_quality_warnings(candidates),
            'published':bool(ontologies),
            'ontology_id':ontologies[-1]['id'] if ontologies else None,
            'candidate_status_counts':status_counts,
            'unpublished_candidate_count':status_counts['pending']+status_counts['included_in_draft'],
            'requires_controlled_reingest':bool(status_counts['approved'])}

    @router.get('/candidate-mindmap')
    def candidate_mindmap(p:str,limit:int=500):
        service.repository.get_project(p)
        candidates=_candidates(service.repository,p)
        drafts=service.repository.list_artifacts('ontology_discovery_draft',p)
        states,_=_candidate_lifecycle(service.repository,p,candidates,drafts)
        return _candidate_mindmap(candidates,states,limit)

    @router.post('/drafts',status_code=201)
    def create_draft(p:str,request:DraftRequest):
        service.repository.get_project(p);candidates=_candidates(service.repository,p)
        if not candidates:raise ValueError('尚无开放发现候选，请先用“开放本体发现”模式解析文档')
        ontologies=service.repository.list_ontologies(p)
        parent=ontologies[-1] if ontologies else None
        baseline=parent['turtle'] if parent else None
        turtle,mappings,inferred=_induce(p,request.name,candidates,baseline_turtle=baseline)
        draft_id=str(uuid4());summary=Ontology(turtle).summary();diff=_ontology_diff(baseline,turtle)
        candidate_ids=[x['id'] for x in candidates]
        draft={'id':draft_id,'project_id':p,'name':request.name,'status':'draft','revision':1,
            'generator_backend':'semantica','created_at':utc_now(),'candidate_ids':candidate_ids,
            'candidate_snapshot':candidates,
            'candidate_count':len(candidates),'turtle':turtle,'mappings':mappings,'summary':summary,
            'parent_ontology_id':parent['id'] if parent else None,'diff':diff,
            'quality_warnings':_quality_warnings(candidates),
            'inference':{'metadata':inferred.get('metadata',{}),'validation':inferred.get('validation',{})},
            'ontology_metadata':{'parent_version_id':parent['id'] if parent else None,
                'source_draft_id':draft_id,'diff':diff,'candidate_ids':candidate_ids}}
        return service.repository.save_artifact('ontology_discovery_draft',draft)

    @router.post('/drafts/{draft_id}/publish')
    def publish(p:str,draft_id:str):
        with service.lock:
            draft=service.repository.get_artifact('ontology_discovery_draft',draft_id)
            if draft.get('project_id')!=p:raise KeyError(draft_id)
            if draft.get('status')!='draft':raise ValueError('Version conflict: 该发现草案已经发布，不能重复发布')
            _,published=service.repository.publish_ontology_draft(
                p,draft,draft.get('parent_ontology_id'))
            return published

    app.include_router(router)
