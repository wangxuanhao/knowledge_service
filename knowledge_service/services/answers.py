"""先独立检索实体/来源，再提供限定范围内的图谱证据与 SSE 流式输出。"""
import asyncio
import json
import logging
import os

from ..core.net import external_client
from . import structure_pending
from .provenance import ProvenanceService
from .retrieval import RetrievalEngine
from .retrieval_context import adjacent_chunks
from ..domain.applicability import dimension_mode, resolve_query_applicability


def pending_block(rows):
    """结构待定的知识 → 问答页面用的那一份单列数据（**不是证据**）。

    问答有两个入口：`service.answer`（一次性 JSON）与 `stream_events`（SSE 流）。
    两边各写一套必然出现"流式说没有、一次性说有"这种自相矛盾，所以口径只在这里定义一次。
    """
    return {
        'count': len(rows),
        'terms': sorted({term for row in rows for term in
                         (row.get('structure_pending') or {}).get('terms') or []}),
        'items': [{'citation': row['citation'], 'id': row['id'], 'kind': row['kind'],
                   'text': row['text'], 'text_preview': row.get('text_preview'),
                   'terms': (row.get('structure_pending') or {}).get('terms') or []}
                  for row in rows],
        'note': '这些知识的类型在本体里还没有（结构待定）：能查、能看原文，但不作为正式证据。',
    }


def no_evidence_text(block):
    """范围内一条正式证据都没有、但有结构待定知识时，答案必须**显式说出来**。

    这是"真没有"和"有但不算数"的区别 —— 用"没有证据"盖过去，用户会以为系统把知识弄丢了。
    """
    return (f"当前查询范围内有 {block['count']} 条知识，但它们的类型在本体里还没有"
            f"（结构待定），不能作为正式证据。涉及的概念：{'、'.join(block['terms']) or '（未命名）'}。"
            '去「本体建模层」把概念建出来并发布后，这些知识会自动回到证据链里。')


def answer_messages(question, evidence):
    """Build one platform-aware prompt shared by JSON and SSE answers."""
    groups = {}
    for row in evidence:
        key = (row.get('query_platform'), row.get('source_scope') or 'unscoped')
        groups.setdefault(key, []).append({
            'id': row['citation'], 'text': row['text'],
            'applicability': (row.get('metadata') or {}).get('applicability'),
            'published_at': (row.get('metadata') or {}).get('published_at'),
            'valid_from': row.get('valid_from'),
            'valid_until': row.get('valid_until'),
        })
    platform_evidence = [
        {'platform': platform, 'source_scope': source_scope, 'evidence': rows}
        for (platform, source_scope), rows in groups.items()
    ]
    return [
        {'role': 'system', 'content':
         '你回答知识图谱问题，只依据给定证据推理，不得引入证据之外的事实。'
         '证据已按平台分组；每个平台必须独立给出结论，禁止把不同平台证据合并成一个结论。'
         '通用组可用于各平台，但仍须在对应平台结论中明确引用。'
         '按「结论 → 推理 → 依据」三层输出，每条结论引用 [E编号]；'
         '证据不足时明确说明，不要编造。'},
        {'role': 'user', 'content': json.dumps({
            'question': question, 'platform_evidence': platform_evidence,
        }, ensure_ascii=False)},
    ]


def _reasoning_paths(seeds, relations, max_hops):
    """把命中实体沿关系边还原成「A →关系→ B →关系→ C」的推理链（不调模型，纯图谱推导）。

    ``found`` 是 BFS 平铺出来的关系集合，本身没有方向、没有先后；这里用带路径的 BFS
    把「谁经哪条关系连到谁」还原成多跳链，作为问答里「推理路径」这一块展示——
    让用户看到命中 A 之后，是**怎么一步步走到结论节点 C 的**，而不是只有一堆证据文本。
    """
    from collections import deque
    def _label(edge):
        # 关系的可读名：type 是 IRI（如 urn:...:计算），取最后一段（fragment）；否则退回 text。
        t = edge.get('type') or ''
        if t and ('#' in t or ':' in t):
            return t.rsplit('#', 1)[-1].rsplit(':', 1)[-1]
        return t or edge.get('text') or ''
    reached = {seed: [] for seed in seeds}
    queue = deque(seeds)
    seen = set(seeds)
    while queue:
        node = queue.popleft()
        path = reached[node]
        if len(path) >= max_hops:
            continue
        for edge in relations:
            if edge['subject_id'] == node:
                other = edge['object_id']
            elif edge['object_id'] == node:
                other = edge['subject_id']
            else:
                continue
            if other in seen:
                continue
            seen.add(other)
            step = {'from': node, 'to': other,
                    'relation': _label(edge),
                    'relation_id': edge['id']}
            reached[other] = path + [step]
            queue.append(other)
    # 只保留非空路径（种子到自身是空路径，不算推理）；steps[0]['from'] 即种子。
    return [{'steps': steps, 'start': steps[0]['from'], 'end': steps[-1]['to']}
            for steps in reached.values() if steps]


