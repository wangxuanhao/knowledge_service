"""开放模式候选聚合、Semantica 归纳与带版本发布（业务逻辑）。

路由层在 api.ontology_discovery，本文件只含候选摊平、生命周期分类、
归纳与物化校验等业务函数。
"""
from collections import Counter,defaultdict
from copy import deepcopy
import hashlib
import json
import re
from urllib.parse import unquote
from uuid import uuid4

from rdflib import Graph,Literal,Namespace,RDF,RDFS,URIRef
from rdflib.namespace import OWL,XSD

from ..services.ontology import Ontology, readable_iri_segment
from ..services.discovery_vocabulary import (
    DiscoveryVocabularyNormalizer,
    validate_generated_term_kinds,
)
from ..services.ontology_shape import structurally_changed_iris
from ..core.time import utc_now
from ..utils.attributes import primitive_datatype
from ..utils.diagnostics import timed


NORMAL_CANDIDATE_KINDS={'entity','relation','attribute'}


def _normalize_induction_candidates(candidates, baseline_turtle=None):
    """Normalize route candidates and return only safe Semantica inputs."""
    originals={item['id']:item for item in candidates}
    boundary=[]
    for item in candidates:
        payload=deepcopy(item)
        payload['kind']='class' if item.get('kind')=='entity' else item.get('kind')
        payload['name']=item.get('proposed_type')
        payload['evidence_refs']=list(item.get('evidence_refs') or [
            item.get('assertion_id') or item['id']])
        boundary.append(payload)
    result=DiscoveryVocabularyNormalizer(baseline_turtle or '').normalize(boundary)
    bindings={item['candidate_id']:item for item in result.candidate_bindings}
    accepted=[]
    for item in result.accepted_candidates:
        payload=deepcopy(originals[item['id']])
        leader_id=bindings[item['id']]['accepted_candidate_id']
        payload['vocabulary_name']=originals[leader_id]['proposed_type']
        if item.get('iri'):
            payload['iri']=item['iri']
        accepted.append(payload)
    return accepted,result


def _literal_language(value):
    return 'zh' if any('\u4e00' <= char <= '\u9fff' for char in str(value)) else 'en'


def _definition(kind,label):
    names={'class':'实体类型','relation':'关系类型','attribute':'属性类型'}
    return f'由开放知识候选归纳的“{label}”{names[kind]}；发布前需人工审核其边界与示例。'


def _formal_id(project_id,candidate):
    identity='|'.join((project_id,str(candidate.get('document_version_id','')),str(candidate.get('id',''))))
    return 'discovery-'+hashlib.sha256(identity.encode()).hexdigest()[:28]


def _candidate_outcome_skip(item, outcome):
    code = outcome.get('code')
    reason_code = ('low_frequency_attribute' if code == 'low_frequency_attribute'
        else 'ontology_term_conflict')
    return {
        'candidate_id': item.get('id'), 'kind': item.get('kind'),
        'reason_code': reason_code,
        'reason': ('低频属性未进入发现本体' if reason_code == 'low_frequency_attribute'
            else '候选词与本体术语类型冲突'),
        'candidate': deepcopy(item),
    }


