"""Build the offline redesign manual and check coverage, links and route mappings.

Only reads source and documentation; does not import the application, connect to a
database, start a browser or execute a business API. Run with --build after edits.
The renderer supports the deliberately small Markdown vocabulary used by this
manual and needs no third-party packages.
"""
from __future__ import annotations

import argparse
import ast
import html
from html.parser import HTMLParser
import re
from pathlib import Path
import struct


ROOT = Path(__file__).resolve().parents[1]
MARKDOWN = ROOT / "docs/2026-10-20-知识平台完整操作手册.md"
OUTPUT = ROOT / "docs/知识平台完整操作手册.html"
EDITION = "2026.10.22 · 生产检索与统一写入版"
FIELDS = ("场景", "为什么与何时", "前置身份与条件", "操作步骤", "正确结果", "失败与恢复", "下一步")
ILLUSTRATED_CHAPTERS = (2, 3, 4, 6, 7, 9, 11, 13, 14, 15, 17, 18, 20, 21, 23, 24)
COVERAGE = {
    1: ("候选事实", "正式术语", "资料库", "本体建模层"),
    2: ("superadmin", "viewer", "重置口令", "自己的口令", "停用", "删除"),
    3: ("use_default_ontology", "创建项目", "删除", "cleaned"),
    4: ("具体知识", "结构", "documents", "ontology", "discovery", "切片", "引用"),
    5: ("路径 A", "按已有本体", "正式发布", "待确认", "不应因普通上传增加", "advisory/review/strict/off"),
    6: ("路径 B", "待完善结构", "预演", "加入本轮修改", "目标草案", "依赖", "复用", "按来源", "逐条关联知识预期结果", "预计跳过", "处理回执"),
    7: ("路径 C", "新增定义", "第一次保存", "关联知识 0 条", "历史积压候选", "未发布"),
    8: ("ZIP", "并发", "逐文件", "断点", "失败切片", "strict", "阈值"),
    9: ("OCR", "source_id", "断言", "修订记录", "固定版本"),
    10: ("单值属性", "约束异常", "审核理由", "申请本体变更"),
    11: ("类型卡片", "层级树", "domain", "range", "多父类", "IRI", "中间父类", "发现父类", "来源 A", "来源 B", "先前手工差异"),
    12: ("SHACL", "RDF", "Turtle", "SPARQL", "源码差异"),
    13: ("草案", "逐条审核", "发布清单", "基线", "发布 ID", "逐来源", "0 条", "本次发布实际处理的关联知识", "候选 ID", "快照"),
    14: ("source_ontology_id", "selected_fields", "预演", "迁移", "撤销", "只读"),
    15: ("实体", "关系", "独立属性", "软删除", "恢复", "预期修订"),
    16: ("别名", "查重", "融合", "attribute_winners", "消歧", "撤销"),
    17: ("Metadata", "业务时点", "系统历史", "or", "本页"),
    18: ("关键词", "语义", "混合", "Milvus", "本体扩展", "失败"),
    19: ("Local/OAG", "冻结原文", "结构待定", "有效依据", "最终依据校验"),
    20: ("局部图", "脑图", "已加载图", "路径", "截断"),
    21: ("全量大图", "清单", "取消", "不完整", "WebGL", "标签数量"),
    22: ("precision", "recall", "F1", "标准答案", "精确"),
    23: ("消融", "basic", "document_only", "no_graph", "no_ontology", "combined", "manifest", "评分题数", "导出"),
    24: ("embedding", "Milvus", "ontology-index", "Neo4j", "重试", "权威"),
    25: ("快照", "恢复前自动备份", "导出", "新修订"),
    26: ("r1", "r2", "ont-A", "ont-C", "G7", "水位"),
    27: ("Global", "DRIFT", "项目成员 ACL", "跨草案", "150,000", "P95", "容量验收", "独立测量"),
    28: ("现象", "409", "预演过期", "保存", "Milvus", "恢复", "对象 ID"),
    29: ("主要 API", "实现依据", "修订", "OpenAPI", "交付复核", "selection-preview", "add-selection", "confirm-reuse", "source_fingerprint", "idempotency_key"),
}


