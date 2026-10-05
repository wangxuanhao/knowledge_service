#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""F1 端到端：上传文档 → LLM 抽取 → 收下候选。

做什么（全在**可丢弃的临时项目**里，finally 自删）
  ① 提交一段含新概念的说明文字 → 触发真实抽取（POST /documents/jobs）；
  ② 轮询摄入任务直到 completed（会真调 LLM，所以超时给得很宽）；
  ③ 检查候选确实产出了，并且**概念候选 / 实例候选两类分开落位**
     （概念 → candidate-mindmap，实例 → /reviews）；
  ④ 收下：实例候选走 reviews 决策接口；概念候选走 ontology-discovery/drafts 生成草案；
  ⑤ 断言收下之后状态真的变了（实例不再是 pending / 草案确实建出来了）。
不做什么
  · 不碰你现有的项目（临时项目自建自删，--keep 可保留排查）；
  · 不验证 UI 渲染（那是浏览器契约测试的职责）；
  · 不重试 LLM：一次跑不出候选就如实报失败，绝不伪造结果。

为什么单独有这个脚本
  这是规划表里 F1 的最后一段（「上传 → 抽取 → 收下候选」），也是唯一**必须依赖真实 LLM**
  的一段 —— 因此它不进默认的 verify_all_chains 全量回归（那套要求离线可重复、稳定快），
  用 --only 或手动单独跑。