def _materialize_candidates(project_id,draft,ontology_id,candidate_outcomes=()):
    """把已审核候选转换为可追溯的正式记录，无需再次调用模型。"""
    candidates=draft.get('candidate_snapshot') or []
    excluded=set(draft.get('excluded_candidate_ids') or [])
    outcomes={item.get('candidate_id'):item for item in candidate_outcomes or ()}
    mappings=draft.get('mappings') or {};entity_types=mappings.get('entity_types') or {}
    relation_types=mappings.get('relation_types') or {};attribute_types=mappings.get('attributes') or {}
    entities=[item for item in candidates if item.get('kind')=='entity' and item.get('id') not in excluded
        and item.get('id') not in outcomes]
    by_document=defaultdict(dict);records=[]
    skipped=[_candidate_outcome_skip(item,outcomes[item.get('id')]) for item in candidates
        if item.get('id') in outcomes]
    for item in entities:
        type_iri=entity_types.get(item.get('proposed_type'))
        if not type_iri or not str(item.get('text','')).strip():
            skipped.append({'candidate_id':item.get('id'),'kind':'entity','reason':'实体名称或类型未通过审核'});continue
        record_id=_formal_id(project_id,item);document_key=item.get('document_version_id') or item.get('document_id')
        by_document[document_key][item.get('id')]=record_id
        records.append({'id':record_id,'kind':'entity','type':type_iri,'text':item['text'],
            'source_id':item.get('document_id'),'ontology_id':ontology_id,
            'valid_from':item.get('valid_from'),'valid_until':item.get('valid_until'),'properties':{},
            'metadata':{'discovery_candidate_id':item.get('id'),'discovery_candidate_ids':[item.get('id')],
                'discovery_draft_id':draft.get('id'),'confidence':item.get('confidence'),
                'evidence':item.get('evidence'),'chunk_id':item.get('chunk_id')}})
    record_by_id={record['id']:record for record in records}
    for item in candidates:
        if (item.get('kind')!='attribute' or item.get('id') in excluded
                or item.get('id') in outcomes):continue
        document_key=item.get('document_version_id') or item.get('document_id')
        entity_id=by_document[document_key].get(item.get('entity_id'));attribute=attribute_types.get(item.get('proposed_type'))
        if not entity_id or not attribute:
            skipped.append({'candidate_id':item.get('id'),'kind':'attribute','reason':'属性或所属实体未通过审核'});continue
        try:datatype=primitive_datatype(item.get('value'))
        except ValueError as exc:
            skipped.append({'candidate_id':item.get('id'),'kind':'attribute','reason':str(exc)});continue
        value_text=json.dumps(item.get('value'),ensure_ascii=False,allow_nan=False,separators=(',',':'))
        records.append({'id':_formal_id(project_id,item),'kind':'attribute','type':attribute,
            'text':f"{item.get('subject') or record_by_id[entity_id]['text']} · {item.get('proposed_type')} = {value_text}",
            'subject_id':entity_id,'value':item.get('value'),'datatype':datatype,
            'source_id':item.get('document_id'),'ontology_id':ontology_id,
            'valid_from':item.get('valid_from'),'valid_until':item.get('valid_until'),'properties':{},
            'metadata':{'discovery_candidate_id':item.get('id'),'discovery_candidate_ids':[item.get('id')],
                'discovery_draft_id':draft.get('id'),'confidence':item.get('confidence'),
                'evidence':item.get('attribute_evidence') or item.get('evidence'),
                'attribute_evidence':item.get('attribute_evidence'),
                'evidence_status':item.get('evidence_status'),'chunk_id':item.get('chunk_id')}})
    for item in candidates:
        if (item.get('kind')!='relation' or item.get('id') in excluded
                or item.get('id') in outcomes):continue
        document_key=item.get('document_version_id') or item.get('document_id');local=by_document[document_key]
        subject=local.get(item.get('subject_id'));obj=local.get(item.get('object_id'));type_iri=relation_types.get(item.get('proposed_type'))
        if not subject or not obj or not type_iri:
            skipped.append({'candidate_id':item.get('id'),'kind':'relation','reason':'关系类型或端点未通过审核'});continue
        records.append({'id':_formal_id(project_id,item),'kind':'relation','type':type_iri,
            'text':f"{item.get('subject') or record_by_id[subject]['text']} {item.get('proposed_type')} {item.get('object') or record_by_id[obj]['text']}",
            'subject_id':subject,'object_id':obj,'source_id':item.get('document_id'),'ontology_id':ontology_id,
            'valid_from':item.get('valid_from'),'valid_until':item.get('valid_until'),
            'metadata':{'discovery_candidate_id':item.get('id'),'discovery_candidate_ids':[item.get('id')],
                'discovery_draft_id':draft.get('id'),'confidence':item.get('confidence'),
                'evidence':item.get('evidence'),'chunk_id':item.get('chunk_id')}})
    return records,skipped


