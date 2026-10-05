#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""「项目与运行」真机复验（D2：项目管理 / 项目总览 / 后台任务 / 快照·评测 四页合并）。

对应《2026-10-02-完整落地规划》§6 的 D2 那一行，以及 §7 的引导式界面规则（E）。

为什么必须真机跑（而不是只靠 pytest）：
  · tests/service/test_runtime_page_ui.py 用的是真实服务，但项目是它自己造的、数据是它自己塞的；
    这里再把**服务端独立算出的数字**（/dashboard 的四项计数、/snapshots 的条数、/api/projects 的项目数）
    与**真实页面上显示的数字**逐项对撞 —— 页面上的数字是不是真的，只有这一层能证。
  · 合并的收益是"少点一次、少找一个"：四个分区必须在进入时就自己把数据拉回来（不是等你点刷新），
    这条只能看真实请求日志。
  · 引导式界面规则是像素读数：字号 ≥11px、首屏内容起点 ≤140px、一屏最多一个实心按钮。

安全性：全程**只读**。脚本自己造一个临时项目（默认跑完删掉），不动你的真实项目数据。

用法：
    python -u -m knowledge_service --port 8100 &          # 先让服务跑起来
    python scripts/verify_runtime_page.py --url http://127.0.0.1:8100
    python scripts/verify_runtime_page.py --keep           # 保留临时项目，便于自己点一遍

退出码：0 = 全部通过；1 = 有失败项；2 = 连接 / 环境问题。
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
from pathlib import Path

# 本机系统代理会把 127.0.0.1 的请求也劫持走（502），必须显式绕过 —— 与 tests 的约定一致。
os.environ.setdefault('NO_PROXY', '127.0.0.1,localhost')

EDGE = Path(r'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe')

RESULTS: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = '') -> bool:
    RESULTS.append((name, bool(ok), detail))
    print(f"{'PASS' if ok else 'FAIL'} · {name}" + (f" · {detail}" if detail else ''))
    return bool(ok)


def call(base: str, path: str, body=None, method: str | None = None):
    """一次 HTTP 调用；非 2xx 时把服务端 detail 原样抛出来（排错靠它）。"""
    data = None if body is None else json.dumps(body, ensure_ascii=False).encode('utf-8')
    request = urllib.request.Request(base + path, data=data,
                                     method=method or ('GET' if body is None else 'POST'),
                                     headers={'Content-Type': 'application/json'})
    try:
        with urllib.request.urlopen(request, timeout=120) as response:
            return json.loads(response.read().decode('utf-8'))
    except urllib.error.HTTPError as error:
        detail = error.read().decode('utf-8', 'replace')
        raise RuntimeError(f'{method or "POST"} {path} → HTTP {error.code}：{detail[:400]}') from error


TURTLE = ('@prefix ex: <http://ex/> . @prefix owl: <http://www.w3.org/2002/07/owl#> . '
          'ex:商户 a owl:Class . ex:结算服务 a owl:Class . ex:使用 a owl:ObjectProperty .')


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument('--url', default='http://127.0.0.1:8100')
    parser.add_argument('--keep', action='store_true', help='跑完保留临时项目（默认删除）')
    args = parser.parse_args()
    base = args.url.rstrip('/')

    stamp = datetime.now().strftime('%m%d-%H%M%S')
    project_name = f'D2复验·项目与运行·{stamp}'
    print(f'== D2「项目与运行」真机复验 · {base} · 临时项目「{project_name}」==')

    try:
        health = call(base, '/api/health')
    except Exception as error:  # noqa: BLE001 - 连不上就是环境问题，直接给清楚提示
        print(f'连不上服务（{base}）：{error}')
        return 2
    check('服务健康', health.get('status') == 'ok', f"{health.get('status')} / {health.get('database')}")

    projects_before = len(call(base, '/api/projects')['projects'])
    suffix = uuid.uuid4().hex[:8]
    project = call(base, '/api/projects', {'name': project_name, 'use_default_ontology': False})
    p = project['id']
    try:
        run(base, p, suffix)
    finally:
        if args.keep:
            print(f'--keep：保留临时项目 {p}（{project_name}），记得手工删掉')
        else:
            try:
                call(base, f'/api/projects/{p}', {}, method='DELETE')
                print(f'已删除临时项目 {p}')
            except RuntimeError as error:
                print(f'临时项目删除失败（请手工清理 {p}）：{error}')

    projects_after = len(call(base, '/api/projects')['projects'])
    check('只读复验：项目总数没变', projects_before == projects_after, f'{projects_before} → {projects_after}')

    failed = [name for name, ok, _ in RESULTS if not ok]
    print(f'\n共 {len(RESULTS)} 项：通过 {len(RESULTS) - len(failed)}，失败 {len(failed)}')
    if failed:
        print('失败项：')
        for name in failed:
            print('  -', name)
        return 1
    print('D2「项目与运行」真机复验全部通过。')
    return 0


