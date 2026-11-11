"""Build searchable streams while keeping frozen reading media independent of OCR."""
from contextlib import nullcontext
from ..integrations.image_ocr import OCRError
from ..integrations.video_asr import ASRError
from .chunking import split_source_blocks,split_mapped_chunks


def _split_block_fragment(block,start,end,options,*,heading=False):
    """Split a media-adjacent slice without replacing its immutable source block."""
    body=block['normalized_text'][start:end]
    if not body.strip():return [],''
    prefix='#'*block['payload'].get('level',1)+' ' if heading else ''
    payload={key:block['payload'][key] for key in ('source_locations','page_no') if key in block['payload']}
    payload['inline_media_fragment']=True
    metadata=dict(source_kind='markdown',source_document_version_id=block['document_version_id'],
                  heading_path=block['heading_path'])
    table_rows=[]
    if block['type']=='table':
        # Media can occur inside a cell; keep only rows that this source slice
        # actually intersects instead of inventing a table with a new identity.
        row_start=len(' | '.join(block['payload']['headers']))+1
        for ordinal,row in enumerate(block['payload']['rows']):
            row_end=row_start+len(' | '.join(row))
            table_rows.append((ordinal,row_start,row_end))
            row_start=row_end+1
    mapped=dict(text=prefix+body,
        source_spans=[dict(block_id=block['block_id'],chunk_start_char=len(prefix),
            chunk_end_char=len(prefix)+len(body),block_start_char=start,block_end_char=end,payload=payload)],
        metadata=metadata)
    chunks,text=split_mapped_chunks([mapped],options,return_text=True)
    if block['type']=='table':
        for chunk in chunks:
            for span in chunk['source_spans']:
                intersecting=[ordinal for ordinal,first,last in table_rows
                              if first<span['block_end_char'] and last>span['block_start_char']]
                first=intersecting[0] if intersecting else 0
                last=intersecting[-1]+1 if intersecting else 0
                table=dict(table_start_row=first,table_end_row=last,
                           table_source_lines=block['payload'].get('row_source_lines',[])[first:last])
                span['payload']={**span['payload'],**table}
                chunk['metadata'].update(table)
    return chunks,text


def prepare_source_ingest(sources,project_id,source,options,*,ocr_semaphore=None,progress=None):
    chunks,text=[],''
    warnings=list(source.get('parser_metadata',{}).get('warnings',[]))
    def append(rows,body):
        nonlocal text
        if not body:return
        offset=len(text)+2 if text else 0
        for chunk in rows:
            chunk['start_char']+=offset;chunk['end_char']+=offset
            chunk['id']='source-'+str(len(chunks));chunk['ordinal']=len(chunks)
            chunks.append(chunk)
        text=text+'\n\n'+body if text else body
    def media(asset):
        if asset['kind'] not in {'local','base64'} or asset['status']=='ignored':return
        if progress:progress()
        is_video=asset.get('provenance',{}).get('media_type')=='video'
        processor=sources.video_asr if is_video else sources.image_ocr
        try:
            with ocr_semaphore if ocr_semaphore is not None and not is_video else nullcontext():
                run=processor.process_asset(project_id,source['id'],asset['id'])
        except (OCRError,ASRError) as exc:
            if options.get('extraction_mode')!='documents':raise
            warnings.append(dict(code=exc.code,asset_id=asset['id'],message=str(exc)))
            run=getattr(exc,'partial_receipt',None)
            if not run or run.get('status')!='partial':return
        if run.get('status') not in {'completed','partial'}:return
        length=sum(len(block['normalized_text'])+1 for block in run.get('segments' if is_video else 'blocks',[]))
        if not length:return
        ocr_chunks,ocr_text=split_mapped_chunks(processor.chunks(run,max(1,length)),options,return_text=True)
        if run.get('status')=='partial':
            for chunk in ocr_chunks:chunk['metadata']['source_incomplete']=True
        append(ocr_chunks,ocr_text)
    pending=[];used=set()
    def flush():
        if pending:
            append(*split_source_blocks(pending,options,return_text=True))
            pending.clear()
    def match_reference(block,ref):
        available=[asset for asset in source['assets'] if asset['id'] not in used]
        ordinal=ref.get('source_image_ordinal')
        if ordinal is not None:
            exact=next((asset for asset in available if
                        asset.get('provenance',{}).get('source_image_ordinal')==ordinal),None)
            if exact is not None:return exact
        keys={ref.get('path'),ref.get('url')} - {None}
        matching=[asset for asset in available if
                  {asset.get('original_path'),asset.get('resolved_path')} & keys]
        # Older frozen sources have no occurrence ordinal. Match their path and
        # source block before falling back to their recorded line range.
        in_block=lambda asset:asset.get('source_start_line') is not None and (
            block.get('source_start_line',0)<=asset['source_start_line']<=block.get('source_end_line',0))
        return next((asset for asset in matching if in_block(asset)),None) or (
            matching[0] if matching else next((asset for asset in available if in_block(asset)),None))
    for block in source['blocks']:
        references=block.get('payload',{}).get('images',[])
        if not references:
            pending.append(block)
            continue
        positioned=all(type(ref.get('block_text_offset')) is int and
                       0<=ref['block_text_offset']<=len(block['normalized_text']) for ref in references)
        if not positioned:pending.append(block)
        flush()
        cursor=0;heading=block['type']=='heading'
        ordered=sorted(references,key=lambda ref:ref['block_text_offset']) if positioned else references
        for ref in ordered:
            if positioned:
                position=ref['block_text_offset']
                rows,body=_split_block_fragment(block,cursor,position,options,heading=heading)
                append(rows,body)
                if body:heading=False
                cursor=position
            asset=match_reference(block,ref)
            if asset is not None:
                used.add(asset['id']);media(asset)
        if positioned:
            append(*_split_block_fragment(block,cursor,len(block['normalized_text']),options,heading=heading))
    flush()
    for asset in source['assets']:
        if asset['id'] not in used:
            used.add(asset['id']);media(asset)
    if not chunks:
        if options.get('extraction_mode')!='documents' or not source['assets']:
            raise ValueError('document contains no text')
        # A media-only receipt remains readable; zero chunks means zero invented
        # searchable body text. The document can still show its actual Markdown.
        text=source['source_text']
        warnings.append(dict(code='no_searchable_text',message='媒体正文尚无可检索文字'))
    return chunks,text,warnings[:100]