def _legacy_question_context(service,p,request):
    # PERF：一次完整的范围读取，其余全部在 Python 中基于这些行计算。
    # 读取由 `service.scoped` 计时；下方的图谱 BFS 每跳遍历一次全部关系，
    # 因此 `hops` 会放大读取后的计算成本。`_include_embeddings`
    # 仅在模式确实需要按向量排序时才保留向量。
    applicability = resolve_query_applicability(
        request.get('query', ''), request.get('applicability'))
    platforms = applicability.get('platforms') or []
    if len(platforms) > 1 and not request.get('_single_platform_context'):
        grouped = []
        for platform in platforms:
            child = question_context(service, p, {
                **request,
                'applicability': {**applicability, 'platforms': [platform]},
                '_single_platform_context': True,
            })
            for key in ('hits', 'evidence_rows', 'structure_pending_rows', 'context_chunks'):
                child[key] = [{**row, 'query_platform': platform}
                              for row in child.get(key, [])]
            child['reasoning_paths'] = [
                {**path, 'query_platform': platform}
                for path in child.get('reasoning_paths', [])]
            grouped.append((platform, child))
        first = grouped[0][1]
        channels = {
            key: sum(child.get('channels', {}).get(key, 0) for _, child in grouped)
            for key in ('entity', 'chunk', 'graph_evidence')}
        return {
            **first,
            'hits': [row for _, child in grouped for row in child['hits']],
            'evidence_rows': [row for _, child in grouped for row in child['evidence_rows']],
            'structure_pending_rows': [
                row for _, child in grouped for row in child['structure_pending_rows']],
            'context_chunks': [
                row for _, child in grouped for row in child.get('context_chunks', [])],
            'candidate_count': sum(child['candidate_count'] for _, child in grouped),
            'reasoning_paths': [
                path for _, child in grouped for path in child['reasoning_paths']],
            'active_modes': sorted({
                mode for _, child in grouped for mode in child['active_modes']}),
            'degraded': any(child['degraded'] for _, child in grouped),
            'channels': channels,
            'platform_groups': [
                {'platform': platform, 'hits': child['hits'],
                 'evidence_rows': child['evidence_rows'],
                 'candidate_count': child['candidate_count']}
                for platform, child in grouped],
            'applicability': applicability,
        }
    scope={k:request.get(k) for k in (
        'filters','valid_at','known_at','kinds','ontology_ids')}
    if any(applicability.values()):
        scope['applicability'] = applicability
    scope['ontology_scope']=request.get('ontology_scope') or 'all'
    scope['include_unknown']=request.get('include_unknown',True)
    scope['_include_embeddings']=request.get('retrieval_mode','hybrid')!='keyword'
    # 本体查询扩展（P0-1，默认关）：开启时把扩展交给检索层，命中的子类实例会
    # 作为检索命中进入下面的种子集与证据集；扩展本身**不改排序**，只补召回。
    expansion=service.ontology_expansion(p,request['query'],request.get('ontology_expansion',False))
    if expansion:
        scope['_ontology_expansion']=expansion
    rows=service.scoped(p,scope)
    entities=[r for r in rows if r['kind']=='entity']
    chunks=[r for r in rows if r['kind']=='chunk']
    direct=[]
    engine=RetrievalEngine(service.repository,service.encoder,getattr(service,'milvus_store',None))
    result=engine.search(p,request['query'],retrieval_mode=request.get('retrieval_mode','hybrid'),
        scope=scope,content_channels=['entity','chunk'],k=request.get('k',10),
        content_k={'entity':request.get('k_entities',5),'chunk':request.get('k_chunks',5)},
        candidates=rows)
    retrieval=[result];direct.extend(result['hits'])
    context_chunks = adjacent_chunks(rows, direct)
    seeds={r['id'] for r in direct if r['kind']=='entity'}
    seeds|={r['id'] for r in entities if r['text'] and r['text'] in request['query']}
    seed_ids=set(seeds)
    relations=[r for r in rows if r['kind']=='relation'];found={};frontier=set(seeds)
    for _ in range(min(request.get('hops',2),3)):
        next_nodes=set()
        for edge in relations:
            if len(found)>=200:break
            if frontier.intersection((edge['subject_id'],edge['object_id'])):
                found[edge['id']]={k:v for k,v in edge.items() if k!='embedding'}
                next_nodes.update((edge['subject_id'],edge['object_id']))
        frontier=next_nodes-seeds;seeds|=next_nodes
    # 推理路径：把 found 平铺的关系还原成「命中实体 →关系→ 中间节点 →关系→ 结论」的多跳链。
    # 与 found（证据）不同，它展示的是**推导顺序**——问答里「推理路径」那一块，让用户
    # 看到命中 A 之后是怎么一步步走到结论节点的，而不是只有一堆证据文本。
    reasoning_paths=_reasoning_paths(seed_ids, relations, min(request.get('hops',2),3))
    # 给推理路径的节点/关系补上业务名，前端直接展示「命中『退款商户』→关系→『退款平台』」。
    _entity_text={r['id']: r['text'] for r in entities}
    for _path in reasoning_paths:
        _path['start_label']=_entity_text.get(_path['start'], _path['start'])
        _path['end_label']=_entity_text.get(_path['end'], _path['end'])
        for _step in _path['steps']:
            _step['from_label']=_entity_text.get(_step['from'], _step['from'])
            _step['to_label']=_entity_text.get(_step['to'], _step['to'])
    unique={r['id']:r for r in direct}
    unique.update({r['id']: r for r in context_chunks})
    unique.update(found)
    # C1「结构待定」：命中集里分清「正式证据」与「概念还没进本体的知识」。
    # 后者仍然可检索、可看原文、可被引用（所以照旧留在 hits 里并显示），
    # 但不进证据链 —— 一个本体里还没定义的概念，撑不起一条"正式结论"。
    # 判断只看服务端给的推导态（service.annotate_structure_pending），前端不再各判一套。
    service.annotate_structure_pending(p,list(unique.values()))
    formal_evidence,pending_evidence=structure_pending.split(list(unique.values()))
    return {'hits':direct,'context_chunks':context_chunks,'evidence_rows':formal_evidence,
            'structure_pending_rows':pending_evidence,'candidate_count':len(entities)+len(chunks),
            'reasoning_paths':reasoning_paths,
            'filter_stage':'before_ranking','embedding_model':service.encoder.identity,'semantic':service.encoder.semantic,
            'requested_mode':request.get('retrieval_mode','hybrid'),
            'active_modes':sorted(set(result['active_mode'] for result in retrieval)),
            'degraded':any(result['degraded'] for result in retrieval),
            # 透传检索后端与降级原因，前端/日志能看清走的是 Milvus 还是本地，以及为何降级
            '_backend':next((r.get('_backend') for r in retrieval if r.get('_backend')), None),
            'retrieval_errors':{name: info.get('error') for r in retrieval
                                for name, info in r.get('backends', {}).items() if info.get('error')},
            'channels':{'entity':sum(r['kind']=='entity' for r in direct),
                        'chunk':sum(r['kind']=='chunk' for r in direct),
                        'graph_evidence':len(found)},
            'applicability': applicability if any(applicability.values()) else None,
            'valid_at':request.get('valid_at'),'known_at':request.get('known_at')}