CSS = """
:root{--paper:#f6f3eb;--ink:#172b29;--muted:#5a6c65;--green:#174d42;--lime:#d6ed99;--line:#d7dfd3;--side:304px;color-scheme:light}
*{box-sizing:border-box}html{scroll-behavior:smooth;scroll-padding-top:30px}body{margin:0;background:var(--paper);color:var(--ink);font:16px/1.85 'Microsoft YaHei','PingFang SC','Noto Sans CJK SC',sans-serif}
a{color:var(--green);text-decoration-thickness:1px;text-underline-offset:4px}a:hover{color:#7c4920}button,input{font:inherit}button{cursor:pointer}button:focus-visible,a:focus-visible,input:focus-visible{outline:3px solid #cf8b37;outline-offset:4px}
.skip{position:fixed;left:12px;top:-100px;background:#fff;padding:12px;z-index:20}.skip:focus{top:12px}.rail{position:fixed;inset:0 auto 0 0;width:var(--side);background:var(--green);color:#f5f3e7;display:flex;flex-direction:column;border-right:1px solid #123e34;z-index:10}
.brand{padding:30px 25px 18px;border-bottom:1px solid #ffffff24}.brand small{display:block;letter-spacing:.2em;font-size:10px;color:#cadac5}.brand strong{font:30px/1.5 'STSong','SimSun',serif;letter-spacing:.12em}.brand p{margin:9px 0 0;color:#d6e4d4;font-size:13px}.search{padding:18px 20px 12px}.search label{display:block;font-size:12px;margin-bottom:7px;color:#e4eddc}.search input{width:100%;border:1px solid #ffffff47;border-radius:5px;padding:9px 11px;background:#113e35;color:white;font-size:14px}.search input::placeholder{color:#b3cbbd}.search-status{min-height:23px;font-size:11px;margin:7px 0 0;color:#d7e5cb}.toc{overflow:auto;padding:0 12px 22px;flex:1;scrollbar-width:thin;scrollbar-color:#809b83 transparent}.toc a{display:grid;grid-template-columns:28px 1fr;gap:8px;color:#e4ecde;text-decoration:none;padding:9px 11px;border-left:2px solid transparent;font-size:13px;line-height:1.55;border-radius:2px}.toc a b{font:12px/1.8 Georgia,serif;opacity:.7}.toc a:hover{background:#ffffff10}.toc a.active{border-left-color:var(--lime);background:#ffffff0d;color:#f3ffdc}.toc a[hidden]{display:none}.rail-foot{padding:12px 24px;border-top:1px solid #ffffff24;font-size:11px;color:#c8d6bd}.rail-foot a{color:inherit}.mobile-menu{display:none}
main{margin-left:var(--side);max-width:1460px;padding:56px clamp(28px,5vw,88px) 70px}.cover{border-bottom:1px solid var(--line);padding-bottom:32px;margin-bottom:32px}.eyebrow{font:11px/1.6 Georgia,serif;letter-spacing:.2em;color:var(--green)}h1{font:clamp(29px,3.2vw,46px)/1.45 'STSong','SimSun',serif;letter-spacing:.04em;margin:12px 0 20px}.edition{display:flex;gap:8px;flex-wrap:wrap;margin-bottom:22px}.edition span{font-size:11px;line-height:1.6;padding:4px 9px;border:1px solid var(--line);border-radius:2px;color:var(--muted)}.cover p{max-width:900px}.frontmatter p{color:#3d514b}.chapter{padding:24px 0 44px;border-bottom:1px solid var(--line);scroll-margin-top:25px;max-width:1120px}.chapter h2{font:clamp(23px,2.2vw,31px)/1.55 'STSong','SimSun',serif;margin:4px 0 29px;color:var(--ink);letter-spacing:.025em}.chapter h3{font-size:15px;line-height:1.6;margin:27px 0 10px;font-weight:700;color:var(--green);border-left:3px solid #a6bd75;padding-left:11px}.chapter p{margin:9px 0 15px}.chapter ol,.chapter ul{padding-left:27px}.chapter li{padding:4px 0 6px 5px}.chapter li::marker{color:var(--green);font-weight:bold}.chapter strong{font-weight:700}.chapter code,.frontmatter code{font:13px/1.7 Consolas,'Cascadia Code',monospace;padding:2px 5px;background:#e9eee2;border:1px solid #dce3d3;border-radius:3px;overflow-wrap:anywhere}.chapter pre{background:#e8eee2;border:1px solid #d1dcc8;border-left:4px solid #809d63;padding:19px 22px;overflow:auto;border-radius:3px;line-height:1.7}.chapter pre code{border:0;padding:0;background:none;font-size:13px;white-space:pre}.table-scroll{overflow:auto;margin:20px 0 25px;border:1px solid var(--line);border-radius:3px}table{border-collapse:collapse;width:100%;min-width:560px;font-size:13px;line-height:1.75;background:#fbfaf6}th{text-align:left;background:#e6ecdf;color:#244f42;font-weight:700}th,td{padding:13px 14px;vertical-align:top;border-bottom:1px solid var(--line)}td+td,th+th{border-left:1px solid #e4e8dd}tr:last-child td{border-bottom:0}td code{font-size:11px!important}td a{white-space:normal}img{max-width:100%;height:auto;border:1px solid var(--line);border-radius:4px}.chapter-nav{display:flex;gap:16px;justify-content:space-between;font-size:12px;margin-top:29px;color:var(--muted)}.chapter-nav a{text-decoration:none}.no-results{display:none;color:#ecf7c9;padding:10px;font-size:13px}.reading{position:fixed;right:24px;bottom:20px;background:#fffdf4;border:1px solid var(--line);padding:7px 12px;font-size:11px;color:var(--muted);box-shadow:0 3px 12px #15352d0c;border-radius:3px}.footer{padding-top:25px;font-size:12px;color:var(--muted)}
@media(min-width:1700px){main{margin-right:auto}.chapter{max-width:1180px}}
@media(max-width:950px){:root{--side:250px}main{padding:35px 28px}.brand{padding:24px 20px}.toc a{font-size:12px}}
@media(max-width:700px){.mobile-menu{display:block;position:sticky;top:0;z-index:12;width:100%;text-align:left;border:0;background:var(--green);color:#fff;padding:13px 20px;font-size:14px}.rail{display:none;width:min(88vw,340px);top:50px;box-shadow:15px 0 50px #0003}.rail.open{display:flex}main{margin:0;padding:30px 20px 55px}.cover{padding-bottom:22px}.chapter{padding-top:20px}.chapter h2{font-size:24px}.reading{display:none}.chapter li{padding-left:2px}th,td{padding:11px}.chapter-nav{gap:20px}.chapter pre{padding:15px}}
@media(prefers-reduced-motion:reduce){html{scroll-behavior:auto}}
@media print{body{font-size:10pt;background:#fff}.rail,.mobile-menu,.reading,.chapter-nav,.skip{display:none!important}main{margin:0;padding:0;max-width:none}.cover{break-after:page}h2,h3{break-after:avoid}.chapter{padding:15px 0;max-width:none}.chapter h2{font-size:20pt}table{min-width:0;font-size:8pt}tr{break-inside:avoid}a{color:inherit}pre{white-space:pre-wrap}code{overflow-wrap:anywhere}.table-scroll{overflow:visible}}
"""