# ── 页面读取用的小工具（都在真实浏览器里跑） ─────────────────────────────────────
JS_NAV = """() => [...document.querySelectorAll('aside nav button[data-tab]')].map(b => b.dataset.tab)"""
JS_GROUPS = """() => [...document.querySelectorAll('details.nav-group')].map(group => ({
    label: group.querySelector('summary').textContent.trim(),
    tabs: [...group.querySelectorAll('button[data-tab]')].map(b => b.dataset.tab),
    open: group.open}))"""
JS_VISIBLE_PANELS = """() => [...document.querySelectorAll('.runtime-panel')]
    .filter(el => getComputedStyle(el).display !== 'none').map(el => el.id)"""
JS_ENTRY = """() => {
    const sections=[...document.querySelectorAll('aside nav details.nav-group')];
    const group=sections.find(d => d.querySelector('[data-tab="runtime"]'));
    const button=document.querySelector('[data-tab="runtime"]');
    return {exists: Boolean(button), label: button ? button.textContent.trim() : null,
            group: group ? group.querySelector('summary').textContent.trim() : null,
            groupOpen: group ? group.open : null};
}"""
JS_VIEWS = """() => [...document.querySelectorAll('[data-runtime-view]')].map(b => ({
    view: b.dataset.runtimeView, pressed: b.getAttribute('aria-pressed'),
    name: b.querySelector('b').textContent.trim(),
    note: (b.querySelector('small') || {}).textContent?.trim() || ''}))"""
JS_DASHBOARD = """() => Object.fromEntries([...document.querySelectorAll('#dashboard .metric-card')]
    .map(card => [card.querySelector('.metric-label').textContent.trim(),
                  Number(card.querySelector('strong').textContent.trim())]))"""
JS_SOLID = """() => {
    const panel=[...document.querySelectorAll('.runtime-panel')]
        .find(el => getComputedStyle(el).display !== 'none' && el.offsetParent);
    if (!panel) return {panel: null, buttons: [], solid: []};
    // 「实心」的判据随主题变：浅色主题下"白底＝描边"，深色主题下"深面板底＝描边"。
    // 所以不能只排掉透明/白，还要排掉**接近背景的暗色**，否则每个 secondary 都会被数成实心。
    const isSolid=background => {
        if (['rgba(0, 0, 0, 0)', 'transparent'].includes(background)) return false;
        const parts=background.match(/[\d.]+/g);
        if (!parts || parts.length < 3) return false;
        const [r, g, b] = parts.map(Number);
        const alpha = parts.length > 3 ? Number(parts[3]) : 1;
        if (alpha < 0.35) return false;                      // 很淡的底＝描边感
        const lum = (0.2126 * r + 0.7152 * g + 0.0722 * b) / 255;
        return lum > 0.22;                                   // 只有明显亮过深面板底的才算「实心」
    };
    const buttons=[...panel.querySelectorAll('button')].filter(button => {
        if (getComputedStyle(button).display === 'none' || !button.offsetParent) return false;
        const rect=button.getBoundingClientRect();
        return rect.width >= 2 && rect.height >= 2;
    }).map(button => ({text: button.textContent.trim(),
                       background: getComputedStyle(button).backgroundColor}));
    return {panel: panel.id, buttons: buttons,
            solid: buttons.filter(b => isSolid(b.background)).map(b => b.text)};
}"""
JS_SMALL_TEXT = """() => {
    const scopes=[document.getElementById('tab-runtime'),
                  ...document.querySelectorAll('.runtime-panel')].filter(el => el && getComputedStyle(el).display !== 'none');
    const bad=[];
    for (const scope of scopes){
        for (const el of scope.querySelectorAll('*')){
            const own=[...el.childNodes].some(node => node.nodeType === 3 && node.textContent.trim());
            if (!own) continue;
            const size=parseFloat(getComputedStyle(el).fontSize);
            if (size < 11) bad.push(`${size}px <${el.tagName.toLowerCase()}> ${el.textContent.trim().slice(0, 24)}`);
        }
    }
    return [...new Set(bad)];
}"""