def question_context(service, p, request):
    """Build QA context from the same grouped retrieval used by search.

    Native current-view retrieval runs before any broad PostgreSQL scope read.
    PostgreSQL rows are loaded only for an actual fallback or a view that
    requires local filtering/history.
    """
    search_request = {**request, 'kinds': ['entity', 'chunk']}
    result = service.search(p, search_request)
    direct = list(result.get('hits', ()))
    native = result.get('_backend') == 'milvus'
    scope = {key: request.get(key) for key in (
        'filters', 'valid_at', 'known_at', 'ontology_ids')}
    scope.update(
        kinds=['entity', 'relation', 'chunk'],
        ontology_scope=request.get('ontology_scope') or 'all',
        include_unknown=request.get('include_unknown', True),
        _include_embeddings=False,
    )
    if result.get('applicability') is not None:
        scope['applicability'] = result['applicability']
    groups = result.get('platform_groups') or []
    rows = [] if native or groups else service.scoped(p, scope)
    context_chunks = list(result.get('context_chunks', ()))

    def split_unknown_scope(items):
        evidence, unknown = [], []
        for row in items:
            source_scope = row.get('source_scope')
            is_unknown = (source_scope == 'unknown' or (
                source_scope not in {'specific', 'all'} and
                dimension_mode((row.get('metadata') or {}).get('applicability'),
                               'platforms') == 'unknown'))
            (unknown if is_unknown else evidence).append(row)
        return evidence, unknown

    if groups:
        grouped_context = []
        formal_evidence = []
        pending_evidence = []
        unknown_scope_rows = []
        for group in groups:
            group_rows = list(group.get('hits', ())) + list(
                group.get('context_chunks', ()))
            service.annotate_structure_pending(p, group_rows)
            formal, pending = structure_pending.split(group_rows)
            formal, unknown_formal = split_unknown_scope(formal)
            pending, unknown_pending = split_unknown_scope(pending)
            group_unknown = [*unknown_formal, *unknown_pending]
            group_entry = {
                **group,
                'evidence_rows': formal,
                'structure_pending_rows': pending,
                'unknown_scope_rows': group_unknown,
            }
            grouped_context.append(group_entry)
            formal_evidence.extend(formal)
            pending_evidence.extend(pending)
            unknown_scope_rows.extend(group_unknown)
        return {
            'hits': direct,
            'context_chunks': context_chunks,
            'evidence_rows': formal_evidence,
            'structure_pending_rows': pending_evidence,
            'unknown_scope_rows': unknown_scope_rows,
            'candidate_count': result.get('candidate_count', 0),
            'reasoning_paths': [],
            'filter_stage': 'before_ranking',
            'embedding_model': service.encoder.identity,
            'semantic': service.encoder.semantic,
            'requested_mode': request.get('retrieval_mode', 'hybrid'),
            'active_modes': sorted({
                group.get('active_mode') for group in grouped_context
                if group.get('active_mode')} or {result.get('active_mode', 'hybrid')}),
            'degraded': result.get('degraded', False),
            '_backend': result.get('_backend'),
            'retrieval_errors': result.get('retrieval_errors') or {
                name: info.get('error')
                for name, info in result.get('backends', {}).items()
                if info.get('error')},
            'channels': {
                'entity': sum(row.get('kind') == 'entity' for row in direct),
                'chunk': sum(row.get('kind') == 'chunk' for row in direct),
                'graph_evidence': 0,
            },
            'platform_groups': grouped_context,
            'applicability': result.get('applicability'),
            'valid_at': request.get('valid_at'),
            'known_at': request.get('known_at'),
        }

    entities = [row for row in rows if row.get('kind') == 'entity']
    relations = [row for row in rows if row.get('kind') == 'relation']
    seeds = {row['id'] for row in direct if row.get('kind') == 'entity'}
    seeds.update(row['id'] for row in entities
                 if row.get('text') and row['text'] in request['query'])
    seed_ids = set(seeds)
    found = {}
    frontier = set(seeds)
    for _ in range(min(request.get('hops', 2), 3)):
        next_nodes = set()
        for edge in relations:
            if len(found) >= 200:
                break
            if frontier.intersection((edge['subject_id'], edge['object_id'])):
                found[edge['id']] = {
                    key: value for key, value in edge.items()
                    if key != 'embedding'}
                next_nodes.update((edge['subject_id'], edge['object_id']))
        frontier = next_nodes - seeds
        seeds.update(next_nodes)
    reasoning_paths = _reasoning_paths(
        seed_ids, relations, min(request.get('hops', 2), 3))
    labels = {row['id']: row['text'] for row in entities}
    for path in reasoning_paths:
        path['start_label'] = labels.get(path['start'], path['start'])
        path['end_label'] = labels.get(path['end'], path['end'])
        for step in path['steps']:
            step['from_label'] = labels.get(step['from'], step['from'])
            step['to_label'] = labels.get(step['to'], step['to'])
    unique = {row['id']: row for row in direct}
    unique.update({row['id']: row for row in context_chunks})
    unique.update(found)
    service.annotate_structure_pending(p, list(unique.values()))
    formal, pending = structure_pending.split(list(unique.values()))
    formal, unknown_formal = split_unknown_scope(formal)
    pending, unknown_pending = split_unknown_scope(pending)
    return {
        'hits': direct, 'context_chunks': context_chunks,
        'evidence_rows': formal, 'structure_pending_rows': pending,
        'unknown_scope_rows': [*unknown_formal, *unknown_pending],
        'candidate_count': result.get('candidate_count', 0),
        'reasoning_paths': reasoning_paths, 'filter_stage': 'before_ranking',
        'embedding_model': service.encoder.identity,
        'semantic': service.encoder.semantic,
        'requested_mode': request.get('retrieval_mode', 'hybrid'),
        'active_modes': [result.get('active_mode', 'hybrid')],
        'degraded': result.get('degraded', False),
        '_backend': result.get('_backend'),
        'retrieval_errors': {
            name: info.get('error') for name, info in result.get('backends', {}).items()
            if info.get('error')},
        'channels': {
            'entity': sum(row.get('kind') == 'entity' for row in direct),
            'chunk': sum(row.get('kind') == 'chunk' for row in direct),
            'graph_evidence': len(found)},
        'applicability': result.get('applicability'),
        'valid_at': request.get('valid_at'),
        'known_at': request.get('known_at'),
    }


