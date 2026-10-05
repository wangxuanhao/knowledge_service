#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""阶段门禁真机复验：确认服务端算出的"哪个阶段能进"**没有撒谎**（A3 状态迁移后的版本）。

A3（状态 7 → 3）之后这条规则换了地基，本脚本跟着改的是**判据**，不是断言强度：
    · 请求只有三种状态：pending（待处理）/ accepted（已收下）/ rejected（已驳回）。
      旧词汇（editing / submitted / reviewed / published / closed / stale_base /
      stale_source）一旦在真库里还读得到，直接判 FAIL —— 那是迁移没跑干净。
    · 「已提交 / 全部有决定 / 需要重新基线」不再是状态，而是服务端**当场推导**的：
      pending_phase ∈ {editing, reviewing, settled}、needs_rebase ∈ {null, stale_base, stale_source}。
      推导字段缺失（null）同样判 FAIL：状态收窄之后它是"这份请求走到哪一步"的唯一口径。
    · 「发布」不再是阶段（收下就是发布）：阶段表里出现 publish 一律 FAIL。

为什么需要这个脚本（而不是只靠 pytest）：
    `stage_state()` 是阶段门禁的唯一判据，前端只渲染它、自己不推规则。
    单测用的是内存里的假仓库，跑在 `knowledge_test` 库上；真机上的草案来自真实的
    发现/提交/审核/发布流程，状态分布完全不同 —— 本脚本读真数据、查真接口、
    并且**真的去撞一次写操作**来反证门禁与写守卫是同一套口径。

它守的具体缺陷（2026-10-02 修复）：
    终态草案（published / closed）曾经把「校验 / 审核 / 发布」三扇门全开
    （根因：`submitted = status in SUBMITTED_STATES`，而该集合含 published/closed），
    但门后的 `publish_preflight` 只认 reviewed —— 按钮亮着、点进去 409。
    用户看到的是"设计灰着、黄条说进不去、审核/发布却亮着"。

安全性：本脚本**只做只读查询 + 一次预期会被拒绝的写入尝试**。
    写入尝试只对终态草案发出（终态必然被状态守卫拒绝），不会产生新版本。
    若某条终态草案居然写出了新版本，脚本会明确报 FAIL 并打印出来。

用法：
    python scripts/verify_ontology_gate.py                    # 自动挑草案最多的项目
    python scripts/verify_ontology_gate.py --project <uuid>   # 指定项目
    python scripts/verify_ontology_gate.py --url http://127.0.0.1:8000

退出码：0 = 全部通过；1 = 有失败项；2 = 连接/参数问题。
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
import uuid

# 本机代理会把 127.0.0.1 的请求也劫持走，必须显式绕过（与 tests 的约定一致）。
os.environ.setdefault('NO_PROXY', '127.0.0.1,localhost')

# 与服务端 services/ontology_drafts.py 保持一致：终态 = 只读账本。
FINAL_STATES = {'accepted', 'rejected'}
# A3 之前存在、之后**不该再出现**的旧状态：读到任何一个都说明迁移没跑干净。
LEGACY_STATES = {'editing', 'submitted', 'reviewed', 'published', 'closed',
                 'stale_base', 'stale_source'}
# 非终态只有这一种。
PENDING_STATES = {'pending'}
# 「待处理」的三个细分处境（服务端推导；见 services/ontology_drafts.pending_phase）。
PENDING_PHASES = {'editing', 'reviewing', 'settled'}
# 推导字段的合法取值。
REBASE_KINDS = {None, 'stale_base', 'stale_source'}
# A3：发布不是阶段 —— 这几个 key 是收窄前的老形状，出现即 FAIL。
RETIRED_STAGES = {'validate', 'publish'}
# 可见文案：收下 / 驳回（用户口径里没有"已发布/已关闭"这两个状态词了）。
STATUS_LABELS = {'pending': '待处理', 'accepted': '已收下', 'rejected': '已驳回'}
# 终态上唯一允许进入的阶段（它是下一轮的入口）。
# 其余阶段不写死名单 —— 见 check_expected_rule：流程合并只会改服务端返回的阶段数。
ONLY_ALLOWED_WHEN_FINAL = 'discover'
# 终态的理由必须指向能真正干活的地方。要求「至少出现一个」而不是「两个都要」：
# 「已发布/已关闭」这一支会同时指向本体档案与本体设计台；
# 「选中了终态草案但项目里还有别的编辑中草案」这一支只该指向本体设计台（用户这时不是在找历史版本）。
FINAL_REASON_HINTS = ('本体档案', '本体编辑台')