def run(base: str, p: str, suffix: str) -> None:
    # ── 准备：一份小本体 + 2 实体 1 关系 + 1 份快照 ────────────────────────────────
    call(base, f'/api/projects/{p}/ontologies', {'turtle': TURTLE, 'expected_ontology_id': None})
    call(base, f'/api/projects/{p}/records', {'records': [
        {'id': f'e-{suffix}-1', 'kind': 'entity', 'type': '商户', 'text': '商户甲'},
        {'id': f'e-{suffix}-2', 'kind': 'entity', 'type': '结算服务', 'text': '结算服务A'},
        {'id': f'r-{suffix}-1', 'kind': 'relation', 'type': '使用', 'text': '商户甲使用结算服务A',
         'subject_id': f'e-{suffix}-1', 'object_id': f'e-{suffix}-2'},
    ]})
    call(base, f'/api/projects/{p}/snapshots', {'name': f'复验快照·{suffix}'})

    # 服务端独立算出的口径（下面每一项都要和页面上的读数对撞）
    counts = call(base, f'/api/projects/{p}/dashboard', {})['counts']
    snapshots = call(base, f'/api/projects/{p}/snapshots', None, 'GET')['snapshots']
    projects = call(base, '/api/projects')['projects']
    expected = {'已解析文档': counts.get('document', 0), '实体': counts.get('entity', 0),
                '关系': counts.get('relation', 0), '原文片段': counts.get('chunk', 0)}
    check('临时数据就位（服务端口径）', expected['实体'] == 2 and expected['关系'] == 1,
          f"实体 {expected['实体']} · 关系 {expected['关系']} · 快照 {len(snapshots)}")

    # ── 开真浏览器 ────────────────────────────────────────────────────────────────
    playwright = None
    try:
        from playwright.sync_api import sync_playwright
        playwright = sync_playwright().start()
    except Exception as error:  # noqa: BLE001
        print(f'无法启动 Playwright（{error}），跳过真页面检查')
        return
    errors: list[str] = []
    paths: list[str] = []
    bad_responses: list[str] = []
    try:
        browser = playwright.chromium.launch(headless=True, executable_path=str(EDGE))
        context = browser.new_context(viewport={'width': 1680, 'height': 950})
        page = context.new_page()
        page.on('pageerror', lambda error: errors.append(str(error)))
        page.on('request', lambda request: paths.append(request.url.split('/api/', 1)[1])
                if '/api/' in request.url else None)
        page.on('response', lambda response: bad_responses.append(f'{response.status} {response.url}')
                if response.status >= 400 and 'favicon' not in response.url else None)
        page.goto(f'{base}/', wait_until='load')
        page.wait_for_function("() => document.querySelectorAll('#project option').length > 1")
        page.select_option('#project', p)
        page.wait_for_function("() => document.querySelectorAll('#type-scope option').length > 1")
        paths.clear()

        # ① 侧边栏：一个「项目与运行」，旧的四个页签彻底消失
        tabs = page.evaluate(JS_NAV)
        for retired in ('projects', 'dashboard', 'jobs', 'evaluation'):
            check(f'旧页签 {retired} 已从侧边栏消失', retired not in tabs)
        check('「项目与运行」恰好一个入口', tabs.count('runtime') == 1, f'共 {len(tabs)} 项：{tabs}')
        entry = page.evaluate(JS_ENTRY)
        check('入口文案是「项目与运行」', entry['label'] == '项目与运行', str(entry))
        check('入口所在分组叫「运行层 · 跑起来」且默认展开', entry['group'] == '运行层 · 跑起来' and entry['groupOpen'] is True,
              str(entry))
        groups = page.evaluate(JS_GROUPS)
        check('导航分组是 4 组（本体层/实例层/消费层/运行层）',
              [g['label'] for g in groups] == ['本体层 · 结构', '实例层 · 知识', '消费层 · 用起来', '运行层 · 跑起来'],
              str([g['label'] for g in groups]))

        # ② 进入这一页：一屏一问
        page.evaluate("() => document.querySelector('[data-tab=\"runtime\"]').click()")
        page.wait_for_function("() => !document.getElementById('dashboard').textContent.includes('正在读取')")
        check('页面可见', page.evaluate(
            "() => !document.getElementById('tab-runtime').classList.contains('hidden')"))
        check('页头写清这一页管什么', page.evaluate(
            "() => document.querySelector('#tab-runtime .runtime-hero').textContent").count('跑得怎么样') == 1)
        views = page.evaluate(JS_VIEWS)
        check('四个分区且每格都写清回答什么',
              [v['view'] for v in views] == ['project', 'overview', 'jobs', 'snapshots']
              and all(len(v['note']) >= 8 for v in views), str(views))
        check('默认落在「运行总览」且同时只有一屏可见',
              page.evaluate(JS_VISIBLE_PANELS) == ['tab-dashboard']
              and [v['view'] for v in views if v['pressed'] == 'true'] == ['overview'])
        check('进入即拉数据（自动请求 dashboard）',
              any(path.endswith('/dashboard') for path in paths), str(paths[-4:]))

        # ③ 运行总览上的数字 = 服务端独立算出的数字
        rendered = page.evaluate(JS_DASHBOARD)
        check('运行总览的四个数字与服务端一致', rendered == expected, f'页面 {rendered} vs 服务端 {expected}')
        check('页面标题带分区后缀', page.evaluate("() => document.getElementById('title').textContent")
              == '项目与运行 · 运行总览')
        solid_overview = page.evaluate(JS_SOLID)

        # ④ 后台任务：进来就拉，且只有这一屏可见
        paths.clear()
        page.evaluate("(view) => document.querySelector(`[data-runtime-view=\"${view}\"]`).click()", 'jobs')
        page.wait_for_function("() => document.getElementById('tasks').textContent.trim() !== ''")
        check('后台任务：只有这一屏可见', page.evaluate(JS_VISIBLE_PANELS) == ['tab-jobs'])
        check('后台任务：进入即拉数据（自动请求 jobs）',
              any(path.endswith('/jobs') for path in paths), str(paths))
        check('后台任务：本项目确实没有任务时如实说明',
              '暂无任务' in page.evaluate("() => document.getElementById('tasks').textContent"))
        solid_jobs = page.evaluate(JS_SOLID)

        # ⑤ 快照与评测：进来就自动读快照（不需要先点「刷新」）
        paths.clear()
        page.evaluate("(view) => document.querySelector(`[data-runtime-view=\"${view}\"]`).click()", 'snapshots')
        page.wait_for_function("() => document.getElementById('snapshot-cards').children.length > 0")
        check('快照与评测：只有这一屏可见', page.evaluate(JS_VISIBLE_PANELS) == ['tab-evaluation'])
        check('快照与评测：进入即拉数据（自动请求 snapshots）',
              any(path.endswith('/snapshots') for path in paths), str(paths))
        cards = page.evaluate("() => document.querySelectorAll('#snapshot-cards .eval-snapshot-card').length")
        check('快照卡片数 = 服务端快照数', cards == len(snapshots), f'页面 {cards} vs 服务端 {len(snapshots)}')
        check('快照恢复只有一个入口（卡片上的按钮）',
              page.evaluate("() => document.querySelectorAll('[data-snapshot-restore]').length") == cards)
        solid_snapshots = page.evaluate(JS_SOLID)

        # ⑥ 项目设置：项目数 = 服务端项目数
        paths.clear()
        page.evaluate("(view) => document.querySelector(`[data-runtime-view=\"${view}\"]`).click()", 'project')
        page.wait_for_function("() => document.querySelectorAll('#projects-root .project-card').length > 0")
        check('项目设置：只有这一屏可见', page.evaluate(JS_VISIBLE_PANELS) == ['tab-projects'])
        cards_in_page = page.evaluate("() => document.querySelectorAll('#projects-root .project-card').length")
        check('项目卡片数 = 服务端项目数', cards_in_page == len(projects),
              f'页面 {cards_in_page} vs 服务端 {len(projects)}')
        check('本项目在列表里（名字对得上）', page.evaluate(
            "() => [...document.querySelectorAll('#projects-root .project-card h3')].map(h => h.textContent)").count(
                page.evaluate("() => document.getElementById('project').selectedOptions[0].textContent")) >= 1)
        solid_project = page.evaluate(JS_SOLID)

        # ⑦ 引导式界面规则（E）：像素读数
        page.evaluate("(view) => document.querySelector(`[data-runtime-view=\"${view}\"]`).click()", 'overview')
        top = page.evaluate(
            "() => document.querySelector('#tab-runtime .runtime-hero').getBoundingClientRect().top")
        check('首屏内容起点 ≤140px', top <= 140, f'{round(top, 1)}px')
        for view in ('overview', 'jobs', 'snapshots', 'project'):
            page.evaluate("(view) => document.querySelector(`[data-runtime-view=\"${view}\"]`).click()", view)
            bad = page.evaluate(JS_SMALL_TEXT)
            check(f'{view} 分区没有小于 11px 的文字', bad == [], str(bad))
        for view, solid in (('overview', solid_overview), ('jobs', solid_jobs),
                            ('snapshots', solid_snapshots), ('project', solid_project)):
            check(f'{view} 分区最多一个实心按钮', len(solid['solid']) <= 1,
                  f"{solid['panel']}：实心 {solid['solid']} · 全部 {[b['text'] for b in solid['buttons']]}")

        # ⑧ 诚实性：没有 JS 错误；接口失败必须"说出来"，不能静默吞掉
        check('页面没有 JS 错误', errors == [], str(errors[:3]))
        # Neo4j 是**可选**外部依赖，没配好时探活接口按仓库既有约定返回 503（test_neo4j_store 也这么钉），
        # 所以这里不把 neo4j 的 4xx/5xx 当失败，而是**要求页面把它说出来**：
        optional = [item for item in bad_responses if '/storage/neo4j' in item]
        unexpected = [item for item in bad_responses if '/storage/neo4j' not in item]
        check('没有未说明的失败接口调用', unexpected == [], str(unexpected[:5]))
        if optional:
            print(f'说明 · Neo4j 可选依赖不可用（{len(optional)} 次 503，未配置即如此），下面核对页面是否如实说明')
        page.evaluate("(view) => document.querySelector(`[data-runtime-view=\"${view}\"]`).click()", 'overview')
        page.wait_for_timeout(250)
        note = page.evaluate(
            "() => document.querySelector('#tab-dashboard .neo4j-panel pre').textContent")
        check('可选依赖不可用时页面上有话（不是静默失败）',
              '连接检查失败' in note or '连接正常' in note, note.replace('\n', ' / ')[:160])

        context.close()
        browser.close()
    finally:
        playwright.stop()


if __name__ == '__main__':
    sys.exit(main())
