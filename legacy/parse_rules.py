# -*- coding: utf-8 -*-
"""
把所有规则 PDF/DOCX/DOC 解析为带格式的 Markdown，扁平输出到 rule_txt/。

格式还原：
- 标题：`#`（文档标题）、`##`（第X章）、`###`（第X条 / 一、 / （一）等小节）
- 行内格式：**加粗**、*斜体*
- DOCX 表格：标准 Markdown 表格
- PDF：按字号/加粗/标题模式识别，跨行自动合并段落

文件名：<一级>_<二级>_<三级>_<四级>_<规则名>.md（同名 + _规则ID）
"""
import os, re, time, hashlib, threading, zipfile, xml.etree.ElementTree as ET
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed

import fitz
import pandas as pd

_W = 'http://schemas.openxmlformats.org/wordprocessingml/2006/main'

def qw(tag):
    return f'{{{_W}}}{tag}'

ROOT = Path(__file__).resolve().parent
SRC_DIR = ROOT / 'rules'
XLSX = ROOT / '美团规则中心-规则全览分级表（364条）.xlsx'
OUT_DIR = ROOT / 'rule_txt'
OUT_DIR.mkdir(exist_ok=True)
MAX_WORKERS = 8

COL_ID = '规则ID'
COL_NAME = '规则名称'
COL_L1 = '一级分类'
COL_L2 = '二级分类（业务线）'
COL_L3 = '三级分类（子模块）'
COL_L4 = '四级分类（细分类）'

_unclassified = '未分类'
_invalid_fs = re.compile(r'[<>:"/\\|?*\x00-\x1f]')

_CN = '一二三四五六七八九十百千万零'
_CHAPTER_RE = re.compile(r'^\s*第[%s0-9]+章' % _CN)
_ART_RE = re.compile(r'^\s*第[%s0-9]+条' % _CN)
_CN_HEAD_RE = re.compile(r'^\s*[%s]+、' % _CN)
_NUM_HEAD_RE = re.compile(r'^\s*[（(][%s0-9]+[）)]' % _CN)

NOISE_RE = re.compile(r'^\s*\d{1,3}\s*$')


def safe_seg(s, max_len=40):
    s = (s or '').strip().replace('\u3000', ' ')
    s = _invalid_fs.sub('_', s)
    s = re.sub(r'\s+', ' ', s)
    if len(s) > max_len:
        h = hashlib.md5(s.encode('utf-8')).hexdigest()[:6]
        s = s[:max_len - 7] + '_' + h
    return s


def is_blank(v):
    if v is None:
        return True
    if isinstance(v, float) and pd.isna(v):
        return True
    if not isinstance(v, str):
        v = str(v)
    return v.strip() == ''


def heat_text(s):
    """去除 markdown 特殊字符干扰"""
    return s.replace('|', '\\|') if s else s


# ── 标题识别 ─────────────────────────────────────────────────────
def heading_level(text, strong=False, is_title=False):
    text = text.strip()
    if not text:
        return None
    if is_title:
        return 1
    if _CHAPTER_RE.match(text):
        return 2
    if _CN_HEAD_RE.match(text) and len(text) <= 30:
        return 3
    if _NUM_HEAD_RE.match(text) and len(text) <= 30:
        return 3
    if strong:
        if _ART_RE.match(text) or _CN_HEAD_RE.match(text) or _NUM_HEAD_RE.match(text):
            return 3
        if len(text) <= 30 and not re.search(r'[。；；！？\u3002]', text):
            return 3
    return None


# ── 行内 Markdown（强调） ─────────────────────────────────────────
def runs_to_md(runs):
    md = []
    for text, bold, italic in runs:
        if not text:
            continue
        t = heat_text(text)
        if bold and italic:
            t = f'***{t}***'
        elif bold:
            t = f'**{t}**'
        elif italic:
            t = f'*{t}*'
        md.append(t)
    return ''.join(md)


