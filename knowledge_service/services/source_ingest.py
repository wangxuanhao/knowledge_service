"""Build searchable streams while keeping frozen reading media independent of OCR."""
from contextlib import nullcontext
from ..integrations.image_ocr import OCRError
from .chunking import split_source_blocks,split_mapped_chunks


def prepare_source_ingest(sources,project_id,source,options,*,ocr_semaphore=None,progress=None):
    chunks,text=split_source_blocks(source['blocks'],options,return_text=True)
    warnings=list(source.get('parser_metadata',{}).get('warnings',[]))
    for asset in source['assets']:
        if asset['kind'] not in {'local','base64'} or asset['status']=='ignored':continue
        if progress:progress()
        try:
            with ocr_semaphore if ocr_semaphore is not None else nullcontext():
                run=sources.image_ocr.process_asset(project_id,source['id'],asset['id'])
        except OCRError as exc:
            if options.get('extraction_mode')!='documents':raise
            warnings.append(dict(code=exc.code,asset_id=asset['id'],message=str(exc)))
            continue
        # A failed/ignored receipt can never contribute text to the search stream.
        if run.get('status')!='completed':continue
        length=sum(len(block['normalized_text'])+1 for block in run.get('blocks',[]))
        ocr_chunks,ocr_text=split_mapped_chunks(sources.image_ocr.chunks(run,max(1,length)),options,return_text=True)
        offset=len(text)+2 if text else 0
        for chunk in ocr_chunks:
            chunk['start_char']+=offset;chunk['end_char']+=offset;chunks.append(chunk)
        if ocr_text:text=text+'\n\n'+ocr_text if text else ocr_text
    if not chunks:
        if options.get('extraction_mode')!='documents' or not source['assets']:
            raise ValueError('document contains no text')
        # A media-only receipt remains readable; zero chunks means zero invented
        # searchable body text. The document can still show its actual Markdown.
        text=source['source_text']
        warnings.append(dict(code='no_searchable_text',message='图片正文尚无可检索文字'))
    return chunks,text,warnings[:100]
