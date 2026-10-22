"""Build an offline, source-backed HTML/JSON capacity recertification report.

This script never opens an application/database connection. Raw server and host
traces remain private; only numeric aggregates and request IDs are published.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from datetime import datetime, timezone
import hashlib
import html
import json
import math
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PREFIX = '2026-10-21-capacity-'


def quantile(values, fraction):
    return sorted(values)[max(0, math.ceil(len(values)*fraction)-1)] if values else None


def read_json(path):
    return json.loads(path.read_text(encoding='utf-8'))


def read_lines(path):
    if path.exists():
        for line in path.open(encoding='utf-8'):
            try:
                yield json.loads(line)
            except ValueError:
                continue


def distribution(values):
    values = [v for v in values if isinstance(v, (int, float))]
    return {'samples': len(values), 'min': min(values, default=None),
            'p50': quantile(values, .5), 'p95': quantile(values, .95),
            'max': max(values, default=None)}


def observer_summary(path):
    resources, pg, errors = [], [], []
    for row in read_lines(path):
        if row.get('label') == 'resources.sample':
            resources.append(row)
        elif row.get('label') == 'postgres.sample':
            pg.append(row)
        elif 'error' in row.get('label', ''):
            errors.append(row.get('label'))
    return {'source': path.name, 'resource_samples': len(resources),
            'resource_first_wall_time': resources[0]['wall_time'] if resources else None,
            'resource_last_wall_time': resources[-1]['wall_time'] if resources else None,
            'pg_first_wall_time': pg[0]['wall_time'] if pg else None,
            'pg_last_wall_time': pg[-1]['wall_time'] if pg else None,
            'host_cpu_percent': distribution([r.get('host_cpu_percent') for r in resources]),
            'host_memory_available_bytes': distribution([r.get('host_memory_available_bytes') for r in resources]),
            'server_rss_bytes': distribution([r.get('process_rss_bytes') for r in resources]),
            'host_swap_used_bytes': distribution([r.get('host_swap_used_bytes') for r in resources]),
            'pg_samples': len(pg),
            'pg_interval_seconds': distribution([b['wall_time']-a['wall_time'] for a,b in zip(pg, pg[1:])]),
            'samples_with_blockers': sum(bool(r.get('blocked_backends')) for r in pg),
            'samples_with_ungranted_locks': sum(bool(r.get('ungranted_locks')) for r in pg),
            'samples_with_lock_wait': sum(any(a.get('wait_event_type') == 'Lock' for a in r.get('activity', [])) for r in pg),
            'observed_wait_events': dict(Counter(a.get('wait_event_type', 'none') or 'none' for r in pg for a in r.get('activity', []))),
            'samples_with_granted_share_row_exclusive': sum(any(l.get('mode') == 'ShareRowExclusiveLock' and l.get('granted') for l in r.get('locks', [])) for r in pg),
            'max_transaction_age_seconds': max((a.get('transaction_age_seconds') or 0 for r in pg for a in r.get('activity', [])), default=None),
            'error_labels': dict(Counter(errors)),
            'caution': 'Sampling can miss short waits. Swap allocation alone does not establish paging I/O or causality.'}


def trace_summary(path):
    groups = defaultdict(lambda: {'http': None, 'phases': defaultdict(list)})
    waits = []
    for row in read_lines(path):
        request_id = row.get('request_id')
        if not request_id:
            continue
        if row.get('label') == 'http.total':
            groups[request_id]['http'] = {k:row.get(k) for k in ('wall_time','duration_seconds','method','route','status','error_type')}
        else:
            groups[request_id]['phases'][row.get('label')].append(row.get('duration_seconds', 0))
        if row.get('label') == 'lock.repository.writer.wait' and row.get('duration_seconds', 0) > 1:
            waits.append({k:row.get(k) for k in ('request_id','duration_seconds','owner_request_id','owner_phase')})
    writes = []
    for rid, group in groups.items():
        if group['http'] and group['http']['method'] == 'PUT':
            writes.append({'request_id':rid, **group['http'],
                           'nested_phase_seconds_do_not_sum':{k:sum(v) for k,v in group['phases'].items() if k in (
                               'repo.snapshot_data','repo.current_records','repo.planning_snapshot',
                               'repo.reserve_operation','encoder.encode','formal.execute_actions',
                               'service.projection_nudge','lock.repository.writer.wait','lock.service.hold')}})
    waits.sort(key=lambda r:r['duration_seconds'], reverse=True)
    for wait in waits:
        wait['waiting_http'] = groups.get(wait['request_id'],{}).get('http')
        wait['owning_http'] = groups.get(wait['owner_request_id'],{}).get('http')
    return {'completed_puts':writes, 'long_writer_waits':waits[:15],
            'caution':'Phase durations are nested and must not be summed. Instrumented timings are diagnostic, not an uninstrumented baseline.'}


def build(data_dir, private_dir):
    sources = {}
    for suffix in ('normal-http-read-v2','normal-search','deep-neighborhood','audited-writes',
                   'browser-final','normal-writes','final-verification','harness-verification','current-http',
                   'source-midpoint','source-normal-start','source-normal-end',
                   'current-search-error','projection-consistency','ui-final','runtime-handoff'):
        path = data_dir / (PREFIX + suffix + '.json')
        if path.exists():
            sources[suffix] = read_json(path)
    required = ('normal-http-read-v2','normal-search','deep-neighborhood','audited-writes','browser-final')
    missing = [name for name in required if name not in sources]
    if missing:
        raise SystemExit('Missing final evidence: ' + ', '.join(missing))
    browser = sources['browser-final']
    load = next(x for x in browser['loads'] if x.get('complete'))
    page_group=next((g for g in browser['http'].get('groups',[]) if g['path'].endswith('/graph-sessions/:session/page')),None)
    pipeline={'page_http':page_group,'renderer_cpu_methods':browser['renderer_calls'],
              'page_http_seconds_approx':page_group['duration']['count']*page_group['duration']['mean_ms']/1000 if page_group else None,
              'caution':'ResourceTiming covers service/transport/scheduling, not pure SQL. Rounded mean*count is approximate. Method durations are CPU only; different scopes must not be treated as an exclusive additive breakdown.'}
    source_changes=[]
    if 'source-normal-start' in sources and 'source-normal-end' in sources:
        before={x['file']:x['sha256'] for x in sources['source-normal-start']['files']}
        source_changes=[x['file'] for x in sources['source-normal-end']['files'] if x['file'] in before and x['sha256']!=before[x['file']]]
    rows = []
    names = {'warm.detail':'实体详情','warm.records_page20':'实体分页（20 条）',
             'warm.exact_record_page':'精确记录筛选','warm.keyword_page20':'关键词筛选',
             'warm.history':'实体修订历史','warm.neighborhood_hops1':'1 跳邻域',
             'warm.semantic':'Milvus 语义检索','warm.hybrid':'混合检索',
             'warm.graph_manifest':'图谱会话清单','concurrent4.semantic':'4 并发语义检索'}
    selected = {}
    for source in ('normal-http-read-v2','normal-search','current-http'):
        if source not in sources:
            continue
        for key, value in sources[source]['summaries'].items():
            if key in names and value['samples'] >= 20:
                selected[key] = {'name':names[key], 'source':source,'category':key,
                             **{k:value[k] for k in ('samples','successful_samples','failed_samples','p50_seconds','p95_seconds','max_seconds')},
                             'target_seconds':2, 'passed':value['successful_samples']>=20 and value['failed_samples']==0 and value['p95_seconds'] is not None and value['p95_seconds']<=2}
    rows = list(selected.values())
    selection = browser['selection']
    active = browser['frames']['active']
    heap = browser['memory'].get('max_js_heap_bytes')
    full_active = (load['first_batch_ms'] + load['resume_ms'])/1000
    stability = browser.get('observed_stability_seconds',0)
    stable_ok = stability >= 1800 and browser['stability']['count_mismatch_checks']==0 and not browser['stability']['errors'] and not browser['stability']['context_lost_events']
    gates = [
        {'name':'全量实体/关系完整性','target':'150001 / 300000，唯一、无悬空','actual':load['validation'],'passed':load['counts_match'] and not any(load['validation'][k] for k in ('duplicate_entity_ids','duplicate_relation_ids','dangling_relations'))},
        {'name':'完整加载','target':'≤30 秒','actual':{'samples':1,'active_seconds':full_active,'wall_seconds':load['all_drawn_ms']/1000,'pause_seconds_approx':load['all_drawn_ms']/1000-full_active},'passed':full_active<=30},
        {'name':'活动帧间隔 P95','target':'≤33ms','actual':active,'passed':active['count']>0 and active['p95_ms']<=33},
        {'name':'点选到详情可见','target':'≤200ms','actual':{**selection,'timeout_observation_window_ms':15000},'passed':selection['attempts']>=20 and not selection['timeouts'] and not selection['empty_picks'] and selection['latency']['p95_ms']<=200},
        {'name':'JS 堆峰值','target':'≤1.5GiB','actual':heap,'passed':heap is not None and heap<=1.5*1024**3},
        {'name':'浏览器 RSS','target':'≤3GiB','actual':'当前浏览器工具无法读取单页/浏览器 RSS','passed':None},
        {'name':'满载连续观察','target':'≥30 分钟，无计数漂移/上下文丢失/脚本错误','actual':{'seconds':stability,**browser['stability']},'passed':stable_ok},
    ]
    report = {'schema':1,'created_utc':datetime.now(timezone.utc).isoformat(),
              'verdict':'NOT_PASSED','project_id':'46249548-2189-4ad7-8f23-70ee2bcc7f6f',
              'project_name':'容量验收 · 15万实体 / 30万关系（保留测试）',
              'dataset':{'entities':150001,'relations':300000,'synthetic_capacity_data':True,'retrieval_relevance_certified':False},
              'http_rows':rows,'gates':gates,'full_graph':load,'browser':browser,
              'full_graph_pipeline_observed':pipeline,'critical_source_changes_during_normal_start_and_checks':source_changes,
              'runtime_evidence':[{'source':key,'started_at':value.get('started_at'),'server_pid':value.get('context',{}).get('server_pid'),'openapi_sha256':value.get('openapi_sha256')} for key,value in sources.items() if value.get('openapi_sha256')],
              'audited_writes':sources['audited-writes'],
              'normal_writes':sources.get('normal-writes'),
              'normal_write_measurement_note':'普通服务投影维护仍在运行，未追加普通服务写入样本；无仪表写入延迟列为未认证。诊断轮5对修改/恢复验证了PG/CAS，未验证写后Milvus追平。',
              'current_search_error':sources.get('current-search-error'),
              'projection_consistency':sources.get('projection-consistency'),
              'ui_final':sources.get('ui-final'),
              'runtime_handoff':sources.get('runtime-handoff'),
              'deep_neighborhood_exploratory':sources['deep-neighborhood']['summaries'],
              'final_verification':sources.get('final-verification'),
              'harness_verification':sources.get('harness-verification'),
              'trace':trace_summary(private_dir/'capacity-server-trace.jsonl'),
              'observers':[observer_summary(private_dir/name) for name in ('capacity-normal-observer.jsonl','capacity-audited-observer.jsonl')],
              'source_files':[{'file':PREFIX+key+'.json','sha256':hashlib.sha256((data_dir/(PREFIX+key+'.json')).read_bytes()).hexdigest()} for key in sources],
              'limitations':[
                  '正常 HTTP 延迟：固定样例，每类预热后 20 次；最近秩 P95；不代表随机查询分布或更多并发用户。',
                  'normal-http-read-v2 在完成所列 20 次采样类别后人为停止更深跳数校准；取消不是 HTTP 业务失败。',
                  '全图只有 1 次完整加载；墙钟包含约 92 秒人工/自动化暂停，应同时看约 229 秒活动加载，均超过 30 秒；没有全图 P95。',
                  '全图使用正常服务；后台清单和并发检索测试部分时段与全图校准重叠。持续观察期间叠加诊断写入与少量深邻域请求。',
                  '3/10 跳各 3 次为并行负载下探索结果，不能作为 20 次独立热测认证。',
                  '活动帧指标是受信任鼠标操作附近的 rAF 间隔，并非 GPU 帧耗时；点选计时到基础实体身份可见，不包含后续证据/本体历史加载。',
                  '整个浏览器观测窗口还记录了 67 次主线程长任务（P95 91ms、最大 183ms），非活动 rAF 最大间隔 4133.3ms；活动帧达标不等于整个流程绝无停顿。',
                  '点选成功样本 P95 单列；1/20 在 15 秒诊断窗口内未观察到所选实体详情身份，计为超时，未与服务日志中的具体请求唯一关联。',
                  'JS 堆不是浏览器/显存总占用；RSS 未测，不判通过。浏览器诊断包装有额外开销，最终完整性扫描约 87ms。',
                  '诊断服务记录同步阶段耗时与锁归属；嵌套阶段不能相加，driver execute 不能称为纯 SQL 执行时间。',
                  '主机与 Docker 为共享资源环境，CPU/内存压力会影响墙钟；交换文件占用不等于发生了换页 I/O。',
                  '共享工作区在测试期间存在外部源码更新；已运行的服务无热重载。早期正常服务、诊断服务、最终重启后的复核分别保留 PID 与 OpenAPI 指纹，不把它们当作同一构建的对照实验。',
                  '测量结束后服务又从PID49500切换为PID36216（04:50:38启动，04:55:20健康200）；新实例未做性能认证，不将既有P95或维护状态快照归给它。进程退出原因未判定，不能称OOM或崩溃。',
                  '04:47:46普通服务日志另记录重建快照records_by_version_ids的PostgreSQL语句超时（QueryCanceled）；它与应用锁等待分别记录，不把全部SQL等待解释成锁表。',
                  '本轮只修改专用测试实体并恢复正文及可写字段；修订历史正常增加，不测试所有本体写入/批量导入。',
                  '诊断写入的HTTP/CAS校验通过不代表向量追平通过；本轮发现投影rebuilding与水位落后，当前普通服务语义/混合检索为503。',
                  '满载观察保留同一图谱会话快照；没有认证外部HTTP修改后前端全图自动增量更新。',
                  '本地报告的file协议自动浏览器预览被策略阻止；未尝试替代浏览器或HTTP绕行，采用离线结构、数据与资源链接检查。',
                  '合成实体及重复真实模型向量仅用于容量验证，不验证检索相关性、抽取质量或答案质量。',
              ]}
    return report


def render(report):
    e = html.escape
    def badge(value):
        return '<span class="badge '+('pass' if value else 'fail' if value is False else 'unknown')+'">'+('通过' if value else '未通过' if value is False else '未认证')+'</span>'
    def f(value, digits=3):
        return '—' if value is None else f'{value:.{digits}f}'
    http = ''.join('<tr><td>'+e(r['name'])+'<br><small>'+('最终重启复核' if r['source']=='current-http' else '早期正常服务')+'</small></td><td>'+str(r['samples'])+'</td><td>'+f(r['p50_seconds'])+'</td><td>'+f(r['p95_seconds'])+'</td><td>'+f(r['max_seconds'])+'</td><td>'+str(r['failed_samples'])+'</td><td>'+badge(r['passed'])+'</td></tr>' for r in report['http_rows'])
    hybrid_p95 = next(r['p95_seconds'] for r in report['http_rows'] if r['category']=='warm.hybrid')
    hybrid_card = f(hybrid_p95)+'s' if hybrid_p95 is not None else 'HTTP 503'
    b = report['browser']; load=report['full_graph']; sel=b['selection']; frames=b['frames']['active']
    active=(load['first_batch_ms']+load['resume_ms'])/1000
    gate_text=[
        '150,001 实体 / 300,000 关系；重复与悬空均为 0',
        f'活动加载 {active:.1f}s；墙钟 {load["all_drawn_ms"]/1000:.1f}s（含暂停）；N=1',
        f'{frames["p95_ms"]:.1f}ms；{frames["count"]:,} 个活动样本',
        f'19 次成功 P95 {sel["latency"]["p95_ms"]:.1f}ms；20 次尝试中 {sel["timeouts"]} 次未在 15s 窗口内观察到详情身份',
        f'{b["memory"]["max_js_heap_bytes"]/1024**2:.1f}MiB，仅 JS 堆',
        '工具未提供浏览器 RSS，不能据 JS 堆推断',
        f'{b["observed_stability_seconds"]/60:.1f} 分钟；计数漂移 {b["stability"]["count_mismatch_checks"]}；WebGL 上下文丢失 {b["stability"]["context_lost_events"]}；脚本错误 {len(b["stability"]["errors"])}',
    ]
    gates=''.join('<tr><td>'+e(g['name'])+'</td><td>'+e(g['target'])+'</td><td>'+e(t)+'</td><td>'+badge(g['passed'])+'</td></tr>' for g,t in zip(report['gates'],gate_text))
    writes=''
    for row in report['trace']['completed_puts']:
        p=row['nested_phase_seconds_do_not_sum']
        writes+='<tr><td><code>'+e(row['request_id'][:8])+'</code></td><td>'+str(row['status'])+'</td><td>'+f(row['duration_seconds'])+'</td>'+''.join('<td>'+f(p.get(k))+'</td>' for k in ('repo.snapshot_data','repo.current_records','repo.reserve_operation','encoder.encode','formal.execute_actions','service.projection_nudge'))+'</tr>'
    normal=report.get('normal_writes')
    normal_text=report['normal_write_measurement_note']
    if normal:
        pairs=normal['write_pairs']; s=normal.get('summaries',{})
        normal_text=f'恢复普通服务后 {len(pairs)} 对修改/恢复，'+('全部恢复原内容。' if all(p.get('restored') for p in pairs) else '存在未完成恢复，必须核对。')
        for key,label in [('write.governed_edit','修改'),('write.governed_restore','恢复'),('write_control.stale_version_cas','旧版本拒绝')]:
            if key in s:
                v=s[key]; normal_text+=f' {label} N={v["samples"]}，范围 {v["p50_seconds"]:.3f}～{v["max_seconds"]:.3f}s。'
        normal_text+=' 样本少，报告观察值，不作稳定 P95 认证。'
    obs=''.join('<tr><td>'+e(o['source'])+'</td><td>'+str(o['resource_samples'])+'</td><td>'+f(o['host_cpu_percent']['p50'],1)+' / '+f(o['host_cpu_percent']['p95'],1)+'%</td><td>'+f((o['host_memory_available_bytes']['min'] or 0)/1024**3,2)+'GiB</td><td>'+f((o['server_rss_bytes']['max'] or 0)/1024**3,2)+'GiB</td><td>'+str(o['pg_samples'])+'</td><td>'+str(o['samples_with_blockers'])+' / '+str(o['samples_with_ungranted_locks'])+'</td></tr>' for o in report['observers'])
    links=''.join('<li><a href="'+e(x['file'])+'">'+e(x['file'])+'</a><small> SHA-256 '+e(x['sha256'])+'</small></li>' for x in report['source_files'])
    limits=''.join('<li>'+e(t)+'</li>' for t in report['limitations'])
    return '''<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>15 万节点性能重新认证 · 2026-10-21</title><style>
:root{color-scheme:light;--ink:#163e3c;--muted:#5d716d;--line:#d2ded9;--paper:#f6f5ef;--red:#973e2f}*{box-sizing:border-box}body{margin:0;background:var(--paper);color:var(--ink);font:16px/1.75 "Microsoft YaHei","Noto Sans CJK SC",sans-serif}main{max-width:1240px;margin:auto;padding:50px 36px 90px}header{border-top:8px solid var(--ink);padding-top:28px}header small,.eyebrow{letter-spacing:2px;color:var(--muted)}h1{font-family:"SimSun",serif;font-size:44px;line-height:1.3;margin:12px 0 22px}h2{font-family:"SimSun",serif;font-size:26px;margin:0 0 18px}p{max-width:1000px}nav{display:flex;gap:22px;flex-wrap:wrap;border-block:1px solid var(--line);padding:14px 0;margin:28px 0}a{color:inherit;text-underline-offset:4px}.verdict{padding:20px 24px;border-left:5px solid var(--red);background:#f3e8dd}.verdict strong{color:var(--red);font-size:24px}section{margin-top:48px;scroll-margin-top:24px}.cards{display:grid;grid-template-columns:repeat(3,1fr);gap:18px;margin:26px 0}.card{border-bottom:2px solid var(--ink);padding:10px 0 18px}.card b{display:block;font:38px/1.4 Georgia,serif}.card small{color:var(--muted)}.table{overflow:auto;border:1px solid var(--line);background:#fff}table{border-collapse:collapse;width:100%;font-size:14px}th,td{padding:12px 14px;border-bottom:1px solid var(--line);text-align:left;vertical-align:top}th{background:#e6eeea;white-space:nowrap}td code{font-size:12px}.badge{display:inline-block;white-space:nowrap;font-size:12px;border:1px solid;padding:1px 9px;border-radius:3px}.pass{color:#20664f;background:#edf5ed}.fail{color:#973e2f;background:#fbefdf}.unknown{color:#6e6e64;background:#f0f0eb}.note{font-size:14px;color:var(--muted);margin:12px 0}.image{max-width:100%;border:1px solid var(--line)}figure{margin:22px 0}figcaption{font-size:13px;color:var(--muted)}li{margin-bottom:10px}small{overflow-wrap:anywhere}details{margin:20px 0}summary{cursor:pointer;font-weight:bold}footer{margin-top:50px;border-top:1px solid var(--line);padding-top:20px;color:var(--muted)}@media(max-width:700px){main{padding:24px 18px}h1{font-size:32px}.cards{grid-template-columns:1fr}.card b{font-size:30px}table{min-width:850px}}@media print{main{max-width:none;padding:12px}nav{display:none}.table{overflow:visible}section{break-inside:avoid}a{color:inherit}}
</style></head><body><main><header><small>KNOWLEDGE SERVICE / CAPACITY AUDIT / 2026.10.21</small><h1>15 万节点，重新认证</h1><p>真实应用、保留项目、PostgreSQL 权威存储及 Milvus 向量检索。分别核对完整性、交互流畅度、查询延迟、正式写入与持续稳定性。</p><div class="verdict"><strong>总体：性能验收未通过</strong><br>全量图谱可以加载并持续运行；检索、点选详情、完整加载与单条正式写入仍存在明显延迟。不能据“能画出 15 万点”宣称整体性能达标。</div></header>
<nav><a href="#gates">验收结论</a><a href="#http">HTTP 延迟</a><a href="#graph">全图与交互</a><a href="#writes">写入卡点</a><a href="#resources">资源与锁</a><a href="#limits">边界及证据</a></nav>
<div class="cards"><div class="card"><small>已验证完整数据</small><b>150,001 / 300,000</b><span>实体 / 关系</span></div><div class="card"><small>混合检索 · 最后一次 20 次热测</small><b>'''+hybrid_card+'''</b><span>成功样本 P95 目标 ≤2s，失败不计入成功延迟</span></div><div class="card"><small>完整加载 · 单次</small><b>'''+f(active,1)+'''s</b><span>活动加载，目标 ≤30s</span></div></div>
<section id="gates"><h2>逐项验收</h2><div class="table"><table><thead><tr><th>指标</th><th>目标</th><th>实测</th><th>判定</th></tr></thead><tbody>'''+gates+'''</tbody></table></div><p class="note">达标仅对这次列明的样例、设备与观测范围有效；未取得的指标保留“未认证”。</p></section>
<section id="http"><h2>正常服务的 HTTP 延迟与可用性</h2><p>预热后每类 20 次，P95 用最近秩计算，失败单列；4 并发为 20 个总请求。以下均通过真实 HTTP 路由。</p><div class="verdict"><strong>最后普通服务复核：检索不可用</strong><br>语义与混合检索各20次全部503，提示“Milvus 投影未就绪或落后于权威版本”。没有成功样本P95，不能把快速拒绝算作性能达标。详情与分页仍通过。</div><div class="table"><table><thead><tr><th>操作</th><th>N</th><th>P50 / 秒</th><th>P95 / 秒</th><th>最大 / 秒</th><th>失败</th><th>≤2s</th></tr></thead><tbody>'''+http+'''</tbody></table></div><p class="note">写入前的早期正常服务：语义P95=5.744s、混合P95=7.371s，各20次成功，Milvus、degraded=false；首个语义预热106.593s另列。这些与最后的503属于不同阶段，不能混为一组。3跳/10跳各3次探索最大2.373s /42.396s，非独立20次认证。</p><p>04:44–04:48的独立只读检查：权威序号29，游标处于rebuilding（snapshot/applied23），旧Milvus目标有450,001物理行、17个索引完成，标记最大仅13。重建状态的applied23不等于旧目标已追平到23。诊断写入期间已经记录ProjectionLeaseLost。04:48:45同一构建又有合法租约与450,001个持久快照ID，因此不能认定永久无人运行，也不重复启动或强改ready标记。详见投影一致性JSON。</p></section>
<section id="graph"><h2>全量图谱：能加载，等待时间未达标</h2><p>首批可见约 4.0 秒。一次完整加载墙钟约 320.9 秒，其中约 92 秒为首批后人工/自动化暂停；累计活动加载约 228.9 秒。共 1,126 次 next/page 调用。只测完整加载 1 次，没有全图 P95。</p><p>已核对加载器逐页await后继续：1,126个分页HTTP平均约172.4ms，累计约194.2s；它包含服务、传输和调度。Renderer.sync的CPU方法平均17.264ms、P95 44.9ms，draw平均0.117ms。分页链路的累积等待明显；这些不同范围的耗时不能当作互斥分账相加，GPU完成耗时未测。</p><figure><img class="image" src="2026-10-21-capacity-complete.jpg" alt="实际产品加载 150001 个实体与 300000 条关系"><figcaption>实际应用的全量图谱；密集总览用于容量观察，日常理解仍需局部、聚合和按需展开。</figcaption></figure><p>完成时独立扫描核对了唯一 ID 与边端点，重复实体、重复关系、悬空关系均为 0；关系缓存 300,000，WebGL error=0。活动帧与点选分别统计：帧流畅不代表后端详情和后续证据能及时返回。</p></section>
<section id="writes"><h2>单条写入为什么会慢</h2><p>专用测试实体的每次普通修改仍做两次项目级快照，合计读取 900,002 行当前记录。首个修改的约 173 秒中，快照约 119 秒、预留操作时间约 26 秒，编码约 0.67 秒，实际动作约 0.94 秒。下列阶段有嵌套，不能相加。</p><div class="table"><table><thead><tr><th>请求 ID 前缀</th><th>状态</th><th>HTTP / 秒</th><th>两次快照</th><th>其中全量记录</th><th>预留操作</th><th>编码</th><th>动作执行</th><th>投影推进</th></tr></thead><tbody>'''+writes+'''</tbody></table></div><p>旧版本修改虽然正确返回 409，却在两次快照和预留操作之后才检查单记录 CAS：首次拒绝也等了约 163 秒。该拒绝动作阶段只查版本，未写入新业务版本。应在正式写入设计中尽早检查版本，并缩小快照与临界区；本轮未实施这些优化。</p><p><strong>普通服务复核：</strong>'''+e(normal_text)+'''</p></section>
<section id="resources"><h2>资源争用、应用锁与数据库锁分别看</h2><p>已关联的本体请求约 81.97 秒，其中约 81.68 秒在等待应用 writer RLock；锁拥有者正是恢复 PUT。证据请求也观察到约 81.55 秒应用锁等待。独立 reader 的关联锁等待最大仅约 9µs（首阶段 516 次），说明不同读取路径表现不同。</p><div class="table"><table><thead><tr><th>观察窗口</th><th>资源样本</th><th>CPU 中位 / P95</th><th>最少可用内存</th><th>服务 RSS 峰值</th><th>PG 样本</th><th>有 blockers / 未授予锁样本</th></tr></thead><tbody>'''+obs+'''</tbody></table></div><p>首 3 次 PUT 的 540 个 PostgreSQL 样本未见 Lock wait、阻塞 PID 或未授予锁，采样最大间隔约 1.45 秒；不能排除更短暂等待。实际持有 ShareRowExclusiveLock，且存在较长事务，因此“未见等待”不等于“没有锁”。ClientWrite 与 idle in transaction / ClientRead 表明网络传输和客户端处理间隙存在，不能把驱动 execute 墙钟全算作数据库计算。</p><p class="note">主机 32GiB、16 逻辑核的共享测试环境。主机 CPU 多次接近 100%；Milvus 单次 Docker 采样约 891% CPU。资源压力是测试条件，尚不足以替代代码阶段计时确定全部因果。</p></section>
<section id="limits"><h2>数据保留、测试边界与复查</h2><p>测试项目继续保留在项目管理中：<strong>容量验收 · 15万实体 / 30万关系（保留测试）</strong>。普通API复核150,001实体、300,000关系，测试实体原可写字段已恢复，修订5→15。健康检查200，但语义检索503；“保留数据”与“检索可用”分别判定。新鲜页面另记录初始化提示 <code>syncGeneratedIri is not defined</code>，项目卡片仍可查看数量，本轮未修复该UI问题。</p><figure><img class="image" src="2026-10-21-capacity-retained-project.jpg" alt="当前普通应用项目管理中的保留项目及准确计数，页面同时提示初始化错误"><figcaption>普通应用最终入口核对；红色提示为实际观察到的页面初始化问题。</figcaption></figure><ol>'''+limits+'''</ol><details><summary>原始证据及校验值</summary><ul>'''+links+'''</ul><p>完整汇总：<a href="2026-10-21-capacity-recertification.json">capacity-recertification.json</a>。原始服务/主机跟踪保留在本机私有诊断目录，汇总只公开数字和请求关联。</p></details></section><footer>生成时间 UTC '''+e(report['created_utc'])+''' · 本轮为性能重新认证，未将“正确性通过”替代“性能通过”。</footer></main></body></html>'''


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--data-dir', type=Path, default=ROOT/'docs/research')
    parser.add_argument('--private-dir', type=Path, required=True)
    args=parser.parse_args()
    report=build(args.data_dir,args.private_dir)
    out=args.data_dir/(PREFIX+'recertification')
    out.with_suffix('.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    out.with_suffix('.html').write_text(render(report),encoding='utf-8')
    print(json.dumps({'verdict':report['verdict'],'http_rows':len(report['http_rows']),'html':str(out.with_suffix('.html'))},ensure_ascii=False))


if __name__ == '__main__':
    main()
