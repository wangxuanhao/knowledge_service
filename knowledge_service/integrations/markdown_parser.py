"""Token based Markdown source mapping and inert, allowlisted HTML rendering."""
import base64
import hashlib
from html import escape
from html.parser import HTMLParser
from urllib.parse import urlsplit, unquote
import bleach
from markdown_it import MarkdownIt

PARSER_VERSION = '1.3'
IMAGE_LIMIT = 10 * 1024 * 1024
HTML_TAGS = {'p', 'div', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6', 'table',
             'caption', 'thead', 'tbody', 'tfoot', 'tr', 'th', 'td', 'colgroup',
             'col', 'ul', 'ol', 'li', 'pre', 'code', 'blockquote', 'strong',
             'em', 'b', 'i', 's', 'del', 'sub', 'sup', 'a', 'hr', 'br', 'span'}
HTML_ATTRIBUTES = {'*': ['data-block-id'], 'a': ['href', 'title'],
                   'th': ['rowspan', 'colspan'], 'td': ['rowspan', 'colspan'],
                   'span': ['class', 'data-image-index']}


def inline_text(token):
    if 'normalized_text' in token.meta:
        return token.meta['normalized_text']
    if not token.children:
        return token.content
    pieces=[]
    for child in token.children:
        if child.type in {'softbreak','hardbreak'}:
            pieces.append('\n')
        elif child.type in {'text','code_inline','image'}:
            pieces.append(child.content)
    return ''.join(pieces)


def temporal_excluded_ranges(tokens, normalized_text):
    """Locate code and nested quotes in the same normalized block coordinates."""
    ranges = []
    cursor = 0
    quote_depth = 0
    for token in tokens:
        if token.type == 'blockquote_open':
            quote_depth += 1
        elif token.type == 'blockquote_close':
            quote_depth -= 1
        if 'normalized_text' in token.meta:
            piece = token.meta['normalized_text']
            start = normalized_text.find(piece, cursor)
            if start >= 0:
                cursor = start + len(piece)
                if quote_depth and piece:
                    ranges.append([start, cursor])
                else:
                    ranges.extend([start + begin, start + end]
                                  for begin, end in token.meta['temporal_excluded_ranges'])
            continue
        children = token.children or []
        if token.type in {'fence', 'code_block'}:
            children = [token]
        for child in children:
            if child.type in {'softbreak', 'hardbreak'}:
                piece = '\n'
            elif child.type in {'text', 'code_inline', 'image', 'fence', 'code_block'}:
                piece = child.content
            else:
                continue
            start = normalized_text.find(piece, cursor)
            if start < 0:
                continue
            cursor = start + len(piece)
            if piece and (quote_depth or child.type in {'code_inline', 'fence', 'code_block'}):
                ranges.append([start, cursor])
    return ranges


def image_reference(url, alt='', max_data_bytes=IMAGE_LIMIT, media_type='image'):
    decoded = unquote(url).strip()
    result = dict(url=url, alt=alt)
    if media_type == 'video':
        result['provenance'] = {'media_type': 'video'}
    scheme = urlsplit(decoded).scheme.lower()
    if scheme == 'data':
        if media_type == 'video':
            return dict(result,kind='unsafe',status='blocked')
        if len(decoded) > max_data_bytes * 4 // 3 + 256:
            raise ValueError('Base64 image exceeds size limit')
        header, encoded = decoded.split(',', 1)
        mime = header[5:].split(';')[0]
        if mime not in {'image/png', 'image/jpeg', 'image/webp', 'image/gif', 'image/bmp', 'image/tiff'} or ';base64' not in header:
            raise ValueError('Unsupported embedded image')
        data = base64.b64decode(encoded, validate=True)
        if len(data) > max_data_bytes:
            raise ValueError('Base64 image exceeds size limit')
        result.update(kind='base64', data=data, mime_type=mime, status='available')
    elif scheme in {'http', 'https'}:
        result.update(kind='external', status='external_unfrozen')
    elif scheme or decoded.startswith(('//', '/', '\\')):
        result.update(kind='unsafe', status='blocked')
    else:
        result.update(kind='local', path=decoded.split('#', 1)[0].split('?', 1)[0], status='pending')
    return result


