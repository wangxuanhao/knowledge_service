# -*- coding: utf-8 -*-
"""F2「待审候选的受理决策」端到端复验（真调 LLM，临时项目自建自删）。

为什么要有这个脚本：F1 主脚本用的样本与本体词汇高度对齐，抽取结果**全部直接映射入账**，
`/reviews` 里一条待审候选都没有，"收下/驳回"这半步从来没被真正执行过（那边只如实标注跳过）。
本脚本专门用一段**跨域文本**把待审候选压出来，验证受理决策这一整条路径。

实测发现的三类候选与它们的**正确处理方式**（这条是脚本要固化下来的行为契约）：

  · 实体类型未定义/歧义   → 属 C5 分流：去本体建模层把概念收下再发布，**不在台账层收下**；
  · 关系谓词未定义/歧义   → 同上；
  · 关系两端不符合 domain/range → 会被 `approve` 明确拒绝（422 且给出精确原因），
                                   正确处理是**驳回**或先改本体。收下会让图谱违反本体约束。

所以本脚本的判据不是"必须收下成功"，而是：**候选能通过受理接口被决策，且决策后不再 pending**，
同时把 `approve` 被本体拒绝时的真实原因原样打出来（便于人工判断该改本体还是该驳回）。
"""
from __future__ import annotations

import json
import sys
import time
from datetime import datetime

sys.path.insert(0, 'scripts')
from verify_ingest_end_to_end import as_list, req, resolve_target  # noqa: E402

# 刻意跨域：里面的事物（采购合同/供应商/审批人/付款计划）在默认本体（平台/商户/违规/处罚那套）
# 里没有对应类，抽取器只能把实体硬映射到最近似的类型 —— 于是谓词解析得到、两端类型却对不上
# domain/range，稳定落进"待审候选"。
CONFLICT_TEXT = (
    '采购合同的履约管理说明。\n\n'
    '采购合同由供应商与采购方共同签署，合同里包含若干条款，每个条款都有生效日期与'
    '违约责任说明。合同审批由审批人负责，审批人隶属于采购部门。\n\n'
    '付款计划挂在采购合同之下，付款计划的每一期对应一个到期日与一个金额。'
    '若供应商逾期交付，采购方可发起索赔，索赔需要引用对应的合同条款。\n\n'
    '本合同管理流程还涉及验收单：验收单由采购部门填写，验收单用于确认供应商的交付结果。'
)

BASE = 'http://127.0.0.1:8100'
_results: list[tuple[bool, str, str]] = []


def ok(passed: bool, name: str, detail: str = '') -> bool:
    _results.append((bool(passed), name, detail))
    print(f'  {"✔" if passed else "✘"} {name}' + (f'   [{detail}]' if detail else ''), flush=True)
    return bool(passed)


