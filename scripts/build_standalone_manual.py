#!/usr/bin/env python
"""Build standalone, offline-readable HTML manuals from Markdown sources.

Usage:
    python scripts/build_standalone_manual.py [--embed-images] docs/<name>.md [...]

Supports a Markdown subset: h1/h2/h3, paragraphs, tables, fenced code,
ordered/unordered lists, images, links, bold and inline code. The builder
verifies that every relative link target and image exists next to the
output HTML before writing it.
"""
from __future__ import annotations

import html
import base64
import mimetypes
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

CSS = """
:root{--ink:#203c35;--sub:#60726b;--line:#dce4dc;--accent:#25604f;--accent-soft:#edf3e9;--bg:#fffefa}
*{box-sizing:border-box}
html{scroll-behavior:smooth}
body{margin:0;font:15px/1.75 "Segoe UI","Microsoft YaHei",system-ui,sans-serif;color:var(--ink);background:var(--bg)}
a{color:var(--accent);text-decoration:none}a:hover{text-decoration:underline}
code{background:#f2f6fb;border:1px solid var(--line);border-radius:4px;padding:1px 5px;font-family:Consolas,"Courier New",monospace;font-size:.92em}
pre{background:#f7fafd;border:1px solid var(--line);border-radius:10px;padding:14px 16px;overflow:auto}
pre code{background:none;border:none;padding:0;font-size:.88em;line-height:1.6}
.sidebar{position:fixed;top:0;bottom:0;left:0;width:272px;border-right:1px solid var(--line);background:#f4f6ef;padding:24px 18px;overflow:auto}
.brand small{display:block;color:var(--sub);letter-spacing:.12em;font-size:10px}
.brand strong{font-size:19px;color:var(--accent)}
.brand p{margin:4px 0 14px;color:var(--sub);font-size:12px}
.search input{width:100%;padding:8px 10px;border:1px solid var(--line);border-radius:8px;font-size:13px;background:#fff}
.search .hint{color:var(--sub);font-size:11px;margin:6px 0 12px}
nav.toc a{display:block;padding:6px 10px;border-radius:7px;color:#33475b;font-size:13.5px;margin-bottom:2px}
nav.toc a:hover{background:var(--accent-soft);text-decoration:none}
nav.toc a.active{background:var(--accent-soft);color:var(--accent);font-weight:600}
nav.toc a[hidden]{display:none}
main{margin-left:272px;padding:48px 48px 90px;max-width:1280px;min-width:0}
.cover h1{font-size:34px;line-height:1.25;margin:6px 0 4px}
.cover .lead{color:var(--sub);border-bottom:1px solid var(--line);padding-bottom:22px}
section.doc-sec{margin:38px 0;padding-bottom:10px}
section.doc-sec[hidden]{display:none}
h2.sec{font-size:24px;color:var(--ink);border-top:2px solid var(--accent);padding-top:18px;margin:0 0 18px;scroll-margin-top:24px}
h3{font-size:18px;color:var(--accent);margin:28px 0 10px}
figure{margin:18px 0}
figure img{max-width:100%;border:1px solid var(--line);border-radius:10px;box-shadow:0 2px 10px rgba(31,78,121,.08)}
figure figcaption{color:var(--sub);font-size:12.5px;margin-top:6px}
.image-zoom{display:block;padding:0;width:100%;border:0;background:none;cursor:zoom-in;text-align:left}
.image-zoom img{display:block;width:100%;height:auto}
button:focus-visible,a:focus-visible,input:focus-visible,summary:focus-visible{outline:3px solid #b58235;outline-offset:3px}
#m-toc summary{font-weight:600;padding:8px 0;cursor:pointer}
.reader-tools{display:flex;gap:10px;margin:16px 0 0;align-items:center;font-size:12px;color:var(--sub)}
.reader-tools button{border:1px solid var(--line);background:white;border-radius:6px;padding:6px 12px;color:var(--ink);cursor:pointer}
.image-dialog{width:min(96vw,1800px);max-width:96vw;max-height:96vh;padding:16px;border:1px solid var(--line);border-radius:12px;background:var(--bg)}
.image-dialog::backdrop{background:rgba(12,28,22,.78)}
.image-dialog button{position:sticky;top:0;float:right;background:var(--accent);color:white;border:0;border-radius:6px;padding:8px 15px;cursor:pointer}
.image-dialog img{display:block;width:100%;height:auto;margin-top:44px}
.image-dialog p{font-size:13px;color:var(--sub);margin:8px 0}
table{border-collapse:collapse;width:100%;margin:14px 0;font-size:13.5px}
th,td{border:1px solid var(--line);padding:8px 10px;text-align:left;vertical-align:top}
th{background:var(--accent-soft);color:#22415f;font-weight:600}
tbody tr:nth-child(even){background:#fafcff}
.table-scroll{overflow-x:auto}
ul,ol{padding-left:24px}li{margin:4px 0}
.tip{background:var(--accent-soft);border:1px solid #cfe3f6;border-radius:10px;padding:12px 16px;margin:14px 0}
.to-top{position:fixed;right:26px;bottom:26px;background:var(--accent);color:#fff;border:none;border-radius:999px;padding:10px 14px;font-size:13px;cursor:pointer;box-shadow:0 4px 14px rgba(31,78,121,.28)}
@media print{.sidebar,.to-top,.reader-tools,.image-dialog{display:none}main{margin:0;padding:0;max-width:none}body{font-size:11pt;background:white}figure{break-inside:avoid}figure img{box-shadow:none}h2,h3{break-after:avoid}section.doc-sec{break-inside:auto}section.doc-sec[hidden]{display:block}a{color:inherit}.table-scroll{overflow:visible}th,td{padding:6px}}
@media (prefers-reduced-motion:reduce){html{scroll-behavior:auto}}
@media (max-width:960px){.sidebar{position:static;width:auto;border-right:none;border-bottom:1px solid var(--line)}main{margin-left:0;padding:24px 18px}h1{overflow-wrap:anywhere}.cover h1{font-size:28px}table{min-width:540px}nav.toc{max-height:45vh;overflow:auto}}
"""