def _validated_materialization(turtle,records,skipped):
    """发布前只保留符合已审核本体的记录。"""
    ontology=Ontology(turtle);accepted_entities=[];accepted_attributes=[];accepted_relations=[]
    for record in (row for row in records if row['kind']=='entity'):
        report=ontology.validate_timeline([*accepted_entities,record],enforce_relationship_constraints=True)
        if report['conforms']:
            accepted_entities.append(record);continue
        skipped.append({'candidate_id':record['metadata']['discovery_candidate_id'],'kind':'entity',
            'reason':report['errors'][0]['message'] if report.get('errors') else '实体未通过本体校验'})
    accepted_entity_ids={row['id'] for row in accepted_entities}
    for record in (row for row in records if row['kind']=='attribute'):
        if record.get('subject_id') not in accepted_entity_ids:
            skipped.append({'candidate_id':record['metadata']['discovery_candidate_id'],'kind':'attribute',
                'reason':'属性主体实体未通过本体校验'});continue
        report=ontology.validate_timeline(
            [*accepted_entities,*accepted_attributes,record],enforce_relationship_constraints=True)
        if report['conforms']:
            accepted_attributes.append(record);continue
        skipped.append({'candidate_id':record['metadata']['discovery_candidate_id'],'kind':'attribute',
            'reason':report['errors'][0]['message'] if report.get('errors') else '属性未通过本体校验'})
    for record in (row for row in records if row['kind']=='relation'):
        if record.get('subject_id') not in accepted_entity_ids or record.get('object_id') not in accepted_entity_ids:
            skipped.append({'candidate_id':record['metadata']['discovery_candidate_id'],'kind':'relation',
                'reason':'关系端点实体未通过本体校验'});continue
        report=ontology.validate_timeline(
            [*accepted_entities,*accepted_attributes,*accepted_relations,record],enforce_relationship_constraints=True)
        if report['conforms']:
            accepted_relations.append(record);continue
        skipped.append({'candidate_id':record['metadata']['discovery_candidate_id'],'kind':'relation',
            'reason':report['errors'][0]['message'] if report.get('errors') else '关系未通过本体校验'})
    accepted=[*accepted_entities,*accepted_attributes,*accepted_relations]
    return accepted,skipped,{'conforms':True,'accepted_count':len(accepted),'skipped_count':len(skipped)}


def _candidates(repository,project_id,records=None):
    """把每个当前文档的 `metadata.discovery_candidates` 摊平。

    PERF：全量 `current_records` 扫描（SELECT * + 解码每个载荷的 JSON）。
    `_candidate_lifecycle` 在同一请求中也需要这些行，因此调用方通过 `records`
    传入已经读过的列表，避免第二次扫描。
    """
    result=[]
    with timed('候选发现') as record:
        assertions={item['id']:item for item in repository.list_assertions(project_id)}
        for document in (repository.current_records(project_id, vectors='none', kinds=['document']) if records is None else records):
            if document['kind']!='document' or document.get('metadata',{}).get('_deleted'):continue
            for item in document.get('metadata',{}).get('discovery_candidates',[]):
                candidate={**item,'document_id':document['id'],'document_version_id':document['version_id'],
                    'document_title':document['metadata'].get('title',document['id']),
                    'valid_from':document.get('valid_from'),'valid_until':document.get('valid_until')}
                assertion=assertions.get(item.get('id')) if item.get('kind') in NORMAL_CANDIDATE_KINDS else None
                candidate.update({
                    'assertion_id':assertion.get('id') if assertion else None,
                    'assertion_document_id':assertion.get('document_id') if assertion else None,
                    'assertion_document_version_id':assertion.get('document_version_id') if assertion else None,
                    'assertion_chunk_id':assertion.get('chunk_id') if assertion else None,
                    'assertion_start_char':assertion.get('start_char') if assertion else None,
                    'assertion_end_char':assertion.get('end_char') if assertion else None,
                    'assertion_status':assertion.get('status') if assertion else None,
                })
                result.append(candidate)
        record['candidates']=len(result)
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
    exceptions=[x for x in candidates if x.get('kind')=='exception']
    return {'candidate_count':sum(x.get('kind') in NORMAL_CANDIDATE_KINDS for x in candidates),
        'entity_count':sum(entities.values()),'relation_count':sum(len(v) for v in relations.values()),
        'attribute_count':sum(attributes.values()),'exception_count':len(exceptions),
        'entity_types':[{'name':name,'count':count,'examples':entity_groups[name][:5]}
            for name,count in sorted(entities.items(),key=lambda x:(-x[1],x[0]))],
        'attribute_types':[{'name':name,'count':count} for name,count in sorted(attributes.items(),key=lambda x:(-x[1],x[0]))],
        'relation_types':[{'name':name,'count':len(values),'examples':values[:3]} for name,values in sorted(relations.items())]}