def stream_events(service,p,request):
    def event(name,payload):return 'event: '+name+'\ndata: '+json.dumps(payload,ensure_ascii=False)+'\n\n'
    provenance=ProvenanceService(service.repository)
    run_id=answer_id=None
    logger=logging.getLogger('knowledge_service.qa')

    def finish_running(status,public_error):
        activity_id=answer_id or run_id
        if activity_id is None:return
        try:
            if service.repository.get_provenance_activity(p,activity_id)['status']=='running':
                provenance.fail_activity(p,activity_id,status,public_error)
        except Exception:
            # A cleanup failure must not replace the original stream error or cancellation.
            logger.exception('knowledge问答溯源终态保存失败')

    try:
        run_id=provenance.begin_retrieval(p,request)
        context=question_context(service,p,request)
        peer_rows=[{'citation':f'P{i+1}',**r} for i,r in enumerate(context.pop('structure_pending_rows',[]))]
        evidence=[{'citation':f'E{i+1}',**r} for i,r in enumerate(context.pop('evidence_rows'))]
        # 结构待定单独记一份（不是证据，也不进证据链）：别人问"这条为什么没进答案"时有据可查。
        context['structure_pending']=pending_block(peer_rows)
        answer_id=provenance.complete_retrieval(p,run_id,context,evidence)
        from .answer_evidence import AnswerEvidence
        evidence=AnswerEvidence(provenance).summaries(p,answer_id,evidence)
        yield event('evidence',{**context,'evidence':evidence,
                               'answer_id':answer_id,'retrieval_run_id':run_id})
        answer='';mode='evidence_only'
        if request.get('generate') and evidence:
            url=os.getenv('KG_LLM_BASE_URL','').rstrip('/');key=os.getenv('KG_LLM_API_KEY','');model=os.getenv('KG_LLM_MODEL','')
            if not all((url,key,model)):raise RuntimeError('请配置 KG_LLM_BASE_URL、KG_LLM_API_KEY、KG_LLM_MODEL')
            messages=answer_messages(request['query'], evidence)
            with external_client(180) as client:
                with client.stream('POST',url+'/chat/completions',headers={'Authorization':'Bearer '+key},json={'model':model,'messages':messages,'stream':True}) as response:
                    response.raise_for_status()
                    for line in response.iter_lines():
                        if not line.startswith('data:'):continue
                        data=line[5:].strip()
                        if data=='[DONE]':break
                        item=json.loads(data);choices=item.get('choices',[])
                        content=choices[0].get('delta',{}).get('content','') if choices else ''
                        if isinstance(content,str) and content:
                            answer+=content;yield event('delta',{'text':content})
            if not answer:raise RuntimeError('模型未返回任何回答文本')
            mode='llm'
        else:
            for row in evidence:
                content=f"[{row['citation']}] {row['text']}\n\n";answer+=content;yield event('delta',{'text':content})
            if not evidence:
                # 有知识、但它们涉概念还没建模：**显式说出来**，不要用"没有证据"盖过去 ——
                # 用户看到的差别是"真没有"和"有但不算数"。
                if context['structure_pending']['count']:
                    answer=no_evidence_text(context['structure_pending'])
                else:
                    answer='当前查询范围没有证据。'
                yield event('delta',{'text':answer})
        provenance.complete_answer(p,answer_id,run_id,answer,mode,evidence)
        yield event('done',{'answer':answer,'mode':mode,'answer_id':answer_id,
                           'retrieval_run_id':run_id,'provenance_complete':True})
    except (GeneratorExit,asyncio.CancelledError) as exc:
        finish_running('cancelled',{'type':type(exc).__name__,'message':'回答流已取消。'})
        raise
    except Exception as exc:
        # 把完整 traceback 打到服务日志，便于定位（原来只返回类型名，TypeError 无处查）
        logger.exception('knowledge问答流异常: %s', exc)
        # Exception text may contain provider credentials or raw response bodies.
        # Persist only a fixed public summary and the exception type; keep SSE compatibility.
        finish_running('failed',{'type':type(exc).__name__,'message':'回答不可用：'+type(exc).__name__})
        yield event('error',{'detail':str(exc) if isinstance(exc,(ValueError,RuntimeError)) else '回答不可用：'+type(exc).__name__})