JS = """
(function(){
 var q=document.getElementById('m-search'),status=document.getElementById('m-status');
 var secs=[].slice.call(document.querySelectorAll('section.doc-sec'));
 var tocs=[].slice.call(document.querySelectorAll('nav.toc a'));
 function txt(e){return e.textContent||e.innerText||''}
 function filter(){
  var v=(q.value||'').trim().toLowerCase();
  var hit=0;
  secs.forEach(function(s,idx){
   var match=!v||txt(s).toLowerCase().indexOf(v)>=0;
   s.hidden=!match;if(match)hit++;
   if(tocs[idx])tocs[idx].hidden=!match;
  });
  status.textContent=v?('命中 '+hit+' 个章节'):'输入关键词过滤章节，支持正文搜索';
 }
 q.addEventListener('input',filter);
 document.addEventListener('keydown',function(e){if(e.key==='/'&&!/INPUT|TEXTAREA|SELECT/.test(document.activeElement.tagName)){e.preventDefault();q.focus()}});
 var toc=document.getElementById('m-toc');
 if(window.matchMedia('(max-width:960px)').matches)toc.open=false;
 document.getElementById('m-print').onclick=function(){window.print()};
 var dialog=document.getElementById('m-image'),large=dialog.querySelector('img'),caption=dialog.querySelector('p');
 document.querySelectorAll('.image-zoom').forEach(function(button){button.onclick=function(){var img=button.querySelector('img');large.src=img.src;large.alt=img.alt;caption.textContent=img.alt;dialog.showModal()}});
 dialog.querySelector('button').onclick=function(){dialog.close()};
 dialog.addEventListener('click',function(e){if(e.target===dialog){var r=dialog.getBoundingClientRect();if(e.clientX<r.left||e.clientX>r.right||e.clientY<r.top||e.clientY>r.bottom)dialog.close()}});
 var io=new IntersectionObserver(function(entries){
  entries.forEach(function(en){ if(en.isIntersecting){
   tocs.forEach(function(a){a.classList.remove('active')});
   var a=document.querySelector('nav.toc a[href="#'+en.target.id+'"]');
   if(a){a.classList.add('active')}
  }});
 },{rootMargin:'-15% 0px -70% 0px'});
 secs.forEach(function(s){io.observe(s)});
 var top=document.createElement('button');top.className='to-top';top.textContent='回到顶部';
 top.onclick=function(){window.scrollTo({top:0,behavior:'smooth'})};document.body.appendChild(top);
})();
"""

