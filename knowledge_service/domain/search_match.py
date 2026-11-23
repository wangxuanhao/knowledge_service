"""Observable lexical locations are distinct from semantic attribution."""
from math import isfinite

from .search_projection import document_heading


def explain_match(document, query, *, text='', mode='keyword', channels=(), strategy=None, extra_fields=()):
    query=str(query or '').strip()
    metadata=document.get('metadata') or {}
    aliases=metadata.get('aliases')
    if not isinstance(aliases,list):aliases=[]
    fields=[]
    values=[('title',metadata.get('title','')),('filename',metadata.get('source_file','')),
        ('body',text),('identifier',document.get('id','')),
        ('aliases',' '.join(v for v in aliases if isinstance(v,str)))]
    for field,value in values:
        if query and query.lower() in str(value).lower():fields.append(field)
    fields.extend(field for field in extra_fields if field not in fields)
    position=text.lower().find(query.lower()) if query else -1
    start=max(0,position-60) if position>=0 else 0
    snippet=text[start:start+260]
    if mode=='keyword' and 'body' not in fields:snippet=''
    return {'mode':mode,'query':query,'fields':fields,'channels':list(channels),
        'strategy':strategy,'text':(('…' if start else '')+snippet+('…' if start+260<len(text) else '')) if snippet else ''}


def record_retrieval_match(row, query, *, mode, strategy, backend, document=None):
    """Describe the rank and literal locations in the indexed inputs we can verify."""
    kind = row.get('kind')
    text = row.get('text')
    candidates = {
        'attribute': [('attribute_value', row.get('matched_value', row.get('value'))),
                      ('owner_name', row.get('owner_label')), ('property_name', row.get('property_label'))],
        'chunk': [('body', text)], 'entity': [('entity_name', text)],
        'relation': [('relation_text', text)],
    }.get(kind, [('record_text', text)])
    if kind == 'chunk' and backend != 'postgres_history' and document:
        title, filename = (document or {}).get('title'), (document or {}).get('source_file')
        title = title if isinstance(title, str) else ''
        filename = filename if isinstance(filename, str) else ''
        heading = document_heading(title, filename)
        visible_title = heading[:len(title)]
        visible_file = heading[len(title) + 1:].removesuffix('）') if title and filename and title != filename else heading
        candidates += [(key, value) for key, value in (
            ('title', visible_title), ('filename', visible_file if filename else '')) if value]
    if backend == 'postgres_history':
        aliases = (row.get('metadata') or {}).get('aliases')
        if isinstance(aliases, list):
            candidates += [('aliases', alias) for alias in aliases if isinstance(alias, str)]
    term = str(query or '').strip().casefold()
    return {
        'mode': mode, 'strategy': strategy,
        'channels': [item for item in (row.get('match_channels') or []) if item in ('keyword', 'semantic')],
        'fields': list(dict.fromkeys(field for field, value in candidates
                                    if term and value is not None and term in str(value).casefold())),
        'indexed_fields': list(dict.fromkeys(field for field, _ in candidates)),
        'supplemental': row.get('retrieval_channel') in ('ontology_type', 'ontology_entity_type'),
        **{key: row[key] for key in ('score', 'rrf_score')
           if type(row.get(key)) in (int, float) and isfinite(row[key])},
    }


_MATCH_FIELDS = ('title', 'filename', 'body', 'identifier', 'aliases', 'entity_name', 'relation_text',
                 'attribute_value', 'owner_name', 'property_name', 'record_text')


def sanitize_retrieval_match(value):
    """Copy only public, typed per-hit metadata into frozen QA records."""
    from sys import float_info

    if not isinstance(value, dict) or value.get('mode') not in ('keyword', 'semantic', 'hybrid'):
        return {}
    result = {'mode': value['mode']}
    if 'strategy' in value and value['strategy'] in (None, 'fusion', 'keyword_rerank'):
        result['strategy'] = value['strategy']
    for key, allowed in (('channels', ('keyword', 'semantic')), ('fields', _MATCH_FIELDS),
                         ('indexed_fields', _MATCH_FIELDS)):
        if isinstance(items := value.get(key), list):
            result[key] = list(dict.fromkeys(item for item in items if item in allowed))
    for key in ('score', 'rrf_score'):
        score = value.get(key)
        if type(score) in (int, float) and -float_info.max <= score <= float_info.max:
            result[key] = score
    if type(value.get('supplemental')) is bool:
        result['supplemental'] = value['supplemental']
    return result