def _candidate_lifecycle(repository,project_id,candidates,drafts,records=None):
    """对每个候选分类：pending / in draft / approved / materialized。

    PERF：复用调用方已经执行过的 `current_records` 扫描（通过 `records` 传入），
    避免为同一请求第二次读取每个载荷。
    """
    candidate_ids={item['id'] for item in candidates if item.get('kind') in NORMAL_CANDIDATE_KINDS}
    materialized_ids=set()
    with timed('候选生命周期', candidates=len(candidates), drafts=len(drafts),
               reused_records=records is not None) as record:
        for record_row in (repository.current_records(project_id, vectors='none') if records is None else records):
            if record_row['kind'] not in ('entity','relation','attribute') or record_row.get('metadata',{}).get('_deleted'):continue
            metadata=record_row.get('metadata',{})
            if metadata.get('discovery_candidate_id'):
                materialized_ids.add(metadata['discovery_candidate_id'])
            materialized_ids.update(metadata.get('discovery_candidate_ids') or [])
        record['materialized']=len(materialized_ids)
    published_ids=set();included_ids=set()
    for draft in drafts:
        included=set(draft.get('candidate_ids') or [])-set(draft.get('excluded_candidate_ids') or [])
        included-= {item.get('candidate_id') for item in draft.get('skipped_candidates') or []}
        if draft.get('status')=='published':
            published_ids.update(included)
        elif draft.get('status') in {'draft','submitted'}:
            included_ids.update(included)
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


def _candidate_source_reference(item):
    """Build one lightweight candidate source reference without resolving evidence."""
    normal=item.get('kind') in NORMAL_CANDIDATE_KINDS
    assertion_id=item.get('assertion_id') if normal else None
    preview=str(item.get('attribute_evidence') or item.get('evidence') or '')
    return {'assertion_id':assertion_id,
        'document_id':item.get('assertion_document_id') if assertion_id else item.get('document_id'),
        'document_title':item.get('document_title'),
        'document_version_id':item.get('assertion_document_version_id') if assertion_id else None,
        'chunk_id':item.get('assertion_chunk_id') if assertion_id else None,
        'start_char':item.get('assertion_start_char') if assertion_id else None,
        'end_char':item.get('assertion_end_char') if assertion_id else None,
        'confidence':item.get('confidence'),'status':item.get('assertion_status') or item.get('status'),
        'evidence_status':item.get('evidence_status'),
        'evidence_preview':preview[:500],
        'evidence_preview_truncated':len(preview)>500,
        'resolvable':bool(assertion_id)}


def _finalize_candidate_sources(item):
    sources=sorted(item.get('sources') or [],key=lambda source:(
        str(source.get('document_title') or ''),str(source.get('document_id') or ''),
        source.get('start_char') if type(source.get('start_char')) is int else float('inf'),
        str(source.get('assertion_id') or '')))
    item['source_count']=len(sources)
    item['sources_truncated']=len(sources)>10
    item['sources']=sources[:10]