JS = """
(()=>{'use strict';
const chapters=[...document.querySelectorAll('.chapter')],links=[...document.querySelectorAll('.toc a')],search=document.getElementById('manual-search'),status=document.getElementById('search-status'),rail=document.getElementById('rail'),toggle=document.getElementById('menu-toggle');
const text=chapters.map(section=>section.textContent.toLocaleLowerCase());
search.addEventListener('input',()=>{const q=search.value.trim().toLocaleLowerCase();let count=0;links.forEach((link,index)=>{const match=!q||text[index].includes(q);link.hidden=!match;if(match)count++;});status.textContent=q?'找到 '+count+' 章；点击章节继续阅读':'按标题、步骤、接口或错误关键词搜索';document.getElementById('no-results').style.display=count?'none':'block';});
search.addEventListener('keydown',event=>{if(event.key==='Escape'){search.value='';search.dispatchEvent(new Event('input'));}if(event.key==='Enter'){const first=links.find(link=>!link.hidden);if(first)first.click();}});
toggle.addEventListener('click',()=>{const open=rail.classList.toggle('open');toggle.setAttribute('aria-expanded',String(open));if(open)search.focus();});
links.forEach(link=>link.addEventListener('click',()=>{rail.classList.remove('open');toggle.setAttribute('aria-expanded','false');}));
const setActive=id=>{links.forEach(link=>link.classList.toggle('active',link.hash==='#'+id));const section=document.getElementById(id);if(section)document.getElementById('reading').textContent=section.querySelector('h2').textContent;};
if('IntersectionObserver'in window){const visible=new Map();const observer=new IntersectionObserver(entries=>{entries.forEach(entry=>entry.isIntersecting?visible.set(entry.target.id,entry.boundingClientRect.top):visible.delete(entry.target.id));const first=chapters.find(section=>visible.has(section.id));if(first)setActive(first.id);},{rootMargin:'-5% 0px -65% 0px',threshold:0});chapters.forEach(section=>observer.observe(section));}
window.addEventListener('hashchange',()=>{const id=location.hash.slice(1);if(document.getElementById(id)?.classList.contains('chapter'))setActive(id);});
setActive(location.hash.slice(1)||chapters[0]?.id);
})();
"""