def image_placeholder(index, alt):
    return f'<span class="source-image" data-image-index="{index}">{escape(alt)}</span>'


class SourceHTMLParser(HTMLParser):
    """Keep inert article markup and normalized text in the same reading order."""
    blocked_tags = {'script', 'style', 'iframe', 'object', 'svg', 'math', 'template'}
    line_tags = {'p', 'div', 'table', 'tr', 'caption', 'li', 'blockquote', 'pre',
                 'h1', 'h2', 'h3', 'h4', 'h5', 'h6', 'br', 'hr'}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.context = []
        self.blocked = []
        self.register_image = None
        self.reset_capture()

    @property
    def in_code(self):
        return 'code' in self.context or 'pre' in self.context

    def reset_capture(self):
        self.output = []
        self.text = ''
        self.excluded_ranges = []

    def append_text(self, value, excluded=False):
        if not self.blocked:
            start = len(self.text)
            self.text += value
            if value and (excluded or self.in_code or 'blockquote' in self.context):
                self.excluded_ranges.append([start, len(self.text)])

    def line_break(self):
        if self.text and not self.text.endswith('\n'):
            self.append_text('\n')

    def feed_fragment(self, value):
        start = len(self.output)
        self.feed(value)
        return ''.join(self.output[start:])

    def handle_starttag(self, tag, attrs):
        if self.blocked:
            if tag in self.blocked_tags:
                self.blocked.append(tag)
            return
        if self.in_code and tag not in {'code', 'pre'}:
            literal = self.get_starttag_text()
            self.output.append(escape(literal))
            self.append_text(literal)
            return
        if tag in self.blocked_tags:
            self.blocked.append(tag)
            return
        attrs = dict(attrs)
        if tag in {'img', 'video'}:
            alt = attrs.get('alt') or attrs.get('title') or ''
            index = self.register_image(attrs.get('src') or '', alt, 'video' if tag == 'video' else 'image')
            self.output.append(image_placeholder(index, alt))
            self.append_text(alt)
            return
        if tag not in HTML_TAGS:
            return
        if tag in self.line_tags:
            self.line_break()
        elif tag in {'td', 'th'} and self.text and not self.text.endswith('\n'):
            self.append_text(' | ')
        allowed = {}
        if tag == 'a':
            allowed = {name: value for name, value in attrs.items()
                       if name in {'href', 'title'} and value is not None}
        elif tag in {'td', 'th'}:
            allowed = {name: str(int(value)) for name, value in attrs.items()
                       if name in {'rowspan', 'colspan'} and value is not None
                       and value.isascii() and value.isdigit() and len(value) <= 4
                       and 1 <= int(value) <= 1000}
        rendered_attrs = ''.join(f' {name}="{escape(value, quote=True)}"'
                                 for name, value in allowed.items())
        self.output.append(f'<{tag}{rendered_attrs}>')
        if tag in {'code', 'pre', 'blockquote'}:
            self.context.append(tag)

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        if tag not in {'img', 'br', 'hr', 'col'}:
            self.handle_endtag(tag)

    def handle_endtag(self, tag):
        if self.blocked:
            if tag in self.blocked:
                index = len(self.blocked) - 1 - self.blocked[::-1].index(tag)
                del self.blocked[index:]
            return
        if self.in_code and tag not in {'code', 'pre'}:
            literal = f'</{tag}>'
            self.output.append(escape(literal))
            self.append_text(literal)
            return
        if tag in HTML_TAGS:
            if tag in self.line_tags:
                self.line_break()
            self.output.append(f'</{tag}>')
        if tag in self.context:
            index = len(self.context) - 1 - self.context[::-1].index(tag)
            del self.context[index:]

    def handle_data(self, data):
        if not self.blocked:
            self.output.append(escape(data))
            self.append_text(data)