class Failure(Exception):
    """一条断言失败。用异常而不是 assert，保证 python -O 下也照样检查。"""


def request(base: str, path: str, payload=None, method: str | None = None):
    """打一个接口。返回 (status_code, body)；HTTP 错误也当正常结果返回，由调用方判断。"""
    data = None
    headers = {'Accept': 'application/json'}
    if payload is not None:
        data = json.dumps(payload).encode('utf-8')
        headers['Content-Type'] = 'application/json'
    req = urllib.request.Request(base + path, data=data, headers=headers,
                                 method=method or ('POST' if data else 'GET'))
    try:
        with urllib.request.urlopen(req, timeout=20) as response:
            raw = response.read().decode('utf-8')
            return response.status, (json.loads(raw) if raw else None)
    except urllib.error.HTTPError as error:
        raw = error.read().decode('utf-8')
        try:
            return error.code, json.loads(raw) if raw else None
        except json.JSONDecodeError:
            return error.code, raw
    except urllib.error.URLError as error:
        raise Failure(f'连不上 {base}：{error.reason}') from error


def pick_project(base: str, wanted: str | None) -> str:
    """挑一个草案最多的项目：真机上"有终态草案"的项目才验得出这条规则。"""
    _, body = request(base, '/api/projects')
    projects = (body or {}).get('projects') or []
    if not projects:
        raise Failure('这台服务上一个项目都没有，无法复验')
    if wanted:
        if not any(item['id'] == wanted for item in projects):
            raise Failure(f'项目 {wanted} 不存在')
        return wanted
    best, best_count = None, -1
    for item in projects:
        _, drafts = request(base, f"/api/projects/{item['id']}/ontology-drafts")
        count = len((drafts or {}).get('items') or [])
        if count > best_count:
            best, best_count = item['id'], count
    return best