def inline(value: str) -> str:
    pattern = re.compile(r"`([^`]+)`|\*\*([^*]+)\*\*|!\[([^\]]*)\]\(([^)]+)\)|\[([^\]]+)\]\(([^)]+)\)")
    result: list[str] = []
    start = 0
    for match in pattern.finditer(value):
        result.append(html.escape(value[start:match.start()]))
        code, bold, alt, image, label, link = match.groups()
        if code is not None:
            result.append("<code>" + html.escape(code) + "</code>")
        elif bold is not None:
            result.append("<strong>" + inline(bold) + "</strong>")
        elif image is not None:
            result.append('<img loading="lazy" src="' + html.escape(image, quote=True) + '" alt="' + html.escape(alt, quote=True) + '">')
        else:
            result.append('<a href="' + html.escape(link, quote=True) + '">' + inline(label) + '</a>')
        start = match.end()
    result.append(html.escape(value[start:]))
    return "".join(result)


def split_chapters(source: str) -> tuple[str, list[tuple[int, str, str]]]:
    matches = list(re.finditer(r'<a id="chapter-(\d{2})"></a>\s*\n## (\d{2})｜([^\n]+)', source))
    front = source[:matches[0].start()] if matches else source
    chapters: list[tuple[int, str, str]] = []
    for index, match in enumerate(matches):
        if match[1] != match[2]:
            raise ValueError("Chapter anchor and title disagree")
        end = matches[index + 1].start() if index + 1 < len(matches) else len(source)
        chapters.append((int(match[1]), match[3], source[match.end():end].strip()))
    return front, chapters


def render_blocks(source: str, chapter_id: str) -> str:
    lines = source.splitlines()
    result: list[str] = []
    index = 0
    heading = 0
    while index < len(lines):
        line = lines[index]
        if not line.strip():
            index += 1
            continue
        if line.startswith("# "):
            index += 1
            continue
        if line.startswith("### "):
            heading += 1
            result.append(f'<h3 id="{chapter_id}-part-{heading}">{inline(line[4:])}</h3>')
            index += 1
            continue
        if line.startswith("```"):
            language = line[3:].strip()
            code: list[str] = []
            index += 1
            while index < len(lines) and not lines[index].startswith("```"):
                code.append(lines[index])
                index += 1
            result.append('<pre><code' + (' data-language="' + html.escape(language, quote=True) + '"' if language else '') + '>' + html.escape("\n".join(code)) + '</code></pre>')
            index += 1
            continue
        if line.startswith("|"):
            rows: list[list[str]] = []
            while index < len(lines) and lines[index].startswith("|"):
                cells = [cell.strip() for cell in lines[index].strip().strip("|").split("|")]
                if not all(re.fullmatch(r":?-+:?", cell) for cell in cells):
                    rows.append(cells)
                index += 1
            table = '<div class="table-scroll" tabindex="0" role="region" aria-label="可横向滚动的对照表"><table><thead><tr>'
            table += "".join("<th scope=\"col\">" + inline(cell) + "</th>" for cell in rows[0])
            table += "</tr></thead><tbody>"
            table += "".join("<tr>" + "".join("<td>" + inline(cell) + "</td>" for cell in row) + "</tr>" for row in rows[1:])
            result.append(table + "</tbody></table></div>")
            continue
        match = re.match(r"^(\d+\. |[-*] )(.*)", line)
        if match:
            ordered = match[1][0].isdigit()
            tag = "ol" if ordered else "ul"
            items: list[str] = []
            while index < len(lines):
                next_match = re.match(r"^(\d+\. |[-*] )(.*)", lines[index])
                if not next_match or next_match[1][0].isdigit() != ordered:
                    break
                items.append("<li>" + inline(next_match[2]) + "</li>")
                index += 1
            result.append("<" + tag + ">" + "".join(items) + "</" + tag + ">")
            continue
        paragraph = [line]
        index += 1
        while index < len(lines) and lines[index].strip() and not re.match(r"^(### |```|\||\d+\. |[-*] )", lines[index]):
            paragraph.append(lines[index])
            index += 1
        result.append("<p>" + inline(" ".join(paragraph)) + "</p>")
    return "\n".join(result)


