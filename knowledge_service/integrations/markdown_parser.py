"""Token based Markdown source mapping and inert, allowlisted HTML rendering."""
import base64
import hashlib
from urllib.parse import urlsplit, unquote
import bleach
from markdown_it import MarkdownIt

PARSER_VERSION = '1.0'
IMAGE_LIMIT = 10 * 1024 * 1024


def inline_text(token):
    if not token.children:
        return token.content
    pieces=[]
    for child in token.children:
        if child.type in {'softbreak','hardbreak'}:
            pieces.append('\n')
        elif child.type in {'text','code_inline','image'}:
            pieces.append(child.content)
    return ''.join(pieces)


def image_reference(url, alt='', max_data_bytes=IMAGE_LIMIT):
    decoded = unquote(url).strip()
    result = dict(url=url, alt=alt)
    scheme = urlsplit(decoded).scheme.lower()
    if scheme == 'data':
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


class MarkdownParser:
    def __init__(self):
        self.md = MarkdownIt('commonmark', {'html': False}).enable('table')

    def parse(self, text, document_version_id=''):
        tokens = self.md.parse(text)
        lines = text.splitlines(keepends=True)
        offsets = [0]
        for line in lines:
            offsets.append(offsets[-1] + len(line))
        blocks, images, headings = [], [], []
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
            content = '\n'.join(inline_text(t) if t.type=='inline' else t.content for t in segment if t.type in {'inline', 'fence', 'code_block'})
            kind = {'heading_open': 'heading', 'paragraph_open': 'paragraph', 'table_open': 'table', 'bullet_list_open': 'list', 'ordered_list_open': 'list', 'fence': 'code', 'code_block': 'code', 'blockquote_open': 'blockquote'}.get(token.type, token.type)
            payload = {}
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
            block_id = 'block-' + hashlib.sha256(f'{document_version_id}:{start}:{end}:{kind}'.encode()).hexdigest()[:24]
            if token.nesting == 1 or kind == 'code':
                token.attrSet('data-block-id', block_id)
            for t in segment:
                for child in t.children or []:
                    if child.type == 'image':
                        ref = image_reference(child.attrGet('src') or '', child.content)
                        ref.update(block_id=block_id, source_start_line=start + 1, source_end_line=end)
                        images.append(ref)
                        payload.setdefault('images', []).append({k: v for k, v in ref.items() if k != 'data'})
            blocks.append(dict(block_id=block_id, document_version_id=document_version_id, type=kind,
                ordinal=len(blocks), normalized_text=content, source_start_char=offsets[start],
                source_end_char=offsets[min(end, len(lines))], source_start_line=start + 1,
                source_end_line=end, heading_path=list(headings), payload=payload))
            index = last + 1
        # Never emit remote or user-path images; authorized asset URLs are added by the reader.
        image_index = [0]
        def render_image(ts, idx, opts, env):
            ordinal=image_index[0]; image_index[0]+=1
            return '<span class="source-image" data-image-index="'+str(ordinal)+'">' + bleach.clean(ts[idx].content, tags=[], strip=True) + '</span>'
        self.md.renderer.rules['image'] = render_image
        for code_type in ('fence','code_block'):
            original_renderer=self.md.renderer.rules[code_type]
            def render_code(ts,idx,opts,env,rule=original_renderer):
                rendered=rule(ts,idx,opts,env)
                anchor=ts[idx].attrGet('data-block-id')
                return rendered.replace('<pre>', '<pre data-block-id="'+anchor+'">',1) if anchor else rendered
            self.md.renderer.rules[code_type]=render_code
        html = bleach.clean(self.md.renderer.render(tokens, self.md.options, {}), tags={'p','h1','h2','h3','h4','h5','h6','table','thead','tbody','tr','th','td','ul','ol','li','pre','code','blockquote','strong','em','a','hr','br','span'}, attributes={'*':['data-block-id'], 'a':['href','title'], 'span':['class','data-image-index']}, protocols=['http','https','mailto'], strip=True)
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