TEMPL = """<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>{title}</title><style>{css}</style></head>
<body>
<aside class="sidebar">
 <div class="brand"><small>KNOWLEDGE SERVICE MANUAL</small><strong>{brand}</strong><p>图文操作说明 · 可离线阅读与打印</p></div>
 <div class="search"><input id="m-search" type="search" placeholder="搜索章节，例如：上传 / 索引 / 备份" autocomplete="off"><p class="hint" id="m-status">输入关键词过滤章节，支持正文搜索</p></div>
 <details id="m-toc" open><summary>章节目录</summary><nav class="toc" aria-label="目录">{toc}</nav></details>
</aside>
<main>
<header class="cover"><h1>{title}</h1><div class="lead">{lead}</div><div class="reader-tools"><button id="m-print" type="button">打印 / 保存为 PDF</button><span>点击附图放大 · 按 / 搜索正文</span></div></header>
{body}
</main>
<dialog id="m-image" class="image-dialog" aria-label="附图放大"><button type="button">关闭附图</button><img alt=""><p></p></dialog>
<script>{js}</script>
</body></html>
"""

PARA_STOP = re.compile(r"^(#{1,3} |```|\||!\[|\d+\. |- )")


def inline(text: str) -> str:
    code = []
    marker = '\ue000manual-code'
    while marker in text:
        marker += '_'
    def preserve(match):
        code.append('<code>' + match.group(1) + '</code>')
        return marker + str(len(code) - 1) + marker
    out = re.sub(r"`([^`]+)`", preserve, html.escape(text, quote=False))
    out = re.sub(r"\*\*([^*]+)\*\*", lambda m: "<strong>" + m.group(1) + "</strong>", out)
    out = re.sub(r"\[([^\]]+)\]\(([^)]+)\)",
                 lambda m: '<a href="' + html.escape(m.group(2), quote=True) + '">' + m.group(1) + "</a>", out)
    for index, value in enumerate(code):
        out = out.replace(marker + str(index) + marker, value)
    return out


def tokenize(lines: list[str]):
    """Single-pass block tokenizer. Yields (kind, payload) tuples."""
    i, total = 0, len(lines)
    while i < total:
        line = lines[i]
        if not line.strip():
            i += 1
            continue
        if line.startswith("## "):
            yield "h2", line[3:].strip()
            i += 1
            continue
        if line.startswith("### "):
            yield "h3", line[4:].strip()
            i += 1
            continue
        if line.startswith("```"):
            language = line[3:].strip()
            j = i + 1
            while j < total and not lines[j].startswith("```"):
                j += 1
            yield "code", (language, lines[i + 1:j])
            i = j + 1 if j < total else total
            continue
        if line.startswith("|"):
            rows: list[list[str]] = []
            while i < total and lines[i].startswith("|"):
                cells = [c.strip().replace(r"\|", "|") for c in
                         re.split(r"(?<!\\)\|", lines[i].strip().strip("|"))]
                if cells and not all(re.fullmatch(r":?-+:?", c) for c in cells):
                    rows.append(cells)
                i += 1
            yield "table", rows
            continue
        image = re.match(r"^!\[([^\]]*)\]\(([^)]+)\)$", line.strip())
        if image:
            yield "img", image.groups()
            i += 1
            continue
        if re.match(r"^\d+\. ", line):
            items = []
            while i < total and re.match(r"^\d+\. ", lines[i]):
                items.append(re.sub(r"^\d+\. ", "", lines[i]))
                i += 1
            yield "list", (True, items)
            continue
        if line.startswith("- "):
            items = []
            while i < total and lines[i].startswith("- "):
                items.append(lines[i][2:])
                i += 1
            yield "list", (False, items)
            continue
        para = [line]
        i += 1
        while i < total and lines[i].strip() and not PARA_STOP.match(lines[i]):
            para.append(lines[i])
            i += 1
        yield "p", para