def check_expected_rule(status: str, availability: dict) -> list[str]:
    """按状态给出"应该是什么样"，再和真机返回逐条比。返回问题列表（空 = 通过）。

    阶段名字**不写死**：服务端返回几个阶段就检查几个。流程合并（例如「校验」并入
    「审核」）只该改服务端，不该迫使这个脚本跟着改 —— 脚本要守的是语义，
    不是"一共有五扇门"。
    """
    problems = []
    stages = availability.get('stages') or {}

    # ── 每份草案都要过的共同检查（A3 新地基）────────────────────────────
    if status in LEGACY_STATES:
        problems.append(f'读到了旧状态 {status!r}：A3 之后只允许 pending/accepted/rejected，'
                        '说明 0005 迁移没覆盖到这份数据')
    elif status not in PENDING_STATES | FINAL_STATES:
        problems.append(f'未知状态 {status!r}：状态词表只有三种')
    for retired in sorted(RETIRED_STAGES & set(stages)):
        problems.append(f'阶段表里还有旧阶段「{retired}」：A3 之后发布不是阶段（收下即发布）')
    label = availability.get('status_label')
    if status in STATUS_LABELS and label != STATUS_LABELS[status]:
        problems.append(f'状态文案对不上：{status} 应当显示「{STATUS_LABELS[status]}」，'
                        f'实际 {label!r}')

    phase = availability.get('pending_phase')
    rebase = availability.get('needs_rebase')
    if status in FINAL_STATES:
        # 终态的推导字段必须是 null：它们描述的是"还能怎么走"，终态已经没有下一段路了。
        if phase is not None:
            problems.append(f'终态草案不该有 pending_phase（读到 {phase!r}）')
        if rebase is not None:
            problems.append(f'终态草案不该有 needs_rebase（读到 {rebase!r}）')
    elif status in PENDING_STATES:
        # 状态收窄后，"这份请求走到哪一步"只有推导字段这一个口径 —— 缺了就是口径丢了。
        if phase not in PENDING_PHASES:
            problems.append(f'待处理草案的 pending_phase 缺失/非法（读到 {phase!r}）：'
                            f'它应当是 {sorted(PENDING_PHASES)} 之一')
        if rebase not in REBASE_KINDS:
            problems.append(f'needs_rebase 取值非法：{rebase!r}')
        if rebase in ('stale_base', 'stale_source') and stages.get('design', {}).get('allowed'):
            problems.append(f'过期草案（{rebase}）不该让人直接进设计，应先重新基线/刷新来源')

    if ONLY_ALLOWED_WHEN_FINAL not in stages:
        problems.append(f'返回里没有「{ONLY_ALLOWED_WHEN_FINAL}」阶段：它必须是每个终态草案的出口')
    elif not stages[ONLY_ALLOWED_WHEN_FINAL].get('allowed'):
        problems.append(f'终态草案的「{ONLY_ALLOWED_WHEN_FINAL}」应当可进（它是下一轮的入口）')

    if status in FINAL_STATES:
        # 终态＝只读账本：除了「发现」，**其余每一扇门都必须锁**，且都必须写明去哪。
        # 这里逐门检查而不是列固定名单，所以将来加阶段也自动被覆盖。
        for name, entry_ in stages.items():
            if name == ONLY_ALLOWED_WHEN_FINAL:
                continue
            if entry_.get('allowed'):
                # 这一条就是本轮修的缺陷，写清楚"为什么这是错的"
                problems.append(
                    f'终态草案的「{name}」不该可进：门后的写操作只认「待处理」，'
                    '放行等于让用户点进去撞拒绝')
            reason = entry_.get('reason') or ''
            if not reason:
                problems.append(f'终态草案的「{name}」锁住了但没写原因，用户不知道下一步去哪')
                continue
            if not any(hint in reason for hint in FINAL_REASON_HINTS):
                problems.append(
                    f'终态草案的「{name}」原因没指向能干活的地方'
                    f'（要出现「本体档案」或「本体编辑台」之一）：{reason[:60]}…')
        return problems

    # 待处理：三扇门开着不算撒谎（收件箱那一页按 pending_phase 自己分工）。
    # 真正要验的是"服务端给的下一步与推导阶段一致"—— 这既是门禁的口径，也是状态条上
    # 那唯一一个按钮的出处；两者不一致就说明"门禁"和"下一步"是两套规则。
    if status in PENDING_STATES:
        if not stages.get(ONLY_ALLOWED_WHEN_FINAL, {}).get('allowed'):
            problems.append('待处理草案的「发现」应当可进（它是收下一轮候选的入口）')
        if rebase in ('stale_base', 'stale_source'):
            expect_kind, expect_stage = ('rebase', None) if rebase == 'stale_base' else ('reload', None)
            if stages.get('review', {}).get('allowed'):
                problems.append(f'需要重新基线的草案（{rebase}）不该让人直接进收件箱逐条处理')
        elif phase == 'editing':
            expect_kind, expect_stage = 'design', 'review'
            if not stages.get('design', {}).get('allowed'):
                problems.append('还没提交的待处理草案应当能进「设计」')
        elif phase == 'reviewing':
            expect_kind, expect_stage = 'review', 'review'
        else:  # settled
            expect_kind, expect_stage = 'publish', 'review'
        if status in PENDING_STATES and phase in ('reviewing', 'settled'):
            if not stages.get('review', {}).get('allowed'):
                problems.append(f'已提交（pending_phase={phase}）的草案应当能进「收件箱」')
        action = availability.get('next_action') or {}
        if action.get('kind') != expect_kind:
            problems.append(
                f'下一步与推导阶段对不上：pending_phase={phase!r} 应当给 kind={expect_kind!r}，'
                f'实际 {action.get("kind")!r}（label={action.get("label")!r}）')
        if expect_stage is not None and action.get('stage') not in (None, expect_stage):
            problems.append(f'下一步的 stage 指向 {action.get("stage")!r}，期望 {expect_stage!r}')
        return problems

    return problems


def prove_the_gate_does_not_lie(base: str, project: str, draft: dict) -> list[str]:
    """反证：门禁锁着的时候，写操作必须真的被拒绝。

    只对终态草案做 —— 终态必然被 `publish_preflight` 的状态守卫拒绝，
    不会产生新版本；如果居然成功了，说明门禁和写守卫两套口径，必须报错。
    """
    problems = []
    if draft['status'] not in FINAL_STATES:
        return problems
    _, current = request(base, f"/api/projects/{project}/ontology-drafts/{draft['id']}")
    revision = ((current or {}).get('draft') or current or {}).get('revision')
    base_ontology = draft.get('base_ontology_id')
    code, body = request(
        base,
        f"/api/projects/{project}/ontology-drafts/{draft['id']}/publish",
        payload={
            'expected_revision': revision,
            'expected_ontology_id': base_ontology,
            # 必须给字符串：PublishRequest.validation_fingerprint 是必填 str，
            # 传 None 会被 FastAPI 挡在 422，**请求根本到不了状态守卫**，
            # 于是反证拿不到 409、证明不了任何东西（第一版脚本就踩了这个坑）。
            'validation_fingerprint': draft.get('validation_fingerprint') or '',
            'acknowledged_warning_codes': [],
            # 每次都换新键：否则会被"同一把键重放"那条路径吸收，测不到状态守卫。
            'idempotency_key': f'gate-verify-{uuid.uuid4()}',
            'actor': 'verify-ontology-gate@script',
        })
    if code == 200:
        problems.append(
            '门禁说谎：终态草案的发布被放行了，真的产生了新版本。'
            f'请立即检查 publish_preflight 的状态守卫（返回 {str(body)[:120]}）')
        return problems

    # 只看"被拒了"还不够 —— 必须**是被状态守卫拒的**。
    # `api/__init__.py:199` 把 OntologyDraftError 里 revision/stale/validation 那几类
    # 映射成 409，其余（含 draft_immutable）映射成 422。所以这里判的是 code，
    # 不是 HTTP 码：请求被 FastAPI 参数校验挡在门前（没有 code）时，这次反证等于没做。
    rejection_code = (body or {}).get('code') if isinstance(body, dict) else None
    if rejection_code != 'draft_immutable':
        problems.append(
            f'终态草案的发布被拒了，但不是被状态守卫拒的（code={rejection_code!r}，HTTP {code}）：'
            f'这说明请求没走到状态守卫，本次没有验证到门禁。返回 {str(body)[:160]}')
    return problems