# ═══ DOCX 解析 ═══════════════════════════════════════════════════
def parse_docx(p):
    with zipfile.ZipFile(p) as z:
        xml = z.read('word/document.xml')
    root = ET.fromstring(xml)
    body = root.find(qw('body'))
    elements = []

    def para_runs(p_el):
        runs = []
        for r in p_el.iter(qw('r')):
            text = ''.join(t.text or '' for t in r.iter(qw('t')))
            if not text.strip():
                continue
            rpr = r.find(qw('rPr'))
            bold = rpr is not None and rpr.find(qw('b')) is not None
            ital = rpr is not None and rpr.find(qw('i')) is not None
            runs.append((text, bold, ital))
        return runs

    def para_meta(p_el):
        """返回 (text_dedup, bold_ratio, max_sz, centered, style)"""
        ppr = p_el.find(qw('pPr'))
        style = ppr.find(qw('pStyle')) if ppr is not None else None
        style_val = style.get(qw('val')) if style is not None else None
        jc_el = ppr.find(qw('jc')) if ppr is not None else None
        jc = jc_el.get(qw('val')) if jc_el is not None else None
        runs = para_runs(p_el)
        text = ''.join(t for t, _, _ in runs)
        if not text.strip():
            return None
        bold_chars = sum(len(t) for t, b, _ in runs if b)
        total = len(text)
        max_sz = 0
        for r in p_el.iter(qw('r')):
            rpr = r.find(qw('rPr'))
            if rpr is not None:
                szel = rpr.find(qw('sz'))
                if szel is not None:
                    try:
                        max_sz = max(max_sz, int(szel.get(qw('val'))))
                    except Exception:
                        pass
        return text, (bold_chars / total) if total else 0, max_sz, jc == 'center', style_val

    is_first = True
    def walk(el):
        nonlocal is_first
        for child in list(el):
            tag = child.tag
            if tag == qw('p'):
                meta = para_meta(child)
                if meta is None:
                    continue
                text, bold_ratio, max_sz, centered, style = meta
                if is_first:
                    is_first = False
                    strong_title = centered or max_sz >= 30 or bold_ratio >= 0.9
                    if strong_title:
                        elements.append(('title', 1, runs_to_md(para_runs(child))))
                        continue
                strong = bold_ratio >= 0.6 or max_sz >= 26 or centered
                lvl = heading_level(text, strong=strong)
                if lvl:
                    elements.append(('heading', lvl, runs_to_md(para_runs(child))))
                else:
                    elements.append(('para', None, runs_to_md(para_runs(child))))
            elif tag == qw('tbl'):
                elements.append(('table', None, extract_table(child)))
            elif tag == qw('sdt'):
                sc = child.find(qw('sdtContent'))
                if sc is not None:
                    walk(sc)
            else:
                walk(child)

    def extract_table(tbl):
        rows = []
        for tr in tbl.iter(qw('tr')):
            cells = []
            for tc in tr.iter(qw('tc')):
                cell_parts = []
                for cp in tc.iter(qw('p')):
                    s = runs_to_md(para_runs(cp)).replace('\n', ' ').strip()
                    if s and s not in cell_parts:
                        cell_parts.append(s)
                cells.append(' '.join(cell_parts) if cell_parts else ' ')
            if cells:
                rows.append(cells)
        return rows

    walk(body)
    return elements


# ═══ PDF 解析 ════════════════════════════════════════════════════
def is_bold_font(font):
    if not font:
        return False
    low = font.lower()
    if 'bold' in low:
        return True
    if low in ('simhei', 'heiti', 'simhei-regular', 'stheitimedium'):
        return True
    return False