def build_html(source: str) -> str:
    front, chapters = split_chapters(source)
    toc = "\n".join(f'<a href="#chapter-{number:02d}"><b>{number:02d}</b><span>{html.escape(title)}</span></a>' for number, title, _ in chapters)
    sections: list[str] = []
    for index, (number, title, body) in enumerate(chapters):
        navigation: list[str] = []
        if index:
            prev, name, _ = chapters[index - 1]
            navigation.append(f'<a href="#chapter-{prev:02d}">← {prev:02d} {html.escape(name)}</a>')
        if index + 1 < len(chapters):
            following, name, _ = chapters[index + 1]
            navigation.append(f'<a href="#chapter-{following:02d}">{following:02d} {html.escape(name)} →</a>')
        chapter_id = f"chapter-{number:02d}"
        sections.append(f'<section class="chapter" id="{chapter_id}" aria-labelledby="{chapter_id}-title"><h2 id="{chapter_id}-title">{number:02d}｜{html.escape(title)}</h2>\n' + render_blocks(body, chapter_id) + '<div class="chapter-nav">' + "".join(navigation) + '</div></section>')
    return '<!doctype html>\n<html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><meta name="description" content="知序知识平台完整操作手册：资料、本体、事实治理、检索问答、图谱、评测、任务和版本。"><title>知序 · 知识平台完整操作手册</title><style>' + CSS + '</style></head><body>\n<a class="skip" href="#content">跳到正文</a><button class="mobile-menu" id="menu-toggle" aria-expanded="false" aria-controls="rail">☰ 目录与全文搜索</button><aside class="rail" id="rail"><div class="brand"><small>KNOWLEDGE WORKSPACE</small><strong>知序 · 操作手册</strong><p>让每一次操作，都有来路与结果。</p></div><div class="search"><label for="manual-search">搜索手册</label><input id="manual-search" type="search" autocomplete="off" placeholder="例如：恢复、Milvus、属性冲突"><p class="search-status" id="search-status" role="status">按标题、步骤、接口或错误关键词搜索</p></div><nav class="toc" aria-label="手册章节">' + toc + '<p class="no-results" id="no-results">没有匹配章节。试试对象名或操作名。</p></nav><div class="rail-foot">' + html.escape(EDITION) + ' · 可离线阅读 / 打印<br><a href="' + html.escape(MARKDOWN.name, quote=True) + '">Markdown 源文</a></div></aside><main id="content"><header class="cover"><div class="eyebrow">FIELD GUIDE / 2026.10.21</div><h1>知识平台<br>完整操作手册</h1><div class="edition"><span>' + str(len(chapters)) + ' 章 · 从资料到可追溯答案</span><span>场景 / 原理 / 步骤 / 恢复</span><span>静态离线版</span></div><div class="frontmatter">' + render_blocks(front, "front") + '</div></header>\n' + "\n".join(sections) + '<footer class="footer">文档结构和静态映射由 scripts/verify_redesign_manual.py 检查。功能、权限与容量验证以独立验收记录为准。</footer></main><div class="reading" id="reading" aria-hidden="true"></div><script>' + JS + '</script></body></html>\n'