def _candidate_mindmap(candidates,states,limit=500):
    """聚合开放出现以便可视化，而不创建规范知识。

    响应大小随候选数量增长，而非随 ``limit``：每个被选中的节点携带最多
    10 条来源引用（含最长 500 字符的预览）和最多 30 个属性，所以 ``limit=500``
    仍可能序列化出数兆字节。浏览器随后把这些节点喂给 ECharts 的 *force*
    布局，并给每个节点和边加标签——这是延迟中属于客户端的那一半。
    """
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
        node['sources'].append(_candidate_source_reference(item))
        candidate_to_node[item['id']]=node['id'];text_to_nodes[text.casefold()].append(node['id'])
    ordered=sorted(entity_groups.values(),key=lambda item:(-item['occurrence_count'],item['type'],item['text']))
    selected=ordered[:max(1,min(int(limit),2000))];selected_ids={item['id'] for item in selected}
    node_by_id={item['id']:item for item in selected}
    attribute_groups={}
    for item in candidates:
        if item['kind']!='attribute':continue
        node_id=candidate_to_node.get(item.get('entity_id'))
        if node_id in node_by_id and len(node_by_id[node_id]['attributes'])<30:
            node_by_id[node_id]['attributes'].append({'name':item.get('proposed_type'),'value':item.get('value'),
                'candidate_id':item['id'],'status':states.get(item['id'],'pending')})
        if node_id not in node_by_id:continue
        value_key=json.dumps(item.get('value'),ensure_ascii=False,sort_keys=True,separators=(',',':'))
        kind=str(item.get('proposed_type','')).strip() or '未分类属性'
        key=(node_id,kind.casefold(),value_key)
        attribute=attribute_groups.setdefault(key,{
            'id':'candidate-attribute:'+hashlib.sha256(repr(key).encode()).hexdigest()[:24],
            'subject_id':node_id,'subject':node_by_id[node_id]['text'],'type':kind,
            'value':item.get('value'),'value_type':item.get('value_type'),
            'candidate_ids':[],'document_ids':[],'sources':[],
            'status_counts':Counter(),'occurrence_count':0})
        attribute['candidate_ids'].append(item['id']);attribute['occurrence_count']+=1
        attribute['status_counts'][states.get(item['id'],'pending')]+=1
        if item.get('document_id') not in attribute['document_ids']:
            attribute['document_ids'].append(item.get('document_id'))
        attribute['sources'].append(_candidate_source_reference(item))
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
            'type':kind,'subject_id':subject,'object_id':obj,
            'subject':node_by_id[subject]['text'],'object':node_by_id[obj]['text'],
            'candidate_ids':[],'sources':[],
            'status_counts':Counter(),'occurrence_count':0})
        edge['candidate_ids'].append(item['id']);edge['occurrence_count']+=1
        edge['status_counts'][states.get(item['id'],'pending')]+=1
        edge['sources'].append(_candidate_source_reference(item))
    priority=('materialized','approved','included_in_draft','pending')
    for item in [*selected,*relation_groups.values(),*attribute_groups.values()]:
        item['status']=next((status for status in priority if item['status_counts'].get(status)), 'pending')
        item['status_counts']=dict(item['status_counts'])
        _finalize_candidate_sources(item)
    exceptions=[]
    for item in candidates:
        if item.get('kind')!='exception':continue
        exceptions.append({'id':item['id'],'source_kind':item.get('source_kind','unknown'),
            'reason_code':item.get('reason_code'),'reason':item.get('reason'),
            'text':item.get('text'),'subject':item.get('subject'),
            'type':item.get('proposed_type'),'object':item.get('object'),'value':item.get('value'),
            'evidence_status':item.get('evidence_status'),'occurrence_count':1,
            'sources':[_candidate_source_reference(item)]})
        _finalize_candidate_sources(exceptions[-1])
        if len(exceptions)>=200:break
    normal_count=sum(x.get('kind') in NORMAL_CANDIDATE_KINDS for x in candidates)
    return {'nodes':selected,'edges':list(relation_groups.values()),
        'attributes':list(attribute_groups.values()),'exceptions':exceptions,
        'summary':{'candidate_count':normal_count,'exception_count':sum(x.get('kind')=='exception' for x in candidates),
            'entity_occurrences':sum(x['occurrence_count'] for x in ordered),
            'entity_clusters':len(ordered),'visible_entity_clusters':len(selected),
            'relation_clusters':len(relation_groups),'attribute_clusters':len(attribute_groups),
            'unresolved_relations':unresolved,
            'hidden_relations':hidden_relations},
        'truncated':len(selected)<len(ordered)}


