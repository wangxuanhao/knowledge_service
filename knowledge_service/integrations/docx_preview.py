"""Word 原版式预览：直接读取 OOXML 渲染结构，不依赖解析模型或外部转换服务。

只做展示用途，不产生解析结果、切片或索引；图片以 data URI 内联，
超出容量或缺失的资源降级为可见占位提示，不中断其余内容的预览。
"""
from __future__ import annotations

import base64
import html
import io
import posixpath
import zipfile
import xml.etree.ElementTree as ElementTree

W = '{http://schemas.openxmlformats.org/wordprocessingml/2006/main}'
R = '{http://schemas.openxmlformats.org/officeDocument/2006/relationships}'
A = '{http://schemas.openxmlformats.org/drawingml/2006/main}'
PKG_REL = '{http://schemas.openxmlformats.org/package/2006/relationships}'
MAX_IMAGES = 60

STYLE = '''<style>
.docx-preview{font:15px/1.85 -apple-system,"Segoe UI","Microsoft YaHei",sans-serif;color:#1f2d27;padding:4px 2px;word-break:break-word}
.docx-preview h1,.docx-preview h2,.docx-preview h3,.docx-preview h4,.docx-preview h5,.docx-preview h6{line-height:1.5;margin:1.1em 0 .5em;font-weight:600}
.docx-preview h1{font-size:22px}.docx-preview h2{font-size:19px}.docx-preview h3{font-size:17px}
.docx-preview h4,.docx-preview h5,.docx-preview h6{font-size:15px}
.docx-preview p{margin:.55em 0}
.docx-preview table{border-collapse:collapse;width:100%;margin:.9em 0;font-size:13px}
.docx-preview td,.docx-preview th{border:1px solid #d8e3db;padding:6px 8px;vertical-align:top}
.docx-preview img{max-width:100%;height:auto;margin:.5em 0}
.docx-preview .docx-missing{color:#8a6d3b;background:#fdf6e3;border:1px dashed #e0cf9a;padding:4px 8px;border-radius:4px;font-size:12px}
.docx-preview ul{margin:.4em 0 .4em 1.2em;padding:0}
</style>'''


def _attr(element, name, default=''):
    return element.get(W + name, default) or default


def _on(element):
    """Word 的开关属性既可以是 <w:b/>，也可以写成 <w:b w:val="0"/>。"""
    if element is None:
        return False
    value = _attr(element, 'val', 'true').lower()
    return value not in ('0', 'false', 'off')


def _paragraph_style(paragraph):
    style = paragraph.find(f'{W}pPr/{W}pStyle')
    return _attr(style, 'val').lower() if style is not None else ''


def _heading_level(style):
    if not style:
        return 0
    if style == 'title' or style == '标题':
        return 1
    digits = ''.join(character for character in style if character.isdigit())
    if style.startswith(('heading', '标题')) and digits:
        return min(int(digits[:1]), 6)
    return 0


def _is_numbered(paragraph):
    return paragraph.find(f'{W}pPr/{W}numPr') is not None


def _run_text(run):
    parts = []
    for node in run:
        tag = node.tag
        if tag == W + 't':
            parts.append(node.text or '')
        elif tag == W + 'tab':
            parts.append('\u3000')
        elif tag == W + 'br':
            parts.append('\n')
        elif tag == W + 'noBreakHyphen':
            parts.append('-')
    return ''.join(parts)


def _run_html(run, resources):
    text = _run_text(run)
    images = _images(run, resources)
    body = html.escape(text).replace('\n', '<br>')
    properties = run.find(W + 'rPr')
    if properties is not None:
        if _on(properties.find(W + 'b')):
            body = f'<strong>{body}</strong>' if body else body
        if _on(properties.find(W + 'i')):
            body = f'<em>{body}</em>' if body else body
        underline = properties.find(W + 'u')
        if underline is not None and _attr(underline, 'val', 'single') != 'none':
            body = f'<u>{body}</u>' if body else body
        strike = _on(properties.find(W + 'strike'))
        if strike:
            body = f'<s>{body}</s>' if body else body
    return body + images


def _images(run, resources):
    holders = list(run.iter(A + 'blip')) + [
        blip for blip in run.iter('{urn:schemas-microsoft-com:vml}imagedata')]
    output = []
    for holder in holders:
        identifier = holder.get(R + 'embed') or holder.get(R + 'id')
        if not identifier:
            continue
        uri = resources.get(identifier)
        if uri is None:
            output.append('<span class="docx-missing">图片不可用或超出预览上限</span>')
        else:
            output.append(f'<img src="{uri}" alt="文档图片">')
    return ''.join(output)


