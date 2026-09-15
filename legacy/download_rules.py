# -*- coding: utf-8 -*-
"""
下载 美团规则中心-规则全览分级表（364条） 中的规则文件到 ./rules/ 下，按4级分类组织。
- 跳过链接为空的规则（最后报告里列出）
- 失败：每个一级分类下生成 _failed.txt 记录规则名+URL+HTTP状态
- 并发6线程
"""
import os, sys, re, time, json, hashlib, threading
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed
from urllib.parse import urlparse
from pathlib import PurePosixPath

import pandas as pd
import requests

ROOT = Path(r'D:/workspace/semantica_demo')
SRC = ROOT / '美团规则中心-规则全览分级表（364条）.xlsx'
OUT = ROOT / 'rules'
OUT.mkdir(exist_ok=True)

UA = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36'
TIMEOUT = 30
MAX_WORKERS = 6
MAX_RETRIES = 0  # 用户选择不重试

# 分类列
COL_L1 = '一级分类'
COL_L2 = '二级分类（业务线）'
COL_L3 = '三级分类（子模块）'
COL_L4 = '四级分类（细分类）'

# 文件名清洗：去路径分隔符 + 控制字符 + 空格折叠 + 长度截断
_invalid_fs = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
def safe_name(name: str, max_len: int = 120) -> str:
    name = name.strip().replace('\u3000', ' ')
    name = _invalid_fs.sub('_', name)
    name = re.sub(r'\s+', ' ', name)
    if len(name) > max_len:
        # 保留前后特征 + 哈希
        h = hashlib.md5(name.encode('utf-8')).hexdigest()[:6]
        name = name[:max_len-7] + '_' + h
    return name


def category_dir(row) -> Path:
    parts = []
    for c in (COL_L1, COL_L2, COL_L3, COL_L4):
        v = row.get(c)
        if pd.isna(v) or str(v).strip() == '':
            v = '未分类'
        parts.append(safe_name(str(v), 60))
    d = OUT
    for p in parts:
        d = d / p
    d.mkdir(parents=True, exist_ok=True)
    return d


def target_path(row, used: dict) -> Path:
    url = str(row['规则链接']).strip()
    suffix = PurePosixPath(urlparse(url).path).suffix.lower()
    if not suffix or len(suffix) > 6:
        # 从 url basename 抽 .xx 或 .xxx 段
        bname = PurePosixPath(urlparse(url).path).name
        import re as _re
        m = _re.search(r'\.[A-Za-z0-9]{1,5}$', bname)
        suffix = (m.group(0) if m else '.bin').lower()
    base = safe_name(str(row['规则名称']))
    rid = int(row['规则ID']) if not pd.isna(row['规则ID']) else None
    # 一律带 _ID，避免规则名相同 / URL文件名差异导致映射错
    name = f"{base}_{rid}{suffix}"
    d = category_dir(row)
    used[(str(d), name)] = True
    return d / name


def download_one(row, used, stats, lock):
    """下载一条规则，返回 (row_id, status, msg)"""
    rid = row['规则ID']
    url = row['规则链接']
    name = row['规则名称']
    if pd.isna(url):
        with lock:
            stats['missing'].append({
                '规则ID': int(rid) if not pd.isna(rid) else None,
                '规则名称': name,
                '一级分类': row[COL_L1],
                '二级分类': row[COL_L2],
                '三级分类': row[COL_L3],
                '四级分类': row[COL_L4],
                '原始分类路径': row['原始分类路径'],
            })
        return (rid, 'missing', '')

    try:
        tgt = target_path(row, used)
    except Exception as e:
        return (rid, 'path-err', str(e))

    # 已存在跳过（断点续传）
    if tgt.exists() and tgt.stat().st_size > 0:
        with lock:
            stats['skipped'].append({'规则ID': int(rid) if not pd.isna(rid) else None, '规则名称': name, '路径': str(tgt.relative_to(OUT))})
        return (rid, 'skipped', str(tgt.relative_to(OUT)))

    try:
        r = requests.get(url, timeout=TIMEOUT, stream=True, headers={'User-Agent': UA})
        if r.status_code != 200:
            with lock:
                stats['failed'].append({
                    '规则ID': int(rid) if not pd.isna(rid) else None,
                    '规则名称': name,
                    '一级分类': row[COL_L1],
                    'URL': url,
                    'HTTP状态': r.status_code,
                    '分类路径': str(tgt.parent.relative_to(OUT)),
                })
            r.close()
            return (rid, 'failed', f'HTTP {r.status_code}')

        # 写入
        tgt.parent.mkdir(parents=True, exist_ok=True)
        total = 0
        ct = r.headers.get('Content-Type', '')
        cl = r.headers.get('Content-Length')
        is_html_error = 'text/html' in ct and ('.pdf' in tgt.name.lower() or '.docx' in tgt.name.lower() or '.doc' in tgt.name.lower())
        with open(tgt, 'wb') as f:
            for chunk in r.iter_content(chunk_size=64 * 1024):
                if chunk:
                    f.write(chunk)
                    total += len(chunk)
        r.close()

        if is_html_error or total < 200:
            # 极有可能是错误页
            with lock:
                stats['failed'].append({
                    '规则ID': int(rid) if not pd.isna(rid) else None,
                    '规则名称': name,
                    '一级分类': row[COL_L1],
                    'URL': url,
                    'HTTP状态': f'200但Content-Type={ct}, 大小={total}',
                    '分类路径': str(tgt.parent.relative_to(OUT)),
                })
            tgt.unlink(missing_ok=True)
            return (rid, 'failed', 'html-error-or-too-small')

        with lock:
            stats['ok'] += 1
            stats['bytes'] += total
        return (rid, 'ok', f'{total}B')
    except Exception as e:
        with lock:
            stats['failed'].append({
                '规则ID': int(rid) if not pd.isna(rid) else None,
                '规则名称': name,
                '一级分类': row[COL_L1],
                'URL': url,
                'HTTP状态': type(e).__name__,
                '分类路径': str(tgt.parent.relative_to(OUT)) if tgt else '',
            })
        return (rid, 'failed', type(e).__name__)