def _quality_warnings(candidates):
    """在弱开放词汇变成已发布契约之前给出告警。"""
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
    return URIRef(base+readable_iri_segment(name))


def _candidate_vocabulary_name(candidate):
    return candidate.get('vocabulary_name') or candidate.get('proposed_type')


def _candidate_iri_bindings(candidates, kind):
    return {
        str(_candidate_vocabulary_name(item)).strip(): URIRef(
            item.get('reuse_iri') or item.get('target_iri') or item['iri'])
        for item in candidates
        if item.get('kind') == kind
        and (item.get('reuse_iri') or item.get('target_iri') or item.get('iri'))
    }


def _machine_name(prefix,value):
    """为 Semantica 生成稳定的 ASCII 名称，而标签保留源语言。"""
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
        {_candidate_vocabulary_name(x) for x in candidates if x['kind']=='entity'}}
    relation_machine={source:_machine_name('RelationType',source) for source in
        {_candidate_vocabulary_name(x) for x in candidates if x['kind']=='relation'}}
    sample_fields={'type','entity_type','name','text','confidence','properties'}
    attribute_counts=Counter(str(_candidate_vocabulary_name(x)).strip()
        for x in candidates if x['kind']=='attribute')
    attribute_machine={}
    for source in {_candidate_vocabulary_name(x) for x in candidates if x['kind']=='attribute'}:
        if attribute_counts[str(source).strip()]<2:continue
        machine=_machine_name('AttributeType',str(source).strip())
        if machine in sample_fields:machine='AttributeType_'+hashlib.sha256(str(source).strip().encode('utf-8')).hexdigest()[:12]
        attribute_machine[source]=machine
    reverse_entity={value:key for key,value in entity_machine.items()}
    reverse_relation={value:key for key,value in relation_machine.items()}
    reverse_attribute={value:str(key).strip() for key,value in attribute_machine.items()}
    attributes=defaultdict(dict)
    for item in candidates:
        source=_candidate_vocabulary_name(item)
        if item['kind']=='attribute' and source in attribute_machine:
            attributes[item.get('entity_id')].setdefault(
                attribute_machine[source],item.get('value'))
    entities=[]
    for item in candidates:
        if item['kind']!='entity':continue
        properties=attributes.get(item['id'],{})
        source=_candidate_vocabulary_name(item)
        entities.append({'type':entity_machine[source],
            'entity_type':entity_machine[source],
            'name':item['text'],'text':item['text'],'confidence':item.get('confidence',1),
            'properties':properties,**properties})
    relationships=[]
    for item in candidates:
        if item['kind']!='relation':continue
        subject=by_id.get(item.get('subject_id'),{})
        obj=by_id.get(item.get('object_id'),{})
        source=_candidate_vocabulary_name(item)
        subject_type=_candidate_vocabulary_name(subject) or item.get('subject_type')
        object_type=_candidate_vocabulary_name(obj) or item.get('object_type')
        relationships.append({'type':relation_machine[source],
            'relationship_type':relation_machine[source],
            'source':item.get('subject') or subject.get('text'),'target':item.get('object') or obj.get('text'),
            'source_type':entity_machine.get(subject_type,subject_type),
            'target_type':entity_machine.get(object_type,object_type)})
    base=f'urn:knowledge:ontology:{project_id}:'
    class_bindings=_candidate_iri_bindings(candidates,'entity')
    relation_bindings=_candidate_iri_bindings(candidates,'relation')
    attribute_bindings=_candidate_iri_bindings(candidates,'attribute')
    inferred=OntologyGenerator(base_uri=base,min_occurrences=1).generate_ontology(
        {'entities':entities,'relationships':relationships},name=name,build_hierarchy=True)
    graph=Graph()
    if baseline_turtle:graph.parse(data=baseline_turtle,format='turtle')
    baseline_graph=Graph()
    for triple in graph:
        baseline_graph.add(triple)
    ns=Namespace(base);graph.bind('disc',ns);graph.bind('owl',OWL);graph.bind('rdfs',RDFS)
    catalog=_term_catalog(graph)
    inferred_classes=inferred.get('classes',[])
    class_map={};class_lookup=_term_lookup(catalog,'classes')
    for item in inferred_classes:
        machine_source=str(item.get('metadata',{}).get('inferred_from') or item.get('name'))
        source=reverse_entity.get(machine_source,machine_source)
        uri=(class_bindings.get(str(source).strip())
            or class_lookup.get(str(source).strip().casefold()) or _iri(base,source))
        class_map[str(source)]=str(uri)
        for key in (source,machine_source,item.get('name'),item.get('uri')):
            if key:class_lookup[str(key).lower()]=uri
        graph.add((uri,RDF.type,OWL.Class));graph.add((uri,RDFS.label,Literal(str(source),lang=_literal_language(source))))
        graph.add((uri,RDFS.comment,Literal(_definition('class',source),lang='zh')))
    for source in sorted({_candidate_vocabulary_name(x)
            for x in candidates if x['kind']=='entity'}):
        if source not in class_map:
            uri=(class_bindings.get(str(source).strip())
                or class_lookup.get(str(source).strip().casefold()) or _iri(base,source))
            class_map[source]=str(uri);class_lookup[source.lower()]=uri
            graph.add((uri,RDF.type,OWL.Class));graph.add((uri,RDFS.label,Literal(source,lang=_literal_language(source))))
            graph.add((uri,RDFS.comment,Literal(_definition('class',source),lang='zh')))
    # Semantica 0.6.7 emits at most one suggested parent in ``parent``.
    # ``subClassOf`` is accepted only as a compatibility alias.  Unknown or
    # missing parents deliberately leave the class as an independent root.
    for item in inferred_classes:
        machine_source=str(item.get('metadata',{}).get('inferred_from') or item.get('name'))
        source=reverse_entity.get(machine_source,machine_source)
        child=class_map.get(str(source))
        parent_value=item.get('parent')
        if parent_value is None:
            parent_value=item.get('subClassOf')
        if isinstance(parent_value,dict):
            parent_value=parent_value.get('name') or parent_value.get('uri')
        if not child or not isinstance(parent_value,str) or not parent_value.strip():
            continue
        parent_source=reverse_entity.get(parent_value,parent_value)
        parent=class_map.get(str(parent_source))
        if parent and parent!=child:
            graph.add((URIRef(child),RDFS.subClassOf,URIRef(parent)))
    relation_map={};attribute_map={}
    relation_lookup=_term_lookup(catalog,'relations');attribute_lookup=_term_lookup(catalog,'attributes')
    for item in inferred.get('properties',[]):
        machine_source=str(item.get('metadata',{}).get('inferred_from') or item.get('name'))
        is_object=item.get('type')=='object'
        if not is_object and machine_source not in reverse_attribute:
            continue
        source=(reverse_relation if is_object else reverse_attribute).get(machine_source,machine_source)
        lookup=relation_lookup if is_object else attribute_lookup
        bindings=relation_bindings if is_object else attribute_bindings
        uri=(bindings.get(str(source).strip())
            or lookup.get(str(source).strip().casefold()) or _iri(base,source))
        graph.add((uri,RDF.type,OWL.ObjectProperty if is_object else OWL.DatatypeProperty))
        graph.add((uri,RDFS.label,Literal(source,lang=_literal_language(source))))
        graph.add((uri,RDFS.comment,Literal(_definition('relation' if is_object else 'attribute',source),lang='zh')))
        target=relation_map if is_object else attribute_map;target[source]=str(uri)
        # 发现模式下，观测到的关系端点类型是证据而非约束。
        # 数据类型属性仍需要适用的类和值数据类型。
        if not is_object:
            domains=item.get('domain',[]) if isinstance(item.get('domain',[]),list) else [item.get('domain')]
            ranges=item.get('range',[]) if isinstance(item.get('range',[]),list) else [item.get('range')]
            for value in domains:
                resolved=class_lookup.get(str(value).lower()) if value else None
                if resolved:graph.add((uri,RDFS.domain,resolved))
            for value in ranges:
                if not value:continue
                resolved=class_lookup.get(str(value).lower())
                if resolved:graph.add((uri,RDFS.range,resolved))
                elif str(value).startswith('xsd:'):graph.add((uri,RDFS.range,getattr(XSD,str(value).split(':',1)[1])))
    for source in sorted({_candidate_vocabulary_name(x)
            for x in candidates if x['kind']=='relation'}):
        if source not in relation_map:
            uri=(relation_bindings.get(str(source).strip())
                or relation_lookup.get(str(source).strip().casefold()) or _iri(base,source));relation_map[source]=str(uri)
            graph.add((uri,RDF.type,OWL.ObjectProperty));graph.add((uri,RDFS.label,Literal(source,lang=_literal_language(source))))
            graph.add((uri,RDFS.comment,Literal(_definition('relation',source),lang='zh')))
    attribute_sources={_candidate_vocabulary_name(x)
        for x in candidates if x['kind']=='attribute'}
    for source in sorted(attribute_sources):
        canonical=str(source).strip()
        if canonical in attribute_map:continue
        existing=attribute_lookup.get(canonical.casefold())
        if existing or attribute_counts[canonical]>=2:
            uri=attribute_bindings.get(canonical) or existing or _iri(base,canonical);attribute_map[canonical]=str(uri)
            graph.add((uri,RDF.type,OWL.DatatypeProperty));graph.add((uri,RDFS.label,Literal(canonical,lang=_literal_language(canonical))))
            graph.add((uri,RDFS.comment,Literal(_definition('attribute',canonical),lang='zh')))
    attribute_map={source:attribute_map[str(source).strip()] for source in attribute_sources
        if str(source).strip() in attribute_map}
    mapping_specs=(('entity',class_map),('relation',relation_map),('attribute',attribute_map))
    for kind,mapping in mapping_specs:
        for item in candidates:
            if item.get('kind')!=kind:continue
            target=_candidate_vocabulary_name(item)
            iri=mapping.get(target) or mapping.get(str(target).strip())
            if iri:mapping[item['proposed_type']]=iri
    # 父发现版本可能包含旧代码推断出的端点值域。
    # 对本开放候选集中观测到的每条关系都移除它们。显式约束属于
    # 引导式/非开放本体工作流及其审核队列。
    for iri in relation_map.values():
        predicate=URIRef(iri);graph.remove((predicate,RDFS.domain,None));graph.remove((predicate,RDFS.range,None))
    turtle=graph.serialize(format='turtle')
    validate_generated_term_kinds(
        turtle, changed_iris=structurally_changed_iris(baseline_graph, graph))
    Ontology(turtle)
    return turtle,{'entity_types':class_map,'relation_types':relation_map,'attributes':attribute_map},inferred