def _paragraph(paragraph, resources):
    parts = []
    for child in paragraph:
        if child.tag == W + 'r':
            parts.append(_run_html(child, resources))
        elif child.tag == W + 'hyperlink':
            parts.extend(_run_html(run, resources) for run in child.iter(W + 'r'))
        elif child.tag in (W + 'ins', W + 'smartTag', W + 'fldSimple'):
            parts.extend(_run_html(run, resources) for run in child.iter(W + 'r'))
    body = ''.join(parts)
    if not body.strip():
        return ''
    level = _heading_level(_paragraph_style(paragraph))
    if level:
        return f'<h{level}>{body}</h{level}>'
    if _is_numbered(paragraph):
        return f'<ul><li>{body}</li></ul>'
    return f'<p>{body}</p>'


def _cell(cell, resources):
    body = ''.join(_paragraph(paragraph, resources) for paragraph in cell.iter(W + 'p'))
    span = cell.find(f'{W}tcPr/{W}gridSpan')
    attribute = ''
    if span is not None:
        width = int(_attr(span, 'val', '1') or 1)
        if width > 1:
            attribute = f' colspan="{width}"'
    return f'<td{attribute}>{body or "&nbsp;"}</td>'


def _table(table, resources):
    rows = []
    for row in table.findall(f'{W}tr'):
        cells = ''.join(_cell(cell, resources) for cell in row.findall(f'{W}tc'))
        if cells:
            rows.append(f'<tr>{cells}</tr>')
    return f'<table>{"".join(rows)}</table>' if rows else ''


def _resources(archive, warnings):
    """图片按需加载；单张超限或缺失只记录提示，其它内容照常预览。"""
    media = {name: name for name in archive.namelist() if name.startswith('word/media/')}
    targets = {}
    try:
        relationships = ElementTree.fromstring(archive.read('word/_rels/document.xml.rels'))
    except (KeyError, ElementTree.ParseError):
        return {}
    for relationship in relationships.iter(PKG_REL + 'Relationship'):
        identifier = relationship.get('Id')
        target = relationship.get('Target') or ''
        if relationship.get('TargetMode') == 'External' or not identifier:
            continue
        if target.startswith('/'):
            path = target.lstrip('/')
        else:
            path = posixpath.normpath(posixpath.join('word', target))
        if path in media:
            targets[identifier] = path
    budget = 20_000_000
    loaded, uris = 0, {}
    for identifier, path in targets.items():
        if loaded >= MAX_IMAGES or budget <= 0:
            warnings.append('文档图片较多，仅预览前 '+str(MAX_IMAGES)+' 张')
            break
        try:
            payload = archive.read(path)
        except KeyError:
            warnings.append('文档内嵌图片缺失：'+path.rsplit('/', 1)[-1])
            continue
        if len(payload) > budget:
            warnings.append('文档内嵌图片超过预览容量上限，已跳过：'+path.rsplit('/', 1)[-1])
            continue
        budget -= len(payload)
        loaded += 1
        mime = _image_mime(payload, path)
        if mime is None:
            warnings.append('文档内嵌图片格式不受支持：'+path.rsplit('/', 1)[-1])
            continue
        uris[identifier] = 'data:'+mime+';base64,'+base64.b64encode(payload).decode()
    return uris


def _image_mime(payload, path):
    head = payload[:12]
    if head.startswith(b'\x89PNG'):
        return 'image/png'
    if head.startswith(b'\xff\xd8'):
        return 'image/jpeg'
    if head[:4] == b'RIFF' and head[8:12] == b'WEBP':
        return 'image/webp'
    if head.startswith(b'GIF8'):
        return 'image/gif'
    if head.startswith(b'BM'):
        return 'image/bmp'
    suffix = path.rsplit('.', 1)[-1].lower()
    return {'emf': 'image/emf', 'wmf': 'image/wmf', 'svg': 'image/svg+xml'}.get(suffix)


def docx_html(data: bytes) -> dict:
    """把 DOCX 渲染为带内联样式的 HTML 片段，足够核对版式与文字。"""
    warnings: list[str] = []
    try:
        archive = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile as exc:
        raise ValueError('DOCX 容器损坏，无法预览') from exc
    with archive:
        try:
            body = ElementTree.fromstring(archive.read('word/document.xml')).find(W + 'body')
        except KeyError as exc:
            raise ValueError('DOCX 缺少正文结构，无法预览') from exc
        except ElementTree.ParseError as exc:
            raise ValueError('DOCX 正文结构损坏，无法预览') from exc
        if body is None:
            raise ValueError('DOCX 正文为空，无法预览')
        resources = _resources(archive, warnings)
        parts = []
        for child in body:
            if child.tag == W + 'p':
                parts.append(_paragraph(child, resources))
            elif child.tag == W + 'tbl':
                parts.append(_table(child, resources))
            elif child.tag == W + 'sdt':
                # 内容控件内部仍是段落或表格。
                for inner in child.iter():
                    if inner.tag == W + 'p':
                        parts.append(_paragraph(inner, resources))
                    elif inner.tag == W + 'tbl':
                        parts.append(_table(inner, resources))
    content = ''.join(part for part in parts if part)
    if not content.strip():
        raise ValueError('未从 DOCX 中读取到可预览内容')
    return dict(html=STYLE+'<div class="docx-preview">'+content+'</div>', warnings=warnings[:100])