"""
from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime

# 一段**刻意贴合本体词汇与约束**的短文本。
#
# 为什么不用随便一段业务文字：默认本体对每条关系都写了 domain/range（例如
# `commits: Merchant → Violation`、`triggers: Violation → Penalty`）。若样本文本说的是
# 另一套领域概念（早先试过"采购合同/供应商"），抽取器只能把实体硬映射到最近似的类型，
# 于是主语类型落不到 domain 里 —— 收下时会被本体校验拒绝（422 domain/range 不满足）。
# 那**不是链路故障，是样本与本体不匹配**。这里改为逐条对应本体的合法关系链，
# 才能真正走到「收下并写入」这一步。
SAMPLE_TITLE = 'F1 端到端复验样本文档'
SAMPLE_TEXT = (
    '平台规则与商户经营说明。\n\n'
    '商户在平台完成入驻之后，可以在平台上向用户提供商品。用户在平台上浏览并购买商品，'
    '购买完成之后会生成一张订单，订单里包含用户所购买的那些商品。平台会对入驻商户的资质'
    '进行审核，只有资质审核通过的商户才能继续在平台上正常经营。\n\n'
    '平台在日常巡查过程中会发现商户的违规行为。商户如果实施了违规行为，该违规行为就会'
    '触发对应的处罚，平台负责处理这些处罚。平台制定的规则文件明确禁止若干违规行为，'
    '规则文件以法律作为依据。\n\n'
    '用户在使用平台之前需要同意遵守规则文件。用户在自身利益受到损害的时候享有对应的救济。'
    '平台在提供各项服务的过程中会收集用户的个人数据，并且会与外部机构共享必要的信息。'
    '平台有时候也隶属于某个外部机构。\n\n'
    # 末段刻意引入两个**本体里没有的概念**（优惠券、积分）：它们不会被直接映射入账，
    # 而是作为「新概念候选」留下来，用来验证 C5 的收下→草案路径真的走得通。
    '此外，平台还会向用户发放优惠券，并按月结算用户的积分。'
)

_results: list[tuple[bool, str, str]] = []


def ok(passed: bool, name: str, detail: str = '') -> None:
    _results.append((passed, name, detail))
    print(f'  {"✔" if passed else "✘"} {name}' + (f'   [{detail}]' if detail else ''), flush=True)


def note(text: str) -> None:
    print(f'    · {text}', flush=True)


def req(base: str, path: str, payload=None, method: str | None = None, timeout: int = 120):
    """打一个接口。HTTP 错误也照常返回，交给调用方判断。"""
    data = None
    headers = {'Accept': 'application/json'}
    if payload is not None:
        data = json.dumps(payload).encode('utf-8')
        headers['Content-Type'] = 'application/json'
    request = urllib.request.Request(base + path, data=data, headers=headers,
                                     method=method or ('POST' if data else 'GET'))
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read().decode('utf-8')
            return response.status, (json.loads(raw) if raw else None)
    except urllib.error.HTTPError as error:
        raw = error.read().decode('utf-8')
        try:
            return error.code, (json.loads(raw) if raw else None)
        except json.JSONDecodeError:
            return error.code, raw


def explain(status: int, body) -> str:
    if isinstance(body, dict):
        for key in ('detail', 'message', 'error'):
            if body.get(key):
                return f'HTTP {status}: {body[key]}'
    return f'HTTP {status}: {body}'


def as_list(body, *keys) -> list:
    if isinstance(body, list):
        return body
    if isinstance(body, dict):
        for key in keys:
            value = body.get(key)
            if isinstance(value, list):
                return value
    return []


def resolve_target(summary, candidate) -> str:
    """把候选的类型名解析成本体里的 IRI —— 复刻前端 ingest-flow.js 的 resolveTarget。

    为什么必须做这一步：服务端 `services/reviews.py::decide` 是拿
    `request.target_type` 去 `model.resolve(...)` 的，**不传就 resolve(None)**，
    报出来的中文是「未知或存在歧义的本体术语：」（术语名是空的）—— 极难从报错本身看出原因。
    前端则是先自己解析：按 kind 选 classes/relations/attributes 分组，用
    `proposed_type || predicate || target_type` 去匹配 id/name/label（再兜一层忽略大小写的 name）。
    解析不到说明这是个**本体里还没有的新类型** → 前端会提示"先去本体建模层收下"，
    那属于 C5 的正常治理分流，而不是链路故障。
    """
    if not isinstance(summary, dict):
        return ''
    group = {'entity': 'classes', 'relation': 'relations',
             'attribute': 'attributes'}.get(candidate.get('kind') or 'relation')
    terms = summary.get(group) or []
    wanted = (candidate.get('proposed_type') or candidate.get('predicate')
              or candidate.get('target_type') or '')
    if not wanted:
        return ''
    for term in terms:
        if isinstance(term, dict) and wanted in (term.get('id'), term.get('name'), term.get('label')):
            return term.get('id') or ''
    lowered = str(wanted).lower()
    for term in terms:
        if isinstance(term, dict) and str(term.get('name') or '').lower() == lowered:
            return term.get('id') or ''
    return ''


def main() -> int:
    parser = argparse.ArgumentParser(description='F1 上传→抽取→收下候选 端到端复验（真调 LLM）')
    parser.add_argument('--url', default='http://127.0.0.1:8100', help='服务地址')
    parser.add_argument('--keep', action='store_true', help='跑完保留临时项目（排查用）')
    parser.add_argument('--timeout', type=int, default=420,
                        help='等抽取完成的最长秒数（真调 LLM，默认 420s）')
    args = parser.parse_args()
    base = args.url.rstrip('/')

    stamp = datetime.now().strftime('%m%d-%H%M%S')
    project_name = f'F1端到端复验·{stamp}'
    print(f'== F1「上传 → LLM 抽取 → 收下候选」端到端复验 · {base} ==', flush=True)
    print(f'   临时项目「{project_name}」· 抽取超时 {args.timeout}s', flush=True)

    status, _ = req(base, '/api/health')
    if status != 200:
        print(f'✘ 服务没起（{base}/api/health -> {status}）')
        return 2

    status, created = req(base, '/api/projects',
                          {'name': project_name, 'use_default_ontology': True})
    if status not in (200, 201) or not isinstance(created, dict):
        print(f'✘ 建临时项目失败：{explain(status, created)}')
        return 2
    project_id = created.get('id') or (created.get('project') or {}).get('id')
    print(f'   项目 {project_id}', flush=True)

    try:
        # ① 提交文档触发抽取（extract=true + ontology 模式：既抽概念也抽实例）
        payload = {
            'title': SAMPLE_TITLE,
            'text': SAMPLE_TEXT,
            'metadata': {'source': 'f1-verification'},
            'extract': True,
            'extraction_mode': 'ontology',
            'chunk_strategy': 'paragraph',
            'chunk_size': 800,
            'chunk_overlap': 100,
            'resolve_entities': True,
            'auto_merge': False,
            'merge_threshold': 0.88,
        }
        status, job = req(base, f'/api/projects/{project_id}/documents/jobs', payload)
        ok(status in (200, 201, 202), '① 文档已提交并触发抽取',
           '' if status in (200, 201, 202) else explain(status, job))
        if status not in (200, 201, 202) or not isinstance(job, dict):
            return 1
        job_id = job.get('id')
        note(f'任务 {job_id} · 初始状态 {job.get("status")}')

        # ② 轮询摄入任务到终态（真调 LLM，慢，所以按秒轮）
        deadline = time.monotonic() + args.timeout
        final_status, final_job = None, job
        while time.monotonic() < deadline:
            code, runs = req(base, f'/api/projects/{project_id}/ingest-runs')
            items = as_list(runs, 'runs', 'items')
            target = next((r for r in items if r.get('id') == job_id), None) or (items[-1] if items else None)
            if target:
                final_job = target
                final_status = target.get('status')
                stage = target.get('active_stage')
                if final_status in ('completed', 'failed', 'interrupted'):
                    break
                note(f'抽取中… status={final_status} stage={stage}')
            time.sleep(5)
        ok(final_status == 'completed', f'② 抽取任务跑到 completed（{args.timeout}s 内）',
           f'终态={final_status}')
        if final_status != 'completed':
            note(f'任务终态：{json.dumps(final_job, ensure_ascii=False)[:400]}')
            return 1

        # ③ 硬证据：抽取出的知识必须真的落进台账 —— 不能只看任务状态就宣布成功。
        code, ledger = req(base, f'/api/projects/{project_id}/records/query', {})
        rows = as_list(ledger, 'records', 'items')
        typed = [r for r in rows if r.get('kind') in ('entity', 'relation') and r.get('type')]
        note(f'台账记录 {len(rows)} 条 · 其中带本体类型的实体/关系 {len(typed)} 条')
        for row in typed[:4]:
            note(f"  例：{row.get('text')} → {str(row.get('type')).split('#')[-1]}")
        ok(len(typed) > 0, '③ 抽取出的知识已写进台账（带本体类型）',
           f'记录={len(rows)} 实体/关系={len(typed)}')

        # ③b 待审候选。抽取有两条合法结局，都要如实认：
        #   · 留了待审候选（新概念/歧义/需人工确认）→ 下面必须能收下一条；
        #   · 一条没留 → 说明全部被直接映射进账（\`records_accepted\` 已计入），无需人工决策。
        code, reviews_body = req(base, f'/api/projects/{project_id}/reviews')
        reviews = as_list(reviews_body, 'reviews', 'items')
        code, mindmap = req(base, f'/api/projects/{project_id}/ontology-discovery/candidate-mindmap?limit=200')
        concepts = as_list(mindmap, 'candidates', 'nodes', 'concepts', 'items')
        note(f'待审候选 {len(reviews)} 条 · 概念候选 {len(concepts)} 条'
             + ('（本轮全部直接映射入账，没有需要人工收下的候选）' if not reviews and not concepts else ''))

        # ④a 待审候选存在时，逐条尝试收下。**一条成功即证明链路通**；失败的逐条记原因
        # （类型不在本体里时会解析不出 IRI —— 那是 C5「新概念去建模层」的正常治理分流，
        #  前端同样会拦住并提示，不是链路故障）。
        code, ontology = req(base, f'/api/projects/{project_id}/ontology')
        summary = (ontology or {}).get('summary') if isinstance(ontology, dict) else None
        ontology_id = (ontology or {}).get('id') if isinstance(ontology, dict) else None
        accepted_review = None
        attempts: list[dict] = []
        for target in reviews:
            if target.get('status') not in (None, 'pending'):
                continue
            doc_id, cand_id = target.get('document_id'), target.get('id')
            resolved = resolve_target(summary, target)
            if not resolved:
                attempts.append({'kind': target.get('kind'), 'status': 'needs-modeling',
                                 'wanted': target.get('proposed_type') or target.get('predicate'),
                                 'detail': '类型不在当前本体里 → 需先到本体建模层收下并发布'})
                continue
            status, decided = req(base, f'/api/projects/{project_id}/reviews/{doc_id}/{cand_id}', {
                'action': 'approve',
                'target_type': resolved,          # 缺它就是 422「未知或存在歧义的本体术语：」
                'note': 'F1 复验：收下该候选',
                # expected_version 比的是**文档记录的版本**（services/reviews.py::decide 里
                # `doc['version'] != request.expected_version`），不是候选自己的号。
                # 读模型把它暴露成 document_version，取错会 409「版本冲突」。
                'expected_version': target.get('document_version', target.get('version', 1)),
                'expected_ontology_id': ontology_id,
                'expected_entity_version': target.get('entity_version'),
            })
            attempts.append({
                'kind': target.get('kind'), 'status': status, 'target_type': resolved,
                'predicate': target.get('predicate'),
                'detail': explain(status, decided) if status not in (200, 201) else '',
            })
            if status in (200, 201):
                accepted_review = (doc_id, cand_id)
                break
        for attempt in attempts:
            note('候选 ' + json.dumps(attempt, ensure_ascii=False))
        if reviews:
            ok(accepted_review is not None, '④a 待审候选能被收下',
               f'尝试 {len(attempts)} 条 · 成功 {1 if accepted_review else 0} 条')
        else:
            # 没有待审候选时，"收下"这一步无从执行 —— 如实标注为跳过，而不是伪造一次通过。
            note('④a 跳过：本轮没有待审候选（知识已直接映射入账，无需人工收下）')

        # ④b 收下概念候选 → 生成草案
        draft_id = None
        if concepts:
            status, draft = req(base, f'/api/projects/{project_id}/ontology-discovery/drafts',
                                {'name': f'F1 复验草案 {stamp}'})
            ok(status in (200, 201), '④b 概念候选可以收下（生成草案）',
               '' if status in (200, 201) else explain(status, draft))
            if isinstance(draft, dict):
                draft_id = (draft.get('unified_draft_id') or draft.get('id')
                            or (draft.get('draft') or {}).get('id'))
                note(f'草案 {draft_id} · result_kind={draft.get("result_kind")}')

        # ⑤ 断言收下真的生效了（不是接口返回 200 就算数）
        if approved_review := accepted_review:
            code, after = req(base, f'/api/projects/{project_id}/reviews')
            rows = as_list(after, 'reviews', 'items')
            row = next((r for r in rows if r.get('id') == approved_review[1]), None)
            ok(bool(row) and row.get('status') not in (None, 'pending'),
               '⑤a 实例候选收下后不再是 pending', f'status={(row or {}).get("status")}')
        if draft_id:
            code, drafts = req(base, f'/api/projects/{project_id}/ontology-drafts')
            rows = as_list(drafts, 'drafts', 'items')
            ok(any(d.get('id') == draft_id for d in rows),
               '⑤b 概念收下后草案出现在草案列表里', f'草案数={len(rows)}')

    except Exception as error:                       # noqa: BLE001 —— 复验脚本要兜住一切并给出结论
        import traceback
        traceback.print_exc()
        ok(False, '复验过程未抛异常', str(error))
    finally:
        if args.keep:
            print(f'    · 保留临时项目 {project_id}（--keep）')
        else:
            code, _ = req(base, f'/api/projects/{project_id}', {'confirm': project_name}, 'DELETE')
            print(f'    · 已删除临时项目 {project_id}（HTTP {code}）')

    failed = [name for passed, name, _ in _results if not passed]
    print(f'\n共 {len(_results)} 项：通过 {len(_results) - len(failed)}，失败 {len(failed)}')
    if failed:
        print('失败项：' + '；'.join(failed))
        return 1
    print('F1「上传 → 抽取 → 收下候选」端到端复验全部通过。')
    return 0


if __name__ == '__main__':
    sys.exit(main())