def prepare_source_tokens(tokens, html_parser, register_image):
    """Resolve HTML and Markdown images together before rendering either syntax."""
    html_parser.register_image = register_image
    for token in tokens:
        if token.type not in {'inline', 'html_block'}:
            continue
        html_parser.reset_capture()
        if token.type == 'html_block':
            token.content = html_parser.feed_fragment(token.content)
        else:
            children = token.children or []
            for index, child in enumerate(children):
                if child.type == 'link_open' and urlsplit(unquote(child.attrGet('href') or '')).path.lower().endswith(('.mp4', '.webm')) and not html_parser.in_code and not html_parser.blocked:
                    stop = next((end for end in range(index+1,len(children)) if children[end].type == 'link_close'),None)
                    if stop is not None:
                        label = ''.join(item.content for item in children[index+1:stop])
                        child.type = 'image';child.nesting = 0;child.content = label
                        child.attrSet('src',child.attrGet('href'));child.meta['media_type'] = 'video'
                        for item in children[index+1:stop+1]:item.type='text';item.content='';item.nesting=0
                if child.type == 'html_inline':
                    child.content = html_parser.feed_fragment(child.content)
                elif child.type == 'image':
                    if html_parser.in_code or html_parser.blocked:
                        child.type = 'text'
                        child.content = f'![{child.content}]({child.attrGet("src") or ""})'
                    else:
                        child.meta['source_image_index'] = register_image(child.attrGet('src') or '', child.content, child.meta.get('media_type','image'))
                    html_parser.append_text(child.content)
                elif child.type in {'text', 'code_inline'}:
                    html_parser.append_text(child.content, excluded=child.type == 'code_inline')
                elif child.type in {'softbreak', 'hardbreak'}:
                    html_parser.append_text('\n')
        token.meta['normalized_text'] = html_parser.text
        token.meta['temporal_excluded_ranges'] = html_parser.excluded_ranges


class MarkdownParser:
    def __init__(self):
        self.md = MarkdownIt('commonmark', {'html': True}).enable('table')

    def parse(self, text, document_version_id=''):
        tokens = self.md.parse(text)
        lines = text.splitlines(keepends=True)
        offsets = [0]
        for line in lines:
            offsets.append(offsets[-1] + len(line))
        blocks, images, headings = [], [], []
        html_parser = SourceHTMLParser()
        index = 0
        while index < len(tokens):
            token = tokens[index]
            if token.map is None or token.type in {'inline', 'list_item_open'}:
                index += 1
                continue
            start, end = token.map
            last = index
            if token.nesting == 1:
                depth = 1
                while last + 1 < len(tokens) and depth:
                    last += 1
                    depth += tokens[last].nesting
            segment = tokens[index:last + 1]
            kind = {'heading_open': 'heading', 'paragraph_open': 'paragraph', 'table_open': 'table', 'bullet_list_open': 'list', 'ordered_list_open': 'list', 'fence': 'code', 'code_block': 'code', 'blockquote_open': 'blockquote'}.get(token.type, token.type)
            payload = {}
            block_id = 'block-' + hashlib.sha256(f'{document_version_id}:{start}:{end}:{kind}'.encode()).hexdigest()[:24]
            def register_image(url, alt, media_type='image'):
                ref = image_reference(url, alt, media_type=media_type)
                ref.update(block_id=block_id, source_start_line=start + 1, source_end_line=end)
                ordinal = len(images)
                images.append(ref)
                payload.setdefault('images', []).append({key: value for key, value in ref.items() if key != 'data'})
                return ordinal
            prepare_source_tokens(segment, html_parser, register_image)
            content = '\n'.join(inline_text(t) if t.type in {'inline', 'html_block'} else t.content for t in segment if t.type in {'inline', 'html_block', 'fence', 'code_block'})
            if kind == 'heading':
                level = int(token.tag[1:])
                headings = headings[:level - 1] + [content]
                payload['level'] = level
            if kind == 'code':
                payload['language'] = token.info.strip()
            if kind == 'table':
                headers, rows, row = [], [], []
                in_header = False
                for t in segment:
                    if t.type == 'thead_open': in_header = True
                    elif t.type == 'thead_close': in_header = False
                    elif t.type == 'tr_open': row = []
                    elif t.type == 'inline': row.append(inline_text(t))
                    elif t.type == 'tr_close':
                        if in_header: headers = row
                        else: rows.append(row)
                payload.update(headers=headers, rows=rows, row_source_lines=list(range(start + 3, start + 3 + len(rows))))
                content = '\n'.join(' | '.join(r) for r in [headers, *rows])
            payload['temporal_excluded_ranges'] = temporal_excluded_ranges(segment, content)
            if token.nesting == 1 or kind in {'code', 'html_block'}:
                token.attrSet('data-block-id', block_id)
            blocks.append(dict(block_id=block_id, document_version_id=document_version_id, type=kind,
                ordinal=len(blocks), normalized_text=content, source_start_char=offsets[start],
                source_end_char=offsets[min(end, len(lines))], source_start_line=start + 1,
                source_end_line=end, heading_path=list(headings), payload=payload))
            index = last + 1
        # Never emit remote or user-path images; authorized asset URLs are added by the reader.
        def render_image(ts, idx, opts, env):
            return image_placeholder(ts[idx].meta['source_image_index'], ts[idx].content)
        self.md.renderer.rules['image'] = render_image
        def render_html_block(ts, idx, opts, env):
            anchor = ts[idx].attrGet('data-block-id')
            return '<div data-block-id="' + anchor + '">' + ts[idx].content + '</div>\n' if anchor else ts[idx].content
        self.md.renderer.rules['html_block'] = render_html_block
        for code_type in ('fence','code_block'):
            original_renderer=self.md.renderer.rules[code_type]
            def render_code(ts,idx,opts,env,rule=original_renderer):
                rendered=rule(ts,idx,opts,env)
                anchor=ts[idx].attrGet('data-block-id')
                return rendered.replace('<pre>', '<pre data-block-id="'+anchor+'">',1) if anchor else rendered
            self.md.renderer.rules[code_type]=render_code
        html = bleach.clean(self.md.renderer.render(tokens, self.md.options, {}), tags=HTML_TAGS, attributes=HTML_ATTRIBUTES, protocols=['http','https','mailto'], strip=True)
        return dict(parser_name='markdown-it-py', parser_version=PARSER_VERSION, blocks=blocks, images=images, rendered_html=html, text=text)