def main() -> int:
    parser = argparse.ArgumentParser(description='本体阶段门禁真机复验')
    parser.add_argument('--url', default='http://127.0.0.1:8000', help='服务地址')
    parser.add_argument('--project', default=None, help='指定项目 uuid（默认挑草案最多的）')
    parser.add_argument('--no-write-probe', action='store_true',
                        help='跳过"真去撞一次写操作"的反证（只做只读检查）')
    args = parser.parse_args()
    base = args.url.rstrip('/')

    try:
        project = pick_project(base, args.project)
    except Failure as error:
        print(f'[无法开始] {error}', file=sys.stderr)
        return 2

    _, listing = request(base, f'/api/projects/{project}/ontology-drafts')
    drafts = (listing or {}).get('items') or []
    print(f'项目 {project} · 共 {len(drafts)} 个草案')
    print('-' * 78)

    failures: list[str] = []
    counts: dict[str, int] = {}
    phases: dict[str, int] = {}

    for draft in drafts:
        status = draft.get('status') or '?'
        counts[status] = counts.get(status, 0) + 1
        _, availability = request(
            base, f"/api/projects/{project}/ontology-drafts/stage-availability"
                  f"?draft_id={draft['id']}")
        if not availability:
            failures.append(f'{draft["id"][:8]} 读不到 stage-availability')
            continue

        # 待处理的草案把"走到哪一步"记一笔：三种细分的样本都出现过，才算真验到了推导逻辑。
        if availability.get('pending_phase'):
            key = availability['pending_phase']
            phases[key] = phases.get(key, 0) + 1
        problems = check_expected_rule(status, availability)
        problems += prove_the_gate_does_not_lie(base, project, draft) \
            if not args.no_write_probe else []

        mark = 'PASS' if not problems else 'FAIL'
        stages = availability.get('stages') or {}
        # 门禁概览按**服务端实际返回的阶段**渲染，不写死名字：
        # 第一版这里硬编码了 5 个名字，于是在 4 阶段的合并版本上打出一排假的 validate✘。
        gate = ' '.join(
            f"{name}{'✔' if stages.get(name, {}).get('allowed') else '✘'}"
            for name in stages) or '（服务端没返回阶段）'
        print(f'[{mark}] {draft["id"][:8]} status={status:<12} 门禁 {gate}')
        if problems:
            for line in problems:
                print(f'        · {line}')
            failures.extend(problems)

    print('-' * 78)
    print('状态分布：' + '、'.join(f'{k}×{v}' for k, v in sorted(counts.items())) or '（无草案）')
    print('推导阶段：' + ('、'.join(f'{k}×{v}' for k, v in sorted(phases.items()))
                        or '（没有待处理草案）'))

    # 样本覆盖度要说清楚：缺哪一类，这次复验就少验了一块。
    if not any(status in FINAL_STATES for status in counts):
        print('[提醒] 这个项目里没有终态（已收下/已驳回）草案 —— 本次没验到终态那条规则。')
        print('        用 --project 指定一个有已收下草案的项目，或先跑一轮完整流程。')
    if not phases:
        print('[提醒] 这个项目里没有待处理草案 —— 推导阶段（editing/reviewing/settled）没验到。')

    if failures:
        print(f'\n结果：FAIL —— {len(failures)} 条问题')
        return 1
    print('\n结果：PASS —— 门禁与写守卫口径一致，没有放行不该放行的阶段。')
    return 0


if __name__ == '__main__':
    sys.exit(main())
