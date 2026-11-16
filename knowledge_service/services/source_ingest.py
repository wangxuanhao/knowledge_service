"""Build searchable streams while keeping frozen reading media independent of OCR."""
from contextlib import nullcontext
from ..integrations.image_ocr import OCRError
from ..integrations.video_asr import ASRError
from .chunking import split_source_blocks,split_mapped_chunks


def _table_fragment_payload(block,start,end):
    rows=[];offset=len(' | '.join(block['payload']['headers']))+1
    for ordinal,row in enumerate(block['payload']['rows']):
        stop=offset+len(' | '.join(row))
        if offset<end and stop>start:rows.append(ordinal)
        offset=stop+1
    first=rows[0] if rows else 0;last=rows[-1]+1 if rows else 0
    return dict(table_start_row=first,table_end_row=last,
        table_source_lines=block['payload'].get('row_source_lines',[])[first:last])


def _block_fragment(block,start,end,*,heading=False,inline=False):
    """Map an unsplit fragment; media and prose share the final text windows."""
    body=block['normalized_text'][start:end]
    if not body.strip():return []
    prefix='#'*block['payload'].get('level',1)+' ' if heading else ''
    payload={key:block['payload'][key] for key in ('source_locations','page_no') if key in block['payload']}
    if inline:payload['inline_media_fragment']=True
    if block['type']=='table':payload.update(_table_fragment_payload(block,start,end))
    return [dict(text=prefix+body,
        source_spans=[dict(block_id=block['block_id'],chunk_start_char=len(prefix),
            chunk_end_char=len(prefix)+len(body),block_start_char=start,block_end_char=end,payload=payload)],
        metadata=dict(source_kind='markdown',source_document_version_id=block['document_version_id'],
            heading_path=block['heading_path']))]


def prepare_source_ingest(sources,project_id,source,options,*,ocr_semaphore=None,progress=None,cached_only=False,strict_media=False):
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
    def media(asset,heading_path=None):
        if asset['kind'] not in {'local','base64'} or asset['status']=='ignored':return
        if progress:progress()
        is_video=asset.get('provenance',{}).get('media_type')=='video'
        if not is_video and options.get('parse_config',{}).get('ocr_mode')=='disabled':
            warnings.append(dict(code='ocr_disabled',asset_id=asset['id'],message='已按解析设置禁用图片文字识别'))
            return
        processor=sources.video_asr if is_video else sources.image_ocr
        try:
            if cached_only:
                receipt=sources.asr(project_id,source['id'],asset['id']) if is_video else sources.ocr(project_id,source['id'],asset['id'])
                run=receipt.get('run') or {'status':'pending'}
            else:
                with ocr_semaphore if ocr_semaphore is not None and not is_video else nullcontext():
                    run=processor.process_asset(project_id,source['id'],asset['id'])
        except (OCRError,ASRError) as exc:
            if (options.get('extraction_mode')!='documents' or
                    strict_media and exc.code not in {'asr_no_audio','asr_no_speech'}):raise
            warnings.append(dict(code=exc.code,asset_id=asset['id'],message=str(exc)))
            run=getattr(exc,'partial_receipt',None)
            if not run or run.get('status')!='partial':return
        if run.get('status') not in {'completed','partial'}:return
        length=sum(len(block['normalized_text'])+1 for block in run.get('segments' if is_video else 'blocks',[]))
        if not length:return
        ocr_chunks=processor.chunks(run,max(1,length))
        if heading_path:
            for chunk in ocr_chunks:chunk['metadata']['heading_path']=heading_path
        if run.get('status')=='partial':
            for chunk in ocr_chunks:chunk['metadata']['source_incomplete']=True
        pending.extend(ocr_chunks)
    pending=[];used=set()
    def flush():
        if pending:
            append(*split_mapped_chunks(pending,options,return_text=True))
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
            if block['type']=='table':
                flush()
                append(*split_source_blocks([block],options,return_text=True))
            else:pending.extend(_block_fragment(block,0,len(block['normalized_text']),heading=block['type']=='heading'))
            continue
        positioned=all(type(ref.get('block_text_offset')) is int and
                       0<=ref['block_text_offset']<=len(block['normalized_text']) for ref in references)
        if not positioned:pending.extend(_block_fragment(block,0,len(block['normalized_text']),heading=block['type']=='heading',inline=True))
        cursor=0;heading=block['type']=='heading'
        ordered=sorted(references,key=lambda ref:ref['block_text_offset']) if positioned else references
        for ref in ordered:
            if positioned:
                position=ref['block_text_offset']
                rows=_block_fragment(block,cursor,position,heading=heading,inline=True)
                pending.extend(rows)
                if rows:heading=False
                cursor=position
            asset=match_reference(block,ref)
            if asset is not None:
                used.add(asset['id']);media(asset,block['heading_path'])
        if positioned:
            pending.extend(_block_fragment(block,cursor,len(block['normalized_text']),heading=heading,inline=True))
    for asset in source['assets']:
        if asset['id'] not in used:
            used.add(asset['id']);media(asset)
    flush()
    tables={b['block_id']:b for b in source['blocks'] if b['type']=='table'}
    for chunk in chunks:
        for span in chunk['source_spans']:
            block=tables.get(span.get('block_id'))
            if block and span['payload'].get('inline_media_fragment'):
                span['payload'].update(_table_fragment_payload(block,span['block_start_char'],span['block_end_char']))
    if not chunks:
        if options.get('extraction_mode')!='documents' or not source['assets']:
            raise ValueError('document contains no text')
        # A media-only receipt remains readable; zero chunks means zero invented
        # searchable body text. The document can still show its actual Markdown.
        text=source['source_text']
        warnings.append(dict(code='no_searchable_text',message='媒体正文尚无可检索文字'))
    return chunks,text,warnings[:100]