def parse_markdown(text, document_version_id=''):
    return MarkdownParser().parse(text, document_version_id)


def chunk_markdown_blocks(parsed, max_chars=2000):
    """Keep blocks intact; oversized tables repeat headers with original row spans."""
    if max_chars < 1:
        raise ValueError('max_chars must be positive')
    chunks = []
    for block in parsed['blocks']:
        text = block['normalized_text']
        pieces = []
        if block['type'] == 'table':
            payload = block['payload']
            header = ' | '.join(payload['headers'])
            rows = payload['rows']
            if len(text) <= max_chars:
                pieces = [(text, 0, len(text), {'table_start_row': 0, 'table_end_row': len(rows)})]
            else:
                group=[]; start=0; used=len(header); source_offset=len(header)+1
                for ordinal,row in enumerate(rows):
                    row_text=' | '.join(row)
                    if group and used+len(row_text)+1>max_chars:
                        part=header+'\n'+'\n'.join(group)
                        pieces.append((part,source_offset,source_offset+sum(len(r)+1 for r in group)-1,{'table_start_row':start,'table_end_row':ordinal,'table_source_lines':payload['row_source_lines'][start:ordinal],'repeated_header':True}))
                        source_offset+=sum(len(r)+1 for r in group);group=[];start=ordinal;used=len(header)
                    group.append(row_text);used+=len(row_text)+1
                if group:
                    pieces.append((header+'\n'+'\n'.join(group),source_offset,source_offset+sum(len(r)+1 for r in group)-1,{'table_start_row':start,'table_end_row':len(rows),'table_source_lines':payload['row_source_lines'][start:],'repeated_header':True}))
        else:
            pieces=[(text[start:start+max_chars],start,min(start+max_chars,len(text)),{}) for start in range(0,len(text),max_chars)]
        for part,start,end,metadata in pieces:
            ordinal=len(chunks)
            chunks.append(dict(id='md-'+str(ordinal),text=part,ordinal=ordinal,source_spans=[dict(block_id=block['block_id'],chunk_start_char=0,chunk_end_char=len(part),block_start_char=start,block_end_char=end,payload=metadata)],metadata=dict(source_kind='markdown',source_document_version_id=block['document_version_id'],heading_path=block['heading_path'],**metadata)))
    return chunks