def write_failed_index(stats):
    """按一级分类生成 _failed.txt"""
    by_l1 = {}
    for f in stats['failed']:
        by_l1.setdefault(f['一级分类'] or '未分类', []).append(f)
    for l1, items in by_l1.items():
        d = OUT / (safe_name(str(l1), 60) or '未分类')
        d.mkdir(parents=True, exist_ok=True)
        p = d / '_failed.txt'
        with open(p, 'a', encoding='utf-8') as fh:
            for f in items:
                fh.write(f"[{f['HTTP状态']}] 规则ID={f['规则ID']} {f['规则名称']} | 路径={f['分类路径']} | URL={f['URL']}\n")


def write_report(stats, elapsed):
    p = OUT / '下载报告.md'
    with open(p, 'w', encoding='utf-8') as f:
        f.write(f"# 美团规则中心 文件下载报告\n\n")
        f.write(f"- 源文件：`美团规则中心-规则全览分级表（364条）.xlsx`\n")
        f.write(f"- 目标目录：`rules/`\n")
        f.write(f"- 耗时：{elapsed:.1f} 秒\n")
        f.write(f"- 并发数：{MAX_WORKERS}\n\n")
        f.write("## 汇总\n\n")
        f.write(f"- 总条数：**364**\n")
        f.write(f"- 下载成功：**{stats['ok']}**\n")
        f.write(f"- 已存在跳过：**{len(stats['skipped'])}**\n")
        f.write(f"- 下载失败：**{len(stats['failed'])}**\n")
        f.write(f"- 链接为空：**{len(stats['missing'])}**\n")
        f.write(f"- 总大小：{stats['bytes']/1024/1024:.1f} MB\n\n")

        if stats['missing']:
            f.write("## 链接为空（未下载）\n\n")
            f.write("| 规则ID | 规则名称 | 原始分类路径 |\n|---|---|---|\n")
            for m in stats['missing']:
                f.write(f"| {m['规则ID']} | {m['规则名称']} | {m['原始分类路径']} |\n")
            f.write("\n")

        if stats['failed']:
            f.write("## 下载失败（按一级分类记录在 `_failed.txt`）\n\n")
            f.write("| 规则ID | 规则名称 | HTTP状态 | URL |\n|---|---|---|---|\n")
            for f1 in stats['failed']:
                f.write(f"| {f1['规则ID']} | {f1['规则名称']} | {f1['HTTP状态']} | {f1['URL']} |\n")
            f.write("\n")


def main():
    t0 = time.time()
    df = pd.read_excel(SRC, header=0)
    print(f"读取 {len(df)} 条规则")
    used = {}
    stats = {'ok': 0, 'bytes': 0, 'skipped': [], 'failed': [], 'missing': []}
    lock = threading.Lock()

    rows = [row for _, row in df.iterrows()]

    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as ex:
        futs = {ex.submit(download_one, r, used, stats, lock): r for r in rows}
        done = 0
        for fut in as_completed(futs):
            rid, status, msg = fut.result()
            done += 1
            if done % 30 == 0 or done == len(rows):
                print(f"[{done}/{len(rows)}] ok={stats['ok']} failed={len(stats['failed'])} missing={len(stats['missing'])} skipped={len(stats['skipped'])}")
        # 最后打印
        print(f"[{done}/{len(rows)}] 完成")

    write_failed_index(stats)
    write_report(stats, time.time() - t0)
    print(f"\n汇总: ok={stats['ok']} skipped={len(stats['skipped'])} failed={len(stats['failed'])} missing={len(stats['missing'])} bytes={stats['bytes']/1024/1024:.1f}MB")
    print(f"报告: {OUT / '下载报告.md'}")


if __name__ == '__main__':
    main()
