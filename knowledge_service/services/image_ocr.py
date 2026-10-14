"""Persist successful image text and explicit failure/ignore audit receipts."""
import hashlib
import threading
from uuid import uuid4
from ..core.time import utc_now
from ..integrations.image_ocr import RapidOCRAdapter, OCRError

_PROCESS_LOCK = threading.RLock()


class ImageOCRService:
    def __init__(self, store, object_store, adapter=None, min_confidence=0.5, min_average_confidence=0.6):
        if not (0 <= min_confidence <= 1 and 0 <= min_average_confidence <= 1):
            raise ValueError('OCR confidence thresholds must be within 0..1')
        self.store,self.object_store,self.adapter=store,object_store,adapter or RapidOCRAdapter()
        self.min_confidence,self.min_average_confidence=min_confidence,min_average_confidence

    def process_asset(self, project_id, version_id, asset_id, retry=False):
        # Serialize the entire check/recognize/receipt cycle, not only engine inference.
        with _PROCESS_LOCK:
            return self._process_asset(project_id,version_id,asset_id,retry)

    def _process_asset(self, project_id, version_id, asset_id, retry=False):
        asset=self.store.get_asset(project_id,version_id,asset_id)
        if asset['status']=='ignored': return dict(status='ignored',blocks=[])
        if asset['kind'] in {'external','unsafe'}: return dict(status=asset['status'],blocks=[])
        if not asset.get('storage_key'): raise OCRError('source_asset_missing','Local image is missing')
        # Include quality policy in reuse identity, so changed gates cannot reuse unchecked output.
        configuration_error=None
        try:
            configuration=self.adapter.configuration_fingerprint
        except OCRError as exc:
            configuration='unavailable'
            configuration_error=exc
        fingerprint=hashlib.sha256(f'{configuration}:{self.min_confidence}:{self.min_average_confidence}'.encode()).hexdigest()
        previous=self.store.reusable_ocr(project_id,asset['sha256'],fingerprint)
        if previous and not retry:
            if previous['asset_id']==asset_id and previous['document_version_id']==version_id:
                return previous
            # Create an asset-scoped receipt with new IDs; evidence never crosses versions.
            result=dict(previous)
            blocks=result.pop('blocks')
            result.update(id=str(uuid4()),asset_id=asset_id,document_version_id=version_id,started_at=utc_now(),completed_at=utc_now(),attempt=len(self.store.ocr_runs(project_id,version_id,asset_id))+1)
            blocks=[dict(b,id=str(uuid4()),document_version_id=version_id,ocr_run_id=result['id']) for b in blocks]
            receipt=self.store.save_ocr(result,blocks)
            self.store.update_asset_ocr(project_id,version_id,asset_id,'completed')
            return receipt
        run=dict(id=str(uuid4()),project_id=project_id,document_version_id=version_id,asset_id=asset_id,asset_sha256=asset['sha256'],status='failed',attempt=len(self.store.ocr_runs(project_id,version_id,asset_id))+1,engine=self.adapter.engine,engine_version='unknown',language_config=getattr(self.adapter,'languages','ch,en'),preprocess_fingerprint='pending',configuration_fingerprint=fingerprint,average_confidence=None,text_sha256=None,normalized_text='',error_code=None,public_error=None,started_at=utc_now(),completed_at=None)
        blocks=[]
        try:
            if configuration_error: raise configuration_error
            output=self.adapter.recognize(self.object_store.read(asset['storage_key']))
            accepted=[b for b in output['blocks'] if b['normalized_text'] and b['confidence']>=self.min_confidence]
            average=sum(b['confidence'] for b in accepted)/len(accepted) if accepted else 0
            run.update(engine_version=output['engine_version'],preprocess_fingerprint=output['preprocess_fingerprint'],average_confidence=average)
            if not accepted or average<self.min_average_confidence: raise OCRError('ocr_low_confidence','Image text does not meet OCR quality threshold')
            text='\n'.join(b['normalized_text'] for b in accepted)
            run.update(status='completed',normalized_text=text,text_sha256=hashlib.sha256(text.encode()).hexdigest())
            blocks=[dict(b,id=str(uuid4()),project_id=project_id,document_version_id=version_id,ocr_run_id=run['id'],ordinal=i) for i,b in enumerate(accepted)]
        except OCRError as exc:
            run.update(error_code=exc.code,public_error=str(exc),completed_at=utc_now())
            self.store.save_ocr(run,[])
            self.store.update_asset_ocr(project_id,version_id,asset_id,'needs_attention')
            raise
        except Exception as exc:
            run.update(error_code='ocr_failed',public_error='Image recognition failed',completed_at=utc_now())
            self.store.save_ocr(run,[])
            self.store.update_asset_ocr(project_id,version_id,asset_id,'needs_attention')
            raise OCRError('ocr_failed','Image recognition failed') from exc
        run['completed_at']=utc_now()
        receipt=self.store.save_ocr(run,blocks)
        self.store.update_asset_ocr(project_id,version_id,asset_id,'completed',output.get('width'),output.get('height'))
        return receipt

    def ignore(self,project_id,version_id,asset_id,actor,reason):
        return self.store.ignore_asset(project_id,version_id,asset_id,actor,reason)

    @staticmethod
    def chunks(run,max_chars=2000):
        """Each OCR chunk keeps the asset/run/block identity for all extraction modes."""
        chunks=[]; group=[]; length=0
        def emit():
            text='\n'.join(b['normalized_text'] for b in group)
            spans=[];offset=0
            for b in group:
                stop=offset+len(b['normalized_text'])
                spans.append(dict(ocr_block_id=b['id'],chunk_start_char=offset,chunk_end_char=stop,block_start_char=0,block_end_char=len(b['normalized_text']),payload={'source_kind':'image_ocr','asset_id':run['asset_id'],'ocr_run_id':run['id']}))
                offset=stop+1
            chunks.append(dict(id='ocr-'+run['id']+'-'+str(len(chunks)),text=text,source_spans=spans,metadata=dict(source_kind='image_ocr',source_document_version_id=run['document_version_id'],source_asset_id=run['asset_id'],ocr_run_id=run['id'],ocr_block_ids=[b['id'] for b in group],average_confidence=run['average_confidence'],ocr_configuration_fingerprint=run.get('configuration_fingerprint'))))
        for block in run.get('blocks',[]):
            if group and length+len(block['normalized_text'])+1>max_chars: emit(); group=[]; length=0
            group.append(block); length+=len(block['normalized_text'])+1
        if group: emit()
        return chunks