def render(tokens) -> tuple[str, list[tuple[str, str]]]:
    body: list[str] = []
    toc: list[tuple[str, str]] = []
    inside = False
    for kind, payload in tokens:
        if kind == "h2":
            if inside:
                body.append("</section>")
            sid = f"sec-{len(toc) + 1}"
            toc.append((sid, payload))
            body.append(f'<section class="doc-sec" id="{sid}"><h2 class="sec">{inline(payload)}</h2>')
            inside = True
        elif kind == "h3":
            body.append(f"<h3>{inline(payload)}</h3>")
        elif kind == "code":
            language, code = payload
            marker = f' data-language="{html.escape(language, quote=True)}"' if language else ""
            body.append("<pre><code" + marker + ">" + html.escape("\n".join(code)) + "</code></pre>")
        elif kind == "table":
            rows = payload
            head = "".join("<th scope=\"col\">" + inline(c) + "</th>" for c in rows[0])
            rest = "".join("<tr>" + "".join("<td>" + inline(c) + "</td>" for c in row) + "</tr>" for row in rows[1:])
            body.append('<div class="table-scroll"><table><thead><tr>' + head + "</tr></thead><tbody>" + rest + "</tbody></table></div>")
        elif kind == "img":
            alt, src = payload
            body.append('<figure><button type="button" class="image-zoom" aria-label="放大附图：' + html.escape(alt, quote=True) + '"><img src="' + html.escape(src, quote=True) + '" alt="' + html.escape(alt, quote=True) + '"></button>'
                        + (f"<figcaption>{inline(alt)}</figcaption>" if alt.strip() else "") + "</figure>")
        elif kind == "list":
            ordered, items = payload
            tag = "ol" if ordered else "ul"
            body.append(f"<{tag}>" + "".join(f"<li>{inline(item)}</li>" for item in items) + f"</{tag}>")
        elif kind == "p":
            body.append("<p>" + inline(" ".join(payload)) + "</p>")
    if inside:
        body.append("</section>")
    return "\n".join(body), toc


def build(markdown: Path, *, embed_images: bool = False) -> Path:
    text = markdown.read_text(encoding="utf-8-sig")
    lines = text.splitlines()
    title = ""
    first_h2 = len(lines)
    for number, line in enumerate(lines):
        if line.startswith("# ") and not title:
            title = line[2:].strip()
        elif line.startswith("## "):
            first_h2 = number
            break
    lead_html, _ = render(tokenize(lines[1:first_h2]))
    body_html, toc = render(tokenize(lines[first_h2:]))
    toc_html = "\n".join(f'<a href="#{sid}">{html.escape(name)}</a>' for sid, name in toc)
    output = markdown.with_suffix(".html")
    page = TEMPL.format(title=html.escape(title), brand=html.escape(title.split("·")[0].strip() or title),
                        lead=lead_html, toc=toc_html, body=body_html, css=CSS, js=JS)
    problems: list[str] = []
    for href in re.findall(r'href="([^"]+)"', page):
        if href.startswith(("http", "mailto:", "#", "/")):
            continue
        if not (output.parent / href.split("#", 1)[0]).is_file():
            problems.append("missing link target: " + href)
    for src, alt in re.findall(r'<img[^>]*src="([^"]+)"[^>]*alt="([^"]*)"', page):
        if src.startswith("http"):
            continue
        if not (output.parent / src).is_file():
            problems.append("missing image: " + src)
        elif not alt.strip():
            problems.append("image without alt text: " + src)
    if problems:
        for problem in problems:
            print("FAIL: " + problem)
        raise SystemExit(1)
    if embed_images:
        def embed(match):
            src = html.unescape(match.group(2))
            if src.startswith(('http:', 'https:', 'data:')):
                raise ValueError('Offline manuals require local images: ' + src)
            path = output.parent / src
            mime = mimetypes.guess_type(path.name)[0] or 'application/octet-stream'
            encoded = base64.b64encode(path.read_bytes()).decode('ascii')
            return match.group(1) + 'data:' + mime + ';base64,' + encoded + match.group(3)
        page = re.sub(r'(<img\b[^>]*\bsrc=")([^"]+)(")', embed, page)
    figures = len(re.findall(r"<figure>", page))
    tables = len(re.findall(r"<table>", page))
    output.write_text(page, encoding="utf-8", newline="\n")
    print(f"PASS: built {output} ({len(toc)} sections, {figures} figures, {tables} tables)")
    return output


def main() -> int:
    files = [arg for arg in sys.argv[1:] if arg != '--embed-images']
    embed_images = '--embed-images' in sys.argv[1:]
    if not files:
        print(__doc__)
        return 2
    for raw in files:
        build((ROOT / raw) if not Path(raw).is_absolute() else Path(raw), embed_images=embed_images)
    print("Scope: static HTML generation and local asset verification only; no application or capacity acceptance was executed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
