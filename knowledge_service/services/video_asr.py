"""Asset-scoped immutable ASR receipts, retries and project-only reuse."""
import hashlib
import threading
from uuid import uuid4

from ..core.time import utc_now
from ..integrations.video_asr import ASRError, ASRSegment, FunASRAdapter


_PROCESS_LOCK = threading.RLock()


class VideoASRService:
    def __init__(self, store, object_store, adapter=None):
        self.store, self.object_store, self.adapter = store, object_store, adapter or FunASRAdapter()

    def process_asset(self, project_id, version_id, asset_id, retry=False):
        with _PROCESS_LOCK:
            return self._process_asset(project_id, version_id, asset_id, retry)

    def _process_asset(self, project_id, version_id, asset_id, retry):
        asset = self.store.get_asset(project_id, version_id, asset_id)
        if asset['status'] == 'ignored' or asset['kind'] in {'external', 'unsafe'}:
            return dict(status=asset['status'], segments=[])
        if asset.get('provenance', {}).get('media_type') != 'video':
            raise ASRError('asr_invalid_media', '该附件不是视频')
        configuration_error = None
        try:
            fingerprint = self.adapter.configuration_fingerprint
        except Exception as exc:
            fingerprint = 'unavailable'
            configuration_error = exc if isinstance(exc, ASRError) else ASRError('asr_engine_unavailable', '本地视频识别配置不可用')
        attempts = self.store.asr_runs(project_id, version_id, asset_id)
        previous = next((run for run in reversed(attempts) if run['status']=='completed' and run['configuration_fingerprint']==fingerprint),None)
        previous = previous or self.store.reusable_asr(project_id, asset['sha256'], fingerprint)
        if previous and not retry:
            if previous['asset_id'] == asset_id and previous['document_version_id'] == version_id:
                return previous
            run = {key: value for key, value in previous.items() if key != 'segments'}
            run.update(id=str(uuid4()), asset_id=asset_id, document_version_id=version_id,
                       started_at=utc_now(), completed_at=utc_now(), attempt=len(attempts)+1)
            segments = [dict(segment, id=str(uuid4()), document_version_id=version_id, asr_run_id=run['id'])
                        for segment in previous['segments']]
            receipt = self.store.save_asr(run, segments)
            self.store.update_asset_ocr(project_id, version_id, asset_id, 'completed')
            return receipt
        run = dict(id=str(uuid4()), project_id=project_id, document_version_id=version_id, asset_id=asset_id,
                   asset_sha256=asset['sha256'] or '', status='failed', attempt=len(attempts)+1,
                   engine=self.adapter.engine, engine_version='unknown', configuration_fingerprint=fingerprint,
                   duration_ms=None, text_sha256=None, normalized_text='', error_code=None, public_error=None,
                   started_at=utc_now(), completed_at=utc_now())
        try:
            if not asset.get('storage_key'):
                raise ASRError('source_asset_missing', '本地视频附件缺失')
            if configuration_error:
                raise configuration_error
            output = self.adapter.recognize(self.object_store.read(asset['storage_key']))
            segments = [ASRSegment.model_validate(item).model_dump() for item in output['segments']]
            duration = output['duration_ms']
            if not isinstance(duration, int) or duration <= 0 or any(segment['end_ms'] > duration for segment in segments):
                raise ASRError('asr_runtime_error', '视频语音时段无效')
            if any(left['end_ms'] > right['start_ms'] for left, right in zip(segments, segments[1:])):
                raise ASRError('asr_runtime_error', '视频语音时段顺序无效')
            if not segments:
                raise ASRError('asr_no_speech', '视频中没有可识别语音')
            text = '\n'.join(segment['normalized_text'] for segment in segments)
            run.update(status='completed', engine_version=output['engine_version'], duration_ms=duration,
                       normalized_text=text, text_sha256=hashlib.sha256(text.encode()).hexdigest())
            segments = [dict(segment, id=str(uuid4()), project_id=project_id, document_version_id=version_id,
                             asr_run_id=run['id'], ordinal=index) for index, segment in enumerate(segments)]
        except Exception as exc:
            error = exc if isinstance(exc, ASRError) else ASRError('asr_runtime_error', '本地视频语音转写失败')
            run.update(error_code=error.code, public_error=str(error), completed_at=utc_now())
            self.store.save_asr(run, [])
            self.store.update_asset_ocr(project_id, version_id, asset_id, 'needs_attention')
            if error is exc:
                raise
            raise error from exc
        run['completed_at'] = utc_now()
        receipt = self.store.save_asr(run, segments)
        self.store.update_asset_ocr(project_id, version_id, asset_id, 'completed')
        return receipt

    @staticmethod
    def chunks(run, max_chars=2000):
        chunks, group, length = [], [], 0
        def emit():
            spans, offset = [], 0
            for segment in group:
                end = offset+len(segment['normalized_text'])
                spans.append(dict(asr_segment_id=segment['id'], chunk_start_char=offset, chunk_end_char=end,
                                  block_start_char=0, block_end_char=len(segment['normalized_text']),
                                  payload=dict(source_kind='video_asr', asset_id=run['asset_id'], asr_run_id=run['id'],
                                               start_ms=segment['start_ms'], end_ms=segment['end_ms'])))
                offset = end+1
            chunks.append(dict(id='asr-'+run['id']+'-'+str(len(chunks)), text='\n'.join(s['normalized_text'] for s in group),
                source_spans=spans, metadata=dict(source_kind='video_asr', source_document_version_id=run['document_version_id'],
                    source_asset_id=run['asset_id'], asr_run_id=run['id'], asr_segment_ids=[s['id'] for s in group],
                    start_ms=group[0]['start_ms'], end_ms=group[-1]['end_ms'], asr_configuration_fingerprint=run['configuration_fingerprint'])))
        for segment in run.get('segments', []):
            if group and length+len(segment['normalized_text'])+1 > max_chars:
                emit(); group, length = [], 0
            group.append(segment)
            length += len(segment['normalized_text'])+1
        if group:
            emit()
        return chunks
