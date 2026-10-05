#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""E1「标注/结构分开计量」真机复验 —— 改注释不换版本号。

做什么
  在**可丢弃的临时项目**里跑完三次发布，检查版本号的行为：
    ① 加一个类（结构变更）→ 发布 → 版本号应为 v1；
    ② 给这个类加一条注释（**只有标注变更**）→ 发布 → **版本号仍为 v1**（E1 的验收点）；
    ③ 再加一个类（结构变更）→ 发布 → 版本号变 v2。
不做什么
  · 不碰你现有的任何项目：临时项目自建、`finally` 自删（--keep 可保留排查）。
  · 不验 UI 渲染（那是浏览器契约测试的事）；这里只验服务端行为与读到的版本号。

为什么"版本号"要单独读 `metadata.version`
  版本号**不是**本体表的行数：纯标注发布会照常插一行（留痕一行都不能少），
  但它复用上一个版本号。所以行数会 +1 而版本号不变 —— 这正是 E1 要区分开的两件事。
"""
from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime

RDFS_COMMENT = 'http://www.w3.org/2000/01/rdf-schema#comment'
NS = 'https://example.org/e1#'

_results: list[tuple[bool, str, str]] = []


def ok(passed: bool, name: str, detail: str = '') -> None:
    _results.append((passed, name, detail))
    mark = '✔' if passed else '✘'
    print(f'  {mark} {name}' + (f'   [{detail}]' if detail else ''), flush=True)


def req(base: str, path: str, payload=None, method: str | None = None):
    """打一个接口，返回 (status, body)。HTTP 错误也照常返回，交给调用方判断。"""
    data = None
    headers = {'Accept': 'application/json'}
    if payload is not None:
        data = json.dumps(payload).encode('utf-8')
        headers['Content-Type'] = 'application/json'
    request = urllib.request.Request(base + path, data=data, headers=headers,
                                     method=method or ('POST' if data else 'GET'))
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
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


def current_version(base: str, project_id: str) -> int | None:
    """读项目当前本体的版本号（metadata.version；老数据回退成行序）。"""
    status, versions = req(base, f'/api/projects/{project_id}/ontologies')
    if status != 200 or not isinstance(versions, dict):
        return None
    items = versions.get('versions') or versions.get('ontologies') or []
    if not items:
        return None
    latest = items[-1]
    meta = latest.get('metadata') or {}
    if isinstance(meta.get('version'), (int, str)) and str(meta['version']).isdigit():
        return int(meta['version'])
    return len(items)          # 老项目没写 version 时的等价回退


def latest_ontology_id(base: str, project_id: str) -> str | None:
    status, body = req(base, f'/api/projects/{project_id}/ontology')
    if status != 200 or not isinstance(body, dict):
        return None
    return body.get('id')


def publish_current_draft(base: str, project_id: str, draft_id: str, *,
                          actor: str, key: str) -> tuple[bool, str, dict | None]:
    """校验 → 提交 → 一键收下 → 发布。返回 (是否成功, 说明, 发布结果)。"""
    draft_path = f'/api/projects/{project_id}/ontology-drafts/{draft_id}'
    ontology_id = latest_ontology_id(base, project_id)

    status, draft = req(base, draft_path)
    if status != 200 or not isinstance(draft, dict):
        return False, f'读草案失败：{explain(status, draft)}', None
    revision = draft.get('revision')

    status, body = req(base, draft_path + '/validate', {'expected_revision': revision})
    if status not in (200, 201):
        return False, f'校验失败：{explain(status, body)}', None

    status, draft = req(base, draft_path)
    revision = draft.get('revision') if isinstance(draft, dict) else revision
    status, body = req(base, draft_path + '/submit', {'expected_revision': revision})
    if status not in (200, 201):
        return False, f'提交审核失败：{explain(status, body)}', None

    status, draft = req(base, draft_path)
    revision = draft.get('revision') if isinstance(draft, dict) else revision
    status, body = req(base, draft_path + '/decisions/batch-approve', {
        'expected_revision': revision,
        'expected_ontology_id': ontology_id or (draft or {}).get('base_ontology_id'),
        'validation_fingerprint': (draft or {}).get('validation_fingerprint'),
        'acknowledged_warning_codes': [],
        'actor': actor,
    })
    if status not in (200, 201):
        return False, f'一键收下失败：{explain(status, body)}', None

    status, draft = req(base, draft_path)
    revision = draft.get('revision') if isinstance(draft, dict) else revision
    status, readiness = req(base, draft_path + '/publish-readiness')
    if status != 200 or not isinstance(readiness, dict):
        return False, f'读发布门禁失败：{explain(status, readiness)}', None
    if readiness.get('ready') is not True:
        first = next((i for i in (readiness.get('items') or []) if i.get('severity') == 'error'), None)
        return False, f'门禁未就绪：{(first or {}).get("code") or (first or {}).get("message") or "未知"}', None

    fingerprint = (draft or {}).get('validation_fingerprint') or readiness.get('validation_fingerprint')
    status, published = req(base, draft_path + '/publish', {
        'expected_revision': revision,
        'expected_ontology_id': latest_ontology_id(base, project_id) or (draft or {}).get('base_ontology_id'),
        'validation_fingerprint': fingerprint,
        'acknowledged_warning_codes': [],
        'idempotency_key': key,
        'actor': actor,
    })
    if status not in (200, 201):
        return False, f'发布失败：{explain(status, published)}', None
    return True, 'ok', published if isinstance(published, dict) else None


def main() -> int:
    parser = argparse.ArgumentParser(description='E1 标注/结构分开计量真机复验')
    parser.add_argument('--url', default='http://127.0.0.1:8100', help='服务地址')
    parser.add_argument('--keep', action='store_true', help='跑完保留临时项目（排查用）')
    args = parser.parse_args()
    base = args.url.rstrip('/')

    stamp = datetime.now().strftime('%m%d-%H%M%S')
    project_name = f'E1标注计量复验·{stamp}'
    actor = 'e1-verifier'
    print(f'== E1「改注释不换版本号」真机复验 · {base} · 临时项目「{project_name}」==', flush=True)

    status, _ = req(base, '/api/health')
    if status != 200:
        print(f'✘ 服务没起（{base}/api/health -> {status}）')
        return 2

    # 新项目默认没有本体，而草案必须有基线才能开 —— use_default_ontology 就是给空项目铺基线的开关。
    status, created = req(base, '/api/projects',
                          {'name': project_name, 'use_default_ontology': True})
    if status not in (200, 201) or not isinstance(created, dict):
        print(f'✘ 建临时项目失败：{explain(status, created)}')
        return 2
    project_id = created.get('id') or (created.get('project') or {}).get('id')

    try:
        # 草案必须挂在一个已有本体上（base_ontology_id），否则 409 stale_base。
        # 基线取项目当前本体 —— 这就是 use_default_ontology 刚铺好的那一版。
        status, baseline = req(base, f'/api/projects/{project_id}/ontology')
        base_id = (baseline or {}).get('id') if isinstance(baseline, dict) else None
        start_version = current_version(base, project_id) or 1

        def open_draft() -> str | None:
            """开一份新草案。

            **发布过的草案是终态、不能再改**（服务端会回 422「已经收下」），所以每一次
            发布动作都要基于"发布后的项目当前本体"重新开一份，不能复用上一份。
            """
            _, latest = req(base, f'/api/projects/{project_id}/ontology')
            status, created = req(base, f'/api/projects/{project_id}/ontology-drafts', {
                'title': 'E1 复验草案', 'actor': actor,
                'base_ontology_id': (latest or {}).get('id') or base_id,
            })
            if status not in (200, 201) or not isinstance(created, dict):
                print(f'  ✘ 建草案失败：{explain(status, created)}')
                return None
            return created.get('id') or (created.get('draft') or {}).get('id')

        def command(draft_id: str, body: dict) -> tuple[bool, str]:
            _, current = req(base, f'/api/projects/{project_id}/ontology-drafts/{draft_id}')
            status, response = req(
                base, f'/api/projects/{project_id}/ontology-drafts/{draft_id}/commands',
                {'expected_revision': (current or {}).get('revision'), 'command': body})
            return status in (200, 201), '' if status in (200, 201) else explain(status, response)

        term = f'{NS}Contract'
        # ① 结构变更：加一个类 → 发布 → 版本号应 +1
        draft_id = open_draft()
        if draft_id is None:
            return 2
        passed, note = command(draft_id, {'action': 'create_term', 'target_iri': term, 'kind': 'class'})
        ok(passed, '①a 加一个类（结构变更）写入草案', note)
        good, note, published = publish_current_draft(base, project_id, draft_id, actor=actor, key=f'e1-struct-1-{stamp}')
        ok(good, '①b 结构变更发布成功', note if not good else '')
        v_after_struct = current_version(base, project_id)
        ok(v_after_struct == start_version + 1, f'①c 结构变更后版本号 +1（v{start_version} → v{start_version + 1}）',
           f'metadata.version={v_after_struct}')
        ok(bool(published and (published.get('metadata') or {}).get('version_reused') is False),
           '①d 发布结果里标明未复用版本号',
           str((published or {}).get('metadata', {}).get('version_reused')))

        # ② 只有标注变更：加一条注释 → 发布 → **版本号必须不变**（E1 核心）
        draft_id = open_draft()
        if draft_id is None:
            return 2
        passed, note = command(draft_id, {
            'action': 'add_annotation', 'target_iri': term,
            'after': {'predicate': RDFS_COMMENT, 'value': '这是一条只在验证里存在的注释'},
        })
        ok(passed, '②a 改注释（纯标注变更）写入草案', note)
        good, note, published2 = publish_current_draft(base, project_id, draft_id, actor=actor, key=f'e1-annot-1-{stamp}')
        ok(good, '②b 纯标注变更发布成功', note if not good else '')
        v_after_annot = current_version(base, project_id)
        ok(v_after_annot == v_after_struct,
           f'②c【E1 核心】改注释后版本号**不变**（仍 v{v_after_struct}）',
           f'metadata.version={v_after_annot}（期望 {v_after_struct}）')
        ok(bool(published2 and (published2.get('metadata') or {}).get('version_reused') is True),
           '②d 发布结果里标明复用了版本号',
           str((published2 or {}).get('metadata', {}).get('version_reused')))

        # 留痕没丢：行数比"结构版本数"多（标注那一次也插了一行）
        status, versions = req(base, f'/api/projects/{project_id}/ontologies')
        rows = (versions or {}).get('versions') or (versions or {}).get('ontologies') or []
        expected_rows = 1 + 2      # bootstrap 1 行 + 结构 1 行 + 标注 1 行（版本号只 +1）
        ok(len(rows) == expected_rows,
           f'②e 历史留痕没丢（{expected_rows} 行记录，但版本号只走过一站）', f'行数={len(rows)}')

        # ③ 再来一次结构变更 → 版本号再 +1
        draft_id = open_draft()
        if draft_id is None:
            return 2
        passed, note = command(draft_id, {'action': 'create_term', 'target_iri': f'{NS}Invoice', 'kind': 'class'})
        ok(passed, '③a 再加一个类（结构变更）写入草案', note)
        good, note, _ = publish_current_draft(base, project_id, draft_id, actor=actor, key=f'e1-struct-2-{stamp}')
        ok(good, '③b 第二次结构变更发布成功', note if not good else '')
        v_final = current_version(base, project_id)
        ok(v_final == v_after_struct + 1,
           f'③c 结构变更后版本号再 +1（= v{v_after_struct + 1}）',
           f'metadata.version={v_final}（期望 {v_after_struct + 1}）')

    except Exception as error:                      # noqa: BLE001 —— 复验脚本要兜住一切并给出结论
        import traceback
        traceback.print_exc()
        ok(False, '复验过程未抛异常', str(error))
    finally:
        if args.keep:
            print(f'  · 保留临时项目 {project_id}（--keep）')
        else:
            code, _ = req(base, f'/api/projects/{project_id}', {'confirm': project_name}, 'DELETE')
            print(f'  · 已删除临时项目 {project_id}（HTTP {code}）')

    failed = [name for passed, name, _ in _results if not passed]
    print(f'\n共 {len(_results)} 项：通过 {len(_results) - len(failed)}，失败 {len(failed)}')
    if failed:
        print('失败项：' + '；'.join(failed))
        return 1
    print('E1「标注/结构分开计量」真机复验全部通过。')
    return 0


if __name__ == '__main__':
    sys.exit(main())
