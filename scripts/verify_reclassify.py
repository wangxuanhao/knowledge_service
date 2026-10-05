"""C2「受控重分类」真机复验：在**真实服务**上造一次 v1→v2 迁移，走完 看 / 干跑 / 迁 / 撤销。

为什么不只靠 pytest（两者都要跑，各管一段）：

    pytest 跑的是真实服务代码，但用的是**测试库**（``knowledge_test``，跑完会删）；
    这个脚本打的是**你正在用的那台服务**（默认 http://127.0.0.1:8100 + 真实 PostgreSQL），
    全程只走公开 HTTP 接口，每一步都和页面上点的按钮一一对应。它自己新建一个项目，
    跑完默认删掉（``--keep`` 可以留着重看），**不碰你现有的任何项目**。

它验的是规划 §8 给 C2 定的口径（每条都打印对照数字，不靠"应该是这样"）：

    ① 旧知识**产生新版本**（不原地改）：迁移后 version +1、历史里多一行、旧版本仍读得到；
    ② **整批可一键撤销**：整批写成一条操作，撤销后归属与类型都回到迁移前；
    ③ **默认不跑**：不给分组时迁移接口必须拒绝（422），而不是"帮你迁全部"；
    ④ 干跑不写、且与真正写入同一口径：干跑说通过 → 迁移就真的成功。

用法：

    NO_PROXY='127.0.0.1,localhost' python scripts/verify_reclassify.py
    ... --url http://127.0.0.1:8100 --keep      # 保留验证项目，自己去页面上看

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
from datetime import datetime

# 本机代理会把 127.0.0.1 的请求也劫持走，必须显式绕过（与 tests 的约定一致）。
os.environ.setdefault('NO_PROXY', '127.0.0.1,localhost')

NS = 'https://verify.test/ns#'
TURTLE_V1 = f'''
@prefix ex: <{NS}> .
@prefix owl: <http://www.w3.org/2002/07/owl#> .
@prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .
ex:包裹 a owl:Class ; rdfs:label "包裹" .
ex:包 a owl:Class ; rdfs:label "包" .
'''


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
        with urllib.request.urlopen(req, timeout=30) as response:
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


def local_name(iri: str | None) -> str:
    """把 IRI 缩成最后一段，便于打印与比较（脚本里两种写法都可能出现）。"""
    return str(iri or '').rstrip('/').split('/')[-1].split('#')[-1]


def check(problems: list, condition: bool, message: str) -> None:
    """收一条检查结果：不通过就记下来并当场打印，不中断后续检查。"""
    if condition:
        print(f'  ✓ {message}')
    else:
        print(f'  ✗ {message}')
        problems.append(message)


def step(title: str) -> None:
    print(f'\n=== {title} ===')


def publish_v2(base: str, project: str, base_ontology_id: str) -> str:
    """走完整的草案流程发布 v2：把「包」停用并指定替代类「包裹」。

    发布必须走审核流程（A3 之后收下即发布），所以这里老老实实把这五步做完 ——
    它同时也证明了 C2 的输入（替代映射）确实是**人工在本体编辑台停用类时**留下的那份数据。
    """
    status, draft = request(base, f'/api/projects/{project}/ontology-drafts', {
        'base_ontology_id': base_ontology_id, 'source_kind': 'manual',
        'title': 'C2 真机复验：停用「包」并指定替代类', 'actor': 'verify:reclassify'})
    if status != 201:
        raise Failure(f'创建草案失败：HTTP {status} {draft}')
    status, draft = request(
        base, f"/api/projects/{project}/ontology-drafts/{draft['id']}/commands", {
            'expected_revision': draft['revision'],
            'command': {'action': 'retire_term', 'target_iri': f'{NS}包',
                        'after': {'replacement': {'iri': f'{NS}包裹'}},
                        'reason': '改名：包 → 包裹'}})
    if status != 200:
        raise Failure(f'停用「包」的命令被拒：HTTP {status} {draft}')
    status, draft = request(
        base, f"/api/projects/{project}/ontology-drafts/{draft['id']}/validate",
        {'expected_revision': draft['revision']})
    if status != 200:
        raise Failure(f'校验失败：HTTP {status} {draft}')
    status, submitted = request(
        base, f"/api/projects/{project}/ontology-drafts/{draft['id']}/submit",
        {'expected_revision': draft['revision']})
    if status != 200:
        raise Failure(f'提交审核失败：HTTP {status} {submitted}')
    operation = submitted['operations'][0]
    warnings = [item['code'] for item in submitted['validation_report']['warnings']
                if item.get('code')]
    status, reviewed = request(
        base, f"/api/projects/{project}/ontology-drafts/{submitted['id']}/decisions", {
            'expected_revision': submitted['revision'],
            'expected_ontology_id': base_ontology_id,
            'validation_fingerprint': submitted['validation_fingerprint'],
            'acknowledged_warning_codes': warnings,
            'actor': 'verify:reviewer',
            'decisions': [{'operation_id': operation['id'],
                           'operation_fingerprint': operation['fingerprint'],
                           'action': 'approve', 'reason': '复验：同意停用与替代'}]})
    if status != 200:
        raise Failure(f'收下决定被拒：HTTP {status} {reviewed}')
    status, version = request(
        base, f"/api/projects/{project}/ontology-drafts/{reviewed['id']}/publish", {
            'expected_revision': reviewed['revision'],
            'expected_ontology_id': base_ontology_id,
            'validation_fingerprint': reviewed['validation_fingerprint'],
            'acknowledged_warning_codes': warnings,
            'idempotency_key': f'verify-reclassify-{reviewed["id"]}',
            'actor': 'verify:publisher'})
    if status != 200:
        raise Failure(f'发布失败：HTTP {status} {version}')
    return version['id']


def run(base: str, project: str | None, keep_project: bool) -> int:
    problems: list = []
    created = False
    # 记录 ID 在库里是全局唯一的（换个项目也不会重号），所以每次跑都得现编一个后缀，
    # 否则第二次运行会 422「断言出现 ID 冲突」—— 复验脚本必须能反复跑。
    tag = uuid.uuid4().hex[:8]
    keep_id, rename_id = f'v-keep-{tag}', f'v-rename-{tag}'
    if project is None:
        step('0. 建一个验证用的项目（跑完会删）')
        status, body = request(base, '/api/projects', {
            'name': f'受控重分类真机复验 {datetime.now():%Y-%m-%d %H:%M:%S}',
            'use_default_ontology': False})
        if status != 201:
            raise Failure(f'建项目失败：HTTP {status} {body}')
        project, created = body['id'], True
        status, v1 = request(base, f'/api/projects/{project}/ontologies',
                             {'turtle': TURTLE_V1, 'expected_ontology_id': None})
        if status != 201:
            raise Failure(f'写入 v1 本体失败：HTTP {status} {v1}')
        print(f'  项目 {project} · 本体 v1 {v1["id"]}')
    else:
        status, versions = request(base, f'/api/projects/{project}/ontologies')
        if status != 200 or not versions.get('versions'):
            raise Failure(f'项目 {project} 没有本体版本，无法复验：HTTP {status}')
        v1 = versions['versions'][-1]
        print(f'  复用项目 {project} · 当前本体 {v1["id"]}')

    try:
        step('1. 在 v1 上写两条知识（一条类型保留、一条类型要改名）')
        status, written = request(base, f'/api/projects/{project}/records', {'records': [
            {'id': keep_id, 'kind': 'entity', 'type': f'{NS}包裹', 'text': '一号包裹',
             'ontology_id': v1['id']},
            {'id': rename_id, 'kind': 'entity', 'type': f'{NS}包', 'text': '旧名叫包的东西',
             'ontology_id': v1['id']},
        ]})
        if status != 201:
            raise Failure(f'写知识失败：HTTP {status} {written}')
        print('  已写入 2 条（都挂在 v1 上）')

        step('2. 发布 v2：停用「包」并指定替代类「包裹」（替代映射来自本体编辑台）')
        v2 = publish_v2(base, project, v1['id'])
        print(f'  新版本真机产出：{v2}（与 v1 不同 = {v2 != v1["id"]}）')

        step('3. 看（只读）：/reclassify 应当列出这两条旧知识，并给出三档处置')
        status, plan = request(base, f'/api/projects/{project}/reclassify')
        check(problems, status == 200,
              f'GET /reclassify 返回 200（实际 {status}）')
        check(problems, plan['current_ontology_id'] == v2,
              f'当前版本指向刚发布的 v2（{local_name(plan["current_ontology_id"])} 是 v2 = '
              f'{plan["current_ontology_id"] == v2}）')
        check(problems, plan['stale_records'] == 2,
              f'还挂在旧版本上的知识 = 2（实际 {plan["stale_records"]}）')
        groups = {group['key']: group for group in plan['groups']}
        actions = sorted(group['action'] for group in plan['groups'])
        check(problems, actions == ['carry', 'rename'],
              f'两组的处置 = carry + rename（实际 {actions}）')
        print('  面板：')
        for group in plan['groups']:
            print(f'    - {group["from_version"]} 的「{group["from_label"]}」 → '
                  f'{group["to_label"] or "（没有去处）"}'
                  f' · {group["record_count"]} 条 · {group["action_label"]}')
        rename = next(group for group in plan['groups'] if group['action'] == 'rename')
        check(problems, rename['to_type'] == f'{NS}包裹',
              f'改名组的替代概念 = 包裹（实际 {local_name(rename["to_type"])}）')

        step('4. 默认不跑：不给分组时迁移接口必须拒绝')
        status, refused = request(base, f'/api/projects/{project}/reclassify', {})
        check(problems, status == 422,
              f'空选被拒（期望 422，实际 {status}）：{str(refused)[:80]}')

        step('5. 干跑（不写任何东西）')
        keys = [group['key'] for group in plan['groups'] if group['migratable']]
        status, before_keep = request(base, f'/api/projects/{project}/records/{keep_id}')
        before_version = before_keep['version']
        status, preview = request(
            base, f'/api/projects/{project}/reclassify/preview', {'groups': keys})
        check(problems, status == 200 and preview['blocked'] is False,
              f'干跑结论 = 能迁（实际 blocked={preview.get("blocked")}）')
        check(problems, preview['would_change'] == 2,
              f'干跑说会改 2 条（实际 {preview["would_change"]}）')
        status, after_keep = request(base, f'/api/projects/{project}/records/{keep_id}')
        check(problems, after_keep['version'] == before_version,
              f'干跑没写库（版本仍是 {after_keep["version"]}）')

        step('6. 真迁：整批一条操作')
        status, applied = request(
            base, f'/api/projects/{project}/reclassify',
            {'groups': keys, 'actor': 'verify:reclassify'})
        check(problems, status == 200, f'迁移成功（HTTP {status}）')
        check(problems, applied['migrated'] == 2,
              f'迁了 2 条（实际 {applied["migrated"]}）')
        operation_id = applied['operation_id']

        step('7. 不原地改：新版本 + 历史仍可查')
        status, record = request(base, f'/api/projects/{project}/records/{keep_id}')
        check(problems, record['ontology_id'] == v2,
              f'保留组的归属已改到 v2（实际 {local_name(record["ontology_id"])}）')
        check(problems, record['version'] == before_version + 1,
              f'版本 +1：{before_version} → {record["version"]}（不是原地改）')
        status, history = request(base, f'/api/projects/{project}/records/{keep_id}/history')
        check(problems, len(history['versions']) == record['version'],
              f'版本历史里每一条都在（{len(history["versions"])} 行 = version {record["version"]}）')
        check(problems, history['versions'][0]['ontology_id'] == v1['id'],
              '旧版本仍读得到（第一行还写着 v1 归属）')
        status, renamed = request(base, f'/api/projects/{project}/records/{rename_id}')
        check(problems, local_name(renamed['type']) == '包裹',
              f'改名组的类型已改成替代概念（实际 {local_name(renamed["type"])}）')
        status, after_plan = request(base, f'/api/projects/{project}/reclassify')
        check(problems, after_plan['stale_records'] == 0,
              f'迁完面板清空（还挂着 {after_plan["stale_records"]} 条）')

        step('8. 整批一键撤销：回到迁移前')
        status, undone = request(
            base, f'/api/projects/{project}/operations/{operation_id}/undo', {})
        check(problems, status == 200 and undone.get('restored') == 2,
              f'撤销恢复 2 条（实际 {str(undone)[:80]}）')
        status, record = request(base, f'/api/projects/{project}/records/{keep_id}')
        check(problems, record['ontology_id'] == v1['id'],
              f'归属回到 v1（实际 {local_name(record["ontology_id"])}）')
        status, renamed = request(base, f'/api/projects/{project}/records/{rename_id}')
        check(problems, local_name(renamed['type']) == '包',
              f'类型也回退了（实际 {local_name(renamed["type"])}）')
        status, after_plan = request(base, f'/api/projects/{project}/reclassify')
        check(problems, after_plan['stale_records'] == 2,
              f'回退后面板重新列出 2 条（实际 {after_plan["stale_records"]}）')
    finally:
        if created and not keep_project:
            request(base, f'/api/projects/{project}', method='DELETE')
            print(f'\n已删除验证项目 {project}（--keep 可以留着）')
        elif created:
            print(f'\n验证项目保留：{project}')

    step('结论')
    if problems:
        print(f'  {len(problems)} 项不通过：')
        for item in problems:
            print(f'    - {item}')
        return 1
    print('  全部通过：看 / 干跑 / 迁 / 撤销 四条路都在真机上走通了')
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description='受控重分类（C2）真机复验')
    parser.add_argument('--url', default='http://127.0.0.1:8100', help='服务地址')
    parser.add_argument('--project', default=None,
                        help='复用已有项目 uuid（默认新建一个验证项目并在跑完后删除）')
    parser.add_argument('--keep', action='store_true', help='保留新建的验证项目')
    args = parser.parse_args()
    base = args.url.rstrip('/')
    try:
        return run(base, args.project, args.keep)
    except Failure as error:
        print(f'\n无法完成复验：{error}', file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