def parse_pdf(p):
    doc = fitz.open(p)
    n_pages = doc.page_count
    lines = []
    for pn in range(n_pages):
        page = doc[pn]
        d = page.get_text('dict')
        for blk in d['blocks']:
            if 'lines' not in blk:
                continue
            for ln in blk['lines']:
                spans = ln['spans']
                if not spans:
                    continue
                text = ''.join(sp['text'] for sp in spans)
                if not text.strip():
                    continue
                if NOISE_RE.match(text):
                    continue
                y0 = min(sp['bbox'][1] for sp in spans)
                y1 = max(sp['bbox'][3] for sp in spans)
                fonts = [sp['font'] for sp in spans if sp['text'].strip()]
                sizes = [sp['size'] for sp in spans if sp['text'].strip()]
                max_size = max(sizes)
                bold_fl = any(sp.get('flags', 0) & 16 for sp in spans if sp['text'].strip())
                bold_fn = any(is_bold_font(sp['font']) for sp in spans if sp['text'].strip())
                strong = bold_fl or bold_fn or max_size > 13.0
                lines.append({
                    'text': text,
                    'y0': y0, 'y1': y1,
                    'max_size': max_size,
                    'strong': strong,
                    'spans': spans,
                    'page': pn + 1,
                })
    doc.close()

    if not lines:
        note = (f'> 注意：本文档为扫描图片（共{n_pages}页），不含文本图层，'
                f'无法直接提取文字，请人工核对原始 PDF。')
        return [('para', None, note)]

    sizes = sorted(l['max_size'] for l in lines)
    body_size = sizes[len(sizes) // 2]
    title_line = None
    if lines and lines[0]['max_size'] >= body_size * 1.25 and lines[0]['page'] == 1:
        title_line = lines[0]

    elements = []
    para_buf = []
    last_y1 = None
    last_size = None

    def flush():
        nonlocal para_buf
        if para_buf:
            elements.append(('para', None, ''.join(para_buf)))
            para_buf = []

    for li in lines:
        text = li['text'].strip()
        if not text:
            continue
        strong = li['strong']
        if title_line is li:
            flush()
            elements.append(('title', 1, heat_text(text)))
            continue
        lvl = heading_level(text, strong=strong)
        if lvl == 2:
            flush()
            elements.append(('heading', 2, heat_text(text)))
            continue
        if lvl == 3:
            flush()
            elements.append(('heading', 3, heat_text(text)))
            continue
        # 段落行合并
        if para_buf and last_y1 is not None:
            gap = li['y0'] - last_y1
            size_diff = (last_size is not None) and abs(li['max_size'] - last_size) > 0.6
            if gap > 9 or size_diff:
                flush()
        para_buf.append(spanlines_to_md(li['spans']))
        last_y1 = li['y1']
        last_size = li['max_size']
    flush()
    return elements


def spanlines_to_md(spans):
    parts = []
    for sp in spans:
        t = sp['text']
        if not t:
            continue
        bold = (sp.get('flags', 0) & 16) or is_bold_font(sp['font'])
        t = heat_text(t)
        if bold:
            t = f'**{t}**'
        parts.append(t)
    return ''.join(parts)


# ═══ DOC 转换（Word COM） ═════════════════════════════════════════
_DOC_LOCK = threading.Lock()


def parse_doc(p):
    import win32com.client
    import pythoncom
    pythoncom.CoInitialize()
    try:
        with _DOC_LOCK:
            word = None
            try:
                word = win32com.client.DispatchEx('Word.Application')
                try:
                    word.Visible = False
                except Exception:
                    pass
                word.DisplayAlerts = 0
                doc = word.Documents.Open(str(p.resolve()), ReadOnly=True)
                try:
                    text = doc.Content.Text
                finally:
                    doc.Close(False)
            finally:
                if word is not None:
                    try:
                        word.Quit()
                    except Exception:
                        pass

            elements = []
            first = True
            for line in text.split('\r'):
                t = line.strip()
                if not t:
                    continue
                if first:
                    first = False
                    elements.append(('title', 1, heat_text(t)))
                    continue
                if _CHAPTER_RE.match(t):
                    elements.append(('heading', 2, heat_text(t)))
                elif _ART_RE.match(t):
                    elements.append(('heading', 3, heat_text(t)))
                elif (_CN_HEAD_RE.match(t) or _NUM_HEAD_RE.match(t)) and len(t) <= 40:
                    elements.append(('heading', 3, heat_text(t)))
                else:
                    elements.append(('para', None, heat_text(t)))
            return elements
    finally:
        pythoncom.CoUninitialize()


# ═══ Markdown 渲染 ════════════════════════════════════════════════
def render_md(elements):
    buf = []
    for el in elements:
        kind = el[0]
        if kind == 'title':
            buf.append(f'# {el[2]}\n')
        elif kind == 'heading':
            lvl = el[1] if el[1] else 2
            buf.append(f'\n{"#" * lvl} {el[2]}\n')
        elif kind == 'para':
            buf.append(f'{el[2]}\n')
        elif kind == 'table':
            rows = el[2]
            if not rows:
                continue
            ncol = max(len(r) for r in rows)
            buf.append('\n')
            for i, row in enumerate(rows):
                cells = (row + [''] * ncol)[:ncol]
                buf.append('| ' + ' | '.join(cells) + ' |\n')
                if i == 0:
                    buf.append('|' + '|'.join(['---'] * ncol) + '|\n')
            buf.append('\n')
    md = ''.join(buf).strip()
    return md + '\n'


# ═══ 文件匹配与主流程 ═════════════════════════════════════════════
def build_prefix(row):
    parts = []
    for c in (COL_L1, COL_L2, COL_L3, COL_L4):
        v = row.get(c)
        if is_blank(v):
            continue
        seg = safe_seg(str(v).strip())
        if seg == _unclassified:
            continue
        parts.append(seg)
    return ('_'.join(parts) + '_') if parts else ''


def find_src_files():
    out = {}
    for f in SRC_DIR.rglob('*'):
        if not f.is_file() or f.suffix.lower() not in ('.pdf', '.docx', '.doc'):
            continue
        m = re.search(r'_(\d+)\.[a-z]+$', f.name, re.I)
        if m:
            out.setdefault(m.group(1), []).append(f)
        else:
            out.setdefault('', []).append(f)
    return out


def parse_one(row, src_map, used_by, stats, lock):
    name = str(row.get(COL_NAME, '')).strip()
    rid_raw = row.get(COL_ID, None)
    link = row.get('规则链接', None)

    rid = None
    if not is_blank(rid_raw):
        try:
            rid = str(int(float(rid_raw)))
        except Exception:
            rid = None

    if not rid:
        with lock:
            stats['failed'].append({'规则ID': rid_raw, '规则名称': name, '原文件': '', '原因': '规则ID为空'})
        return (name, 'failed', 'no-id')

    if is_blank(link):
        with lock:
            stats['missing'].append({'规则ID': rid, '规则名称': name, '原始分类路径': row.get('原始分类路径', '')})
        return (name, 'missing', 'no-link')

    candidates = src_map.get(rid, [])
    if not candidates:
        with lock:
            stats['failed'].append({'规则ID': rid, '规则名称': name, '原文件': '', '原因': '本地未找到源文件'})
        return (name, 'failed', 'local-not-found')
    fp = candidates[0]

    prefix = build_prefix(row)
    base = safe_seg(name, max_len=120)
    out_name = f"{prefix}{base}.md"

    with lock:
        prev_rid = used_by.get(out_name)
        if prev_rid is not None and prev_rid != rid:
            out_name = f"{prefix}{base}_{rid}.md"
        used_by[out_name] = rid

    out = OUT_DIR / out_name

    if out.exists() and out.stat().st_size > 0:
        with lock:
            stats['skipped'] += 1
        return (out_name, 'skipped', '')

    suf = fp.suffix.lower()
    try:
        if suf == '.pdf':
            md = render_md(parse_pdf(fp))
        elif suf == '.docx':
            md = render_md(parse_docx(fp))
        elif suf == '.doc':
            md = render_md(parse_doc(fp))
        else:
            with lock:
                stats['failed'].append({'规则ID': rid, '规则名称': name, '原文件': fp.name, '原因': f'未知扩展名 {suf}'})
            return (out_name, 'failed', f'unknown:{suf}')

        if not md.strip():
            with lock:
                stats['failed'].append({'规则ID': rid, '规则名称': name, '原文件': fp.name, '原因': '解析结果为空'})
            return (out_name, 'failed', 'empty-text')

        if not re.match(r'^#\s', md):
            md = f'# {name}\n\n' + md

        out.write_text(md, encoding='utf-8')
        with lock:
            stats['ok'] += 1
            stats['bytes'] += len(md.encode('utf-8'))
        return (out_name, 'ok', f'{len(md)}c')
    except Exception as e:
        with lock:
            stats['failed'].append({'规则ID': rid, '规则名称': name, '原文件': fp.name, '原因': f'{type(e).__name__}: {e}'})
        return (out_name, 'failed', str(e)[:120])


def main():
    t0 = time.time()
    df = pd.read_excel(XLSX, header=0)
    rows = [row for _, row in df.iterrows()]
    print(f'读取 {len(df)} 条规则')

    src_map = find_src_files()
    print(f'本地源文件 {sum(len(v) for v in src_map.values())} 个')

    stats = {'ok': 0, 'bytes': 0, 'skipped': 0, 'failed': [], 'missing': []}
    lock = threading.Lock()
    used_by = {}

    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as ex:
        futs = {ex.submit(parse_one, r, src_map, used_by, stats, lock): r for r in rows}
        done = 0
        for fut in as_completed(futs):
            name, status, msg = fut.result()
            done += 1
            if status == 'failed':
                print(f'  X {name} :: {msg[:80]}')
            if done % 50 == 0 or done == len(rows):
                print(f'[{done}/{len(rows)}] ok={stats["ok"]} skipped={stats["skipped"]} '
                      f'failed={len(stats["failed"])} missing={len(stats["missing"])}')

    fail_p = OUT_DIR / '_parse_failed.txt'
    with open(fail_p, 'w', encoding='utf-8') as fh:
        fh.write(f"解析失败清单  共 {len(stats['failed'])} 条\n\n")
        for it in stats['failed']:
            fh.write(f"{it['规则ID']}\t{it['规则名称']}\t{it['原文件']}\t{it['原因']}\n")

    rep = OUT_DIR / '解析报告.md'
    with open(rep, 'w', encoding='utf-8') as fh:
        fh.write("# 美团规则文档 解析报告\n\n")
        fh.write(f"- 输入：`rules/`（按分类组织的原始文件）\n")
        fh.write(f"- 输出：`rule_txt/`（Markdown，标题/加粗/段落/表格还原）\n")
        fh.write(f"- 命名：`<一级>_<二级>_<三级>_<四级>_<规则名>.md`，'未分类'层跳过\n")
        fh.write(f"- 本地文件按 `规则ID` 匹配\n")
        fh.write(f"- 耗时：{time.time()-t0:.1f}s  并发：{MAX_WORKERS}\n\n")
        fh.write("## 汇总\n\n")
        fh.write(f"- 总条数：{len(rows)}\n")
        fh.write(f"- 解析成功：**{stats['ok']}**\n")
        fh.write(f"- 跳过（已存在）：{stats['skipped']}\n")
        fh.write(f"- 解析失败：**{len(stats['failed'])}**\n")
        fh.write(f"- 无链接（跳过）：**{len(stats['missing'])}**\n")
        fh.write(f"- md 总大小：{stats['bytes']/1024/1024:.1f} MB\n\n")
        if stats['failed']:
            fh.write("## 解析失败\n\n| 规则ID | 规则名称 | 原文件 | 原因 |\n|---|---|---|---|\n")
            for it in stats['failed']:
                fh.write(f"| {it['规则ID']} | {it['规则名称']} | {it['原文件']} | {it['原因']} |\n")

    print(f"\n汇总: ok={stats['ok']} skipped={stats['skipped']} "
          f"failed={len(stats['failed'])} missing={len(stats['missing'])} md={stats['bytes']/1024/1024:.1f}MB")
    print(f'失败清单: {fail_p}')
    print(f'报告: {rep}')


if __name__ == '__main__':
    main()