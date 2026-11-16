"""Observable lexical locations are distinct from semantic attribution."""


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