class HtmlInventory(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.ids: list[str] = []
        self.links: list[str] = []
        self.assets: list[tuple[str, str]] = []
        self.remote: list[str] = []
        self.scripts: list[str] = []
        self.in_script = False
        self.chapter_count = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = dict(attrs)
        if values.get("id"):
            self.ids.append(values["id"])
        if tag == "a" and values.get("href"):
            self.links.append(values["href"])
        if tag == "img":
            self.assets.append((values.get("src", ""), values.get("alt", "")))
        if tag in ("script", "link", "img", "iframe", "source"):
            target = values.get("src") or values.get("href") or ""
            if target.startswith(("http:", "https:", "//")):
                self.remote.append(target)
        if tag == "script":
            self.in_script = True
        if tag == "section" and "chapter" in (values.get("class") or "").split():
            self.chapter_count += 1

    def handle_endtag(self, tag: str) -> None:
        if tag == "script":
            self.in_script = False

    def handle_data(self, data: str) -> None:
        if self.in_script:
            self.scripts.append(data)


def normalize_route(path: str) -> str:
    return re.sub(r"\{[^}]+\}", "{}", path.rstrip("/"))


def image_dimensions(payload: bytes) -> tuple[int, int] | None:
    """Read PNG/JPEG header dimensions, without decoding pixels or proving origin."""
    if payload.startswith(b"\x89PNG\r\n\x1a\n") and len(payload) >= 24:
        return struct.unpack(">II", payload[16:24])
    if not payload.startswith(b"\xff\xd8"):
        return None
    offset = 2
    sof = {0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF}
    while offset < len(payload):
        if payload[offset] != 0xFF:
            return None
        while offset < len(payload) and payload[offset] == 0xFF:
            offset += 1
        if offset >= len(payload):
            return None
        marker = payload[offset]
        offset += 1
        if marker in {0xD9, 0xDA}:
            return None
        if marker == 0x01 or 0xD0 <= marker <= 0xD7:
            continue
        if offset + 2 > len(payload):
            return None
        length = int.from_bytes(payload[offset:offset + 2], "big")
        if length < 2 or offset + length > len(payload):
            return None
        if marker in sof and length >= 8:
            height, width = struct.unpack(">HH", payload[offset + 3:offset + 7])
            return width, height
        offset += length
    return None


def source_routes() -> set[tuple[str, str]]:
    routes: set[tuple[str, str]] = set()
    sources = list((ROOT / "knowledge_service/api").glob("*.py"))
    sources.append(ROOT / "knowledge_service/integrations/neo4j_store.py")
    for path in sources:
        tree = ast.parse(path.read_text(encoding="utf-8-sig"), filename=str(path))
        prefixes: dict[str, str] = {}
        for node in ast.walk(tree):
            if isinstance(node, ast.Assign) and isinstance(node.value, ast.Call):
                call = node.value
                if isinstance(call.func, ast.Name) and call.func.id == "APIRouter":
                    prefix = next((kw.value.value for kw in call.keywords if kw.arg == "prefix" and isinstance(kw.value, ast.Constant)), "")
                    for target in node.targets:
                        if isinstance(target, ast.Name):
                            prefixes[target.id] = prefix
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            for decorator in node.decorator_list:
                if not isinstance(decorator, ast.Call) or not isinstance(decorator.func, ast.Attribute):
                    continue
                func = decorator.func
                if func.attr not in {"get", "post", "put", "patch", "delete"} or not decorator.args or not isinstance(decorator.args[0], ast.Constant):
                    continue
                if not isinstance(func.value, ast.Name):
                    continue
                route = prefixes.get(func.value.id, "") + decorator.args[0].value
                routes.add((func.attr.upper(), normalize_route(route)))
    return routes


def verify(source: str, built: str) -> list[str]:
    errors: list[str] = []
    _, chapters = split_chapters(source)
    numbers = [number for number, _, _ in chapters]
    if numbers != list(COVERAGE):
        errors.append(f"Expected chapters 01–{len(COVERAGE):02d} in order")
    for number, title, body in chapters:
        for field in FIELDS:
            if len(re.findall(r"^### " + re.escape(field) + r"$", body, re.M)) != 1:
                errors.append(f"Chapter {number:02d}: missing or repeated field {field}")
        if len(body) < 350:
            errors.append(f"Chapter {number:02d}: insufficient explanatory content")
        for token in COVERAGE.get(number, ()):
            if token not in title + body:
                errors.append(f"Chapter {number:02d}: missing topic {token}")
        if number in ILLUSTRATED_CHAPTERS and not re.search(r"^!\[[^\]]+\]\(manual-redesign/[^)]+\)$", body, re.M):
            errors.append(f"Chapter {number:02d}: missing current UI screenshot coverage")
    if re.search(r"\bTODO\b|\bTBD\b|待补截图|占位截图|lorem ipsum", source, re.I):
        errors.append("Placeholder content found")
    inventory = HtmlInventory()
    inventory.feed(built)
    if inventory.chapter_count != len(COVERAGE):
        errors.append("HTML chapter count does not match Markdown")
    if len(inventory.ids) != len(set(inventory.ids)):
        errors.append("Duplicate HTML anchors")
    if inventory.remote:
        errors.append("Offline HTML includes remote assets: " + ", ".join(inventory.remote))
    for link in inventory.links:
        if link.startswith("#"):
            if link[1:] not in inventory.ids:
                errors.append("Broken anchor: " + link)
        elif not link.startswith(("https://", "http://", "mailto:")):
            target = OUTPUT.parent / link.split("#", 1)[0]
            if not target.is_file():
                errors.append("Broken local link: " + link)
    for source_path, alt in inventory.assets:
        path = OUTPUT.parent / source_path
        if not alt.strip() or not path.is_file():
            errors.append("Missing screenshot file or alt text: " + source_path)
        elif not (size := image_dimensions(path.read_bytes())) or size[0] < 700 or size[1] < 450:
            errors.append("Screenshot must have a readable PNG/JPEG header and desktop dimensions: " + source_path)
        if "manual-redesign/" not in source_path.replace("\\", "/"):
            errors.append("Screenshot must be from current real UI capture directory: " + source_path)
    for feature in ("manual-search", "menu-toggle", "IntersectionObserver", "@media print", "prefers-reduced-motion"):
        if feature not in built:
            errors.append("Missing offline-reader feature: " + feature)
    if built != build_html(source):
        errors.append("HTML is stale; rebuild with --build")
    routes = source_routes()
    route_count = 0
    for methods, path in re.findall(r"\b((?:GET|POST|PUT|PATCH|DELETE)(?:/(?:GET|POST|PUT|PATCH|DELETE))*) (/api/[^`；\s]+)", source):
        # /retry after a preceding complete route is explanatory shorthand,
        # while every full /api/... mapping is checked against route source.
        for method in methods.split("/"):
            route_count += 1
            if (method, normalize_route(path)) not in routes:
                errors.append("Unmapped API: " + method + " " + path)
    if route_count < 65:
        errors.append("API coverage unexpectedly small")
    return errors


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--build", action="store_true", help="Regenerate the offline HTML before checking")
    args = parser.parse_args()
    source = MARKDOWN.read_text(encoding="utf-8-sig")
    if args.build:
        OUTPUT.write_text(build_html(source), encoding="utf-8", newline="\n")
    if not OUTPUT.is_file():
        print("FAIL: HTML does not exist; run with --build")
        return 1
    errors = verify(source, OUTPUT.read_text(encoding="utf-8"))
    if errors:
        for error in errors:
            print("FAIL: " + error)
        return 1
    inventory = HtmlInventory()
    inventory.feed(OUTPUT.read_text(encoding="utf-8"))
    print(f"PASS: {len(COVERAGE)} chapters; 7 explanatory fields each; topic coverage; API mappings; local links; {len(inventory.ids)} unique anchors; {len(inventory.assets)} current UI screenshots; offline assets and reader features")
    print("Scope: static documentation verification only; no browser, application, database or capacity acceptance was executed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