def main() -> int:
    parser = __import__('argparse').ArgumentParser(description='F2 待审候选受理决策复验（真调 LLM）')
    parser.add_argument('--url', default=BASE, help='服务地址')
    parser.add_argument('--keep', action='store_true', help='跑完保留临时项目（排查用）')
    parser.add_argument('--timeout', type=int, default=420, help='等抽取完成的最长秒数')
    args = parser.parse_args()
    base = args.url.rstrip('/')

    stamp = datetime.now().strftime('%m%d-%H%M%S')
    name = f'F2受理决策复验·{stamp}'
    print(f'== F2「待审候选受理决策」端到端复验 · {base} ==', flush=True)
    print(f'   临时项目「{name}」', flush=True)

    code, health = req(base, '/api/health')
    if code != 200:
        print(f'✘ 服务没起（{base}/api/health -> {code}）')
        return 2
    code, created = req(base, '/api/projects', {'name': name, 'use_default_ontology': True})
    if code not in (200, 201):
        print(f'✘ 建临时项目失败：{code}')
        return 2
    project_id = created.get('id') or (created.get('project') or {}).get('id')
    print(f'   项目 {project_id}', flush=True)

    try:
        code, job = req(base, f'/api/projects/{project_id}/documents/jobs', {
            'title': f'F2 受理决策样本 {stamp}',
            'text': CONFLICT_TEXT,
            'metadata': {'source': 'f2-verification'},
            'extract': True,
            'extraction_mode': 'ontology',
            'chunk_strategy': 'paragraph',
            'chunk_size': 800,
            'chunk_overlap': 100,
            'resolve_entities': True,
            'auto_merge': False,
            'merge_threshold': 0.88,
        })
        ok(code in (200, 201, 202), '① 文档已提交并触发抽取', f'HTTP {code}')
        if code not in (200, 201, 202):
            return 1

        deadline = time.monotonic() + args.timeout
        final = None
        while time.monotonic() < deadline:
            code, runs = req(base, f'/api/projects/{project_id}/ingest-runs')
            items = as_list(runs, 'runs', 'items')
            final = next((r for r in items if r.get('id') == job.get('id')),
                         items[-1] if items else None)
            if final and final.get('status') in ('completed', 'failed', 'interrupted'):
                break
            time.sleep(5)
        counts = (final or {}).get('counts') or {}
        ok((final or {}).get('status') == 'completed', '② 抽取跑到 completed',
           f'入账 {counts.get("records_accepted")} · 待审 {counts.get("assertions_pending")}')

        code, body = req(base, f'/api/projects/{project_id}/reviews')
        reviews = as_list(body, 'reviews', 'items')
        ok(bool(reviews), '③ 抽取留下了待审候选（跨域文本触发）', f'{len(reviews)} 条')
        if not reviews:
            # LLM 结果有波动 —— 如实区分"功能坏了"与"本轮没抽到"，不伪装通过。
            print('   ⚠ 本轮没抽到待审候选，受理决策未被触发（LLM 结果波动）')
            return 3
        for row in reviews[:6]:
            print(f'   · kind={row.get("kind")} predicate={row.get("predicate")} '
                  f'proposed_type={str(row.get("proposed_type")).split("#")[-1]} '
                  f'reason={row.get("reason")}')

        code, ontology = req(base, f'/api/projects/{project_id}/ontology')
        summary = (ontology or {}).get('summary') if isinstance(ontology, dict) else None
        ontology_id = (ontology or {}).get('id')

        # ④ 逐条走受理：先试收下，被本体拒绝就驳回 —— 两条路都必须让候选离开 pending。
        # 关键：**每决定一条就要重读一次文档版本**。决定会推进来源文档的版本号
        # （`reviews.py:162` 的乐观并发校验），拿循环开头那份快照去决定第二条必然 409
        # 「版本冲突：请刷新审核清单」—— 看起来像"第二条候选驳不动"，实际是客户端没重读。
        decided_rows: list[tuple[str, str]] = []
        for target in reviews:
            doc_id, cand_id = target.get('document_id'), target.get('id')
            code, fresh_body = req(base, f'/api/projects/{project_id}/reviews')
            fresh = {r.get('id'): r for r in as_list(fresh_body, 'reviews', 'items')}
            current = fresh.get(cand_id) or target
            if current.get('status') not in (None, 'pending'):
                continue
            end = f'/api/projects/{project_id}/reviews/{doc_id}/{cand_id}'
            resolved = resolve_target(summary, current)
            if resolved:
                payload = {'action': 'approve', 'target_type': resolved,
                           'expected_version': current.get('document_version'),
                           'expected_ontology_id': ontology_id, 'note': 'F2 复验收下'}
                for key in ('entity_version', 'attribute_versions'):
                    if current.get(key):
                        payload[f'expected_{key}'] = current[key]
                code, resp = req(base, end, payload)
                if code in (200, 201):
                    decided_rows.append(('approve', cand_id))
                    print(f'   ✔ 收下 {current.get("predicate") or current.get("proposed_type")} → HTTP {code}')
                    continue
                # 收下被拒时**原因必须精确可读**，否则人工无法判断该改本体、该先收实体、还是该驳回。
                # 实测至少两种合法拒绝：①「本体校验未通过，共 N 条；示例：subject_id 类型 X 不满足
                # domain Y」；②「关联实体尚未批准、已拒绝或已删除，请先处理实体候选」（端点实体还
                # 在待审）。两者都比"收下失败"有用得多，所以断言只看"给了具体中文原因"，
                # 不写死其中某一种（写死会让另一种真实拒绝被误判成失败）。
                detail = resp.get('detail') if isinstance(resp, dict) else str(resp)
                ok(isinstance(detail, str) and len(detail) > 6 and detail != 'null',
                   '④a 收下被拒时给出精确原因', str(detail)[:100])
            # 驳回同样要带 expected_version：版本冲突校验在 action 分支之前
            # （`reviews.py:162`），少了它一律 422，看起来像"驳回没有实现"。
            code, resp = req(base, end, {'action': 'reject',
                                         'expected_version': current.get('document_version'),
                                         'note': 'F2 复验驳回'})
            ok(code in (200, 201), '④b 违规候选可以驳回', f'HTTP {code}')
            if code in (200, 201):
                decided_rows.append(('reject', cand_id))

        # ⑤ 决策真的生效：被决策的候选不再是 pending
        code, after = req(base, f'/api/projects/{project_id}/reviews')
        rows = as_list(after, 'reviews', 'items')
        by_id = {r.get('id'): r for r in rows}
        stuck = [cid for _, cid in decided_rows
                 if (by_id.get(cid) or {}).get('status') in (None, 'pending')]
        ok(bool(decided_rows) and not stuck, '⑤ 被决策的候选不再 pending',
           f'已决策 {len(decided_rows)} 条 · 仍滞留 {len(stuck)} 条')
    except Exception as error:                       # noqa: BLE001 —— 复验脚本兜住一切并给结论
        import traceback
        traceback.print_exc()
        ok(False, '复验过程未抛异常', str(error))
    finally:
        if args.keep:
            print(f'    · 保留临时项目 {project_id}（--keep）')
        else:
            code, _ = req(base, f'/api/projects/{project_id}', {'confirm': name}, 'DELETE')
            print(f'    · 已删除临时项目 {project_id}（HTTP {code}）')

    failed = [n for p, n, _ in _results if not p]
    print(f'\n共 {len(_results)} 项：通过 {len(_results) - len(failed)}，失败 {len(failed)}')
    if failed:
        print('失败项：' + '；'.join(failed))
        return 1
    print('F2「待审候选受理决策」端到端复验全部通过。')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
