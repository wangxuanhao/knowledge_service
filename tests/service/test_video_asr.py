"""Video bytes, local inference receipts and immutable VAD evidence."""
import importlib.util
import io
import json
import subprocess
import zipfile

import pytest

from knowledge_service.integrations.document_parser import DocumentParser, SUPPORTED_EXTENSIONS
from knowledge_service.integrations.markdown_parser import parse_markdown
from knowledge_service.integrations.markdown_package import MarkdownPackageReader
from knowledge_service.integrations.source_object_store import SourceObjectStore
from knowledge_service.repository import Repository
from knowledge_service.services.source_documents import SourceDocumentService
from pg_support import target


@pytest.fixture
def video(tmp_path):
    path = tmp_path / 'sample.mp4'
    subprocess.run(['ffmpeg', '-v', 'error', '-f', 'lavfi', '-i', 'color=c=blue:s=64x64:r=10',
                    '-f', 'lavfi', '-i', 'sine=frequency=440:sample_rate=16000', '-t', '1',
                    '-c:v', 'libx264', '-pix_fmt', 'yuv420p', '-c:a', 'aac', '-y', str(path)], check=True)
    return path


def test_local_asr_adapter_exists_and_missing_models_fail_closed(tmp_path):
    assert importlib.util.find_spec('knowledge_service.integrations.video_asr') is not None
    from knowledge_service.integrations.video_asr import FunASRAdapter, ASRError
    adapter = FunASRAdapter(model_root=tmp_path/'missing', vad_root=tmp_path/'vad')
    assert adapter.available() is False
    with pytest.raises(ASRError) as error:
        adapter.recognize(b'bad')
    assert error.value.code == 'asr_engine_unavailable'


def test_markdown_video_references_are_inert_and_share_order():
    parsed = parse_markdown('<video src="assets/a.mp4" autoplay onerror="evil()"></video>\n\n[观看视频](assets/b.webm)\n\n<video src="https://example.test/c.mp4"></video>')
    assert len(parsed['images']) == 3
    assert all(ref['provenance']['media_type'] == 'video' for ref in parsed['images'])
    assert [ref['kind'] for ref in parsed['images']] == ['local', 'local', 'external']
    assert '<video' not in parsed['rendered_html']
    assert 'onerror' not in parsed['rendered_html'] and 'autoplay' not in parsed['rendered_html']
    assert parsed['rendered_html'].count('source-image') == 3


def test_video_package_only_freezes_referenced_assets(video):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, 'w') as archive:
        archive.writestr('rule.md', '[观看视频](assets/clip.mp4)')
        archive.writestr('assets/clip.mp4', video.read_bytes())
        archive.writestr('assets/unused.mp4', b'unused')
    package = MarkdownPackageReader().read(buffer.getvalue())
    asset = package['documents'][0]['assets'][0]
    assert asset['data'] == video.read_bytes()
    assert asset['mime_type'] == 'video/mp4' and asset['kind'] == 'local'
    assert len(package['documents'][0]['assets']) == 1


def test_standalone_video_parser_keeps_binary_as_asset_without_transcribing(video):
    assert {'.mp4', '.webm'} <= SUPPORTED_EXTENSIONS
    parsed = DocumentParser().parse(video, 'video.mp4')
    assert parsed.text == ''
    assert parsed.provenance['media_type'] == 'video'
    assert parsed.assets[0]['data'] == video.read_bytes()
    assert parsed.assets[0]['provenance']['media_type'] == 'video'


class Adapter:
    engine = 'sensevoice'; configuration_fingerprint = 'config-1'; calls = 0; fail = False
    def recognize(self, data):
        from knowledge_service.integrations.video_asr import ASRError
        self.calls += 1
        if self.fail:
            raise ASRError('asr_no_speech', '视频中没有可识别语音')
        return dict(engine_version='test', duration_ms=1000, normalized_text='安全要求\n遵守规则',
                    segments=[dict(start_ms=100, end_ms=400, text='安全要求', normalized_text='安全要求'),
                              dict(start_ms=600, end_ms=900, text='遵守规则', normalized_text='遵守规则')])


def test_asr_receipts_reuse_and_time_mapping_are_version_project_scoped(tmp_path, video):
    assert importlib.util.find_spec('knowledge_service.services.video_asr') is not None
    repo = Repository(target(tmp_path/'asr'))
    project = repo.create_project('ASR'); other = repo.create_project('Other')
    adapter = Adapter()
    service = SourceDocumentService(repo, SourceObjectStore(tmp_path/'objects'), asr_adapter=adapter)
    def persist(document):
        return service.persist(project['id'], document, video.read_bytes(), 'video.mp4', text='',
            assets=[dict(kind='local', status='available', original_path='video.mp4', data=video.read_bytes(),
                         mime_type='video/mp4', provenance={'media_type': 'video'})], provenance={'media_type': 'video'})
    one = persist('one'); asset = one['assets'][0]
    assert asset['kind'] == 'local' and asset['provenance']['media_type'] == 'video'
    assert '<video controls' in service.rendered(project['id'], one['id'])['html']
    run = service.video_asr.process_asset(project['id'], one['id'], asset['id'])
    assert run['status'] == 'completed' and adapter.calls == 1
    assert service.video_asr.process_asset(project['id'], one['id'], asset['id'])['id'] == run['id']
    two = persist('two'); copied = service.video_asr.process_asset(project['id'], two['id'], two['assets'][0]['id'])
    assert copied['id'] != run['id'] and adapter.calls == 1
    assert copied['segments'][0]['id'] != run['segments'][0]['id']
    from knowledge_service.services.source_ingest import prepare_source_ingest
    chunks, text, warnings = prepare_source_ingest(service, project['id'], one, {'extraction_mode':'documents'})
    assert text == '安全要求\n遵守规则' and not warnings
    chunk = repo.put_record(project['id'], dict(id='video-chunk', kind='chunk', text=text))
    service.store.add_spans(project['id'], one['id'], chunk['version_id'], chunks[0]['source_spans'])
    location = service.locate(project['id'], one['id'], chunk['version_id'])
    assert location['asset_id'] == asset['id'] and location['start_ms'] == 100 and location['end_ms'] == 900
    assert location['asr_run_id'] == run['id'] and len(location['segments']) == 2
    with pytest.raises(KeyError): service.asr(other['id'], one['id'], asset['id'])
    foreign_span = dict(chunks[0]['source_spans'][0], asr_segment_id=copied['segments'][0]['id'])
    foreign_chunk = repo.put_record(project['id'], dict(id='foreign', kind='chunk', text='foreign'))
    with pytest.raises(Exception): service.store.add_spans(project['id'], one['id'], foreign_chunk['version_id'], [foreign_span])
    adapter.fail = True
    from knowledge_service.integrations.video_asr import ASRError
    with pytest.raises(ASRError): service.video_asr.process_asset(project['id'], one['id'], asset['id'], retry=True)
    assert service.asr(project['id'], one['id'], asset['id'])['run']['error_code'] == 'asr_no_speech'
    assert service.asset(project['id'], one['id'], asset['id'])['data'] == video.read_bytes()
    repo.close()


def test_video_preview_does_not_start_asr(video):
    parsed = DocumentParser().parse(video, 'clip.mp4')
    from knowledge_service.services.document_preview import parsed_source_entry, source_preview
    preview, warnings = source_preview(parsed_source_entry(parsed))
    assert preview['images'][0]['media_type'] == 'video'
    assert preview['assets'][preview['images'][0]['asset_key']]['mime_type'] == 'video/mp4'
    assert any(item.get('code') == 'video_asr_on_ingest' for item in warnings if isinstance(item, dict))


def test_asr_error_retry_classification():
    from knowledge_service.services.ingest_retry import classify_error
    for code in ('asr_engine_unavailable', 'asr_no_audio', 'asr_no_speech', 'asr_invalid_media', 'asr_duration_exceeded'):
        error = type('ASRError', (RuntimeError,), {'code':code})('failure')
        classified = classify_error(error)
        assert classified.stage == 'asr' and classified.code == code and not classified.retryable
    error = type('ASRError', (RuntimeError,), {'code':'asr_timeout'})('timeout')
    assert classify_error(error).retryable


def test_video_probe_rejects_forged_bytes_and_duration(video):
    from knowledge_service.integrations.video_asr import inspect_video, ASRError
    with pytest.raises(ASRError) as error: inspect_video(b'pretend video', 'video/mp4')
    assert error.value.code == 'asr_invalid_media'
    with pytest.raises(ASRError) as error: inspect_video(video.read_bytes(), max_duration=.1)
    assert error.value.code == 'asr_duration_exceeded'


def test_video_chunk_preview_has_no_invented_text():
    from knowledge_service.services.document_preview import chunk_preview
    result=chunk_preview('', dict(chunk_strategy='fixed',chunk_size=1800,chunk_overlap=200), {'source_format':'mp4'})
    assert result['chunks']==[] and result['total']==0
    assert '正式处理' in result['media_note']


def test_external_video_never_uses_remote_image_importer(tmp_path):
    class Importer:
        def fetch(self,url): raise AssertionError('external videos must not be downloaded')
    service=SourceDocumentService(None,SourceObjectStore(tmp_path/'objects'),remote_image_importer=Importer())
    prepared=service.prepare(b'[video](https://example.test/a.mp4)','rule.md')
    assert prepared['assets'][0]['kind']=='external'
    assert prepared['assets'][0]['status']=='external_unfrozen'


def test_local_video_packager_collects_video_and_reports_external_separately(tmp_path,video):
    import runpy
    from pathlib import Path
    module=runpy.run_path(str(Path(__file__).resolve().parents[2]/'scripts/package_rule_documents.py'))
    (tmp_path/'rule.md').write_text('[video](sample.mp4)\n\n[external](https://example.test/remote.mp4)',encoding='utf-8')
    files,warnings={},[]
    document=module['prepare_document'](tmp_path,{'path':'rule.md'},files,warnings)
    assert 'sample.mp4' in document['files']
    assert warnings[0]['code']=='external_video'


@pytest.fixture
def adapter_models(tmp_path):
    from knowledge_service.integrations.video_asr import FunASRAdapter
    for name in ('asr','vad'):
        root=tmp_path/name;root.mkdir()
        for file in ('model.pt','config.yaml','configuration.json','am.mvn'):(root/file).write_bytes(b'local-model')
    return FunASRAdapter(tmp_path/'asr',tmp_path/'vad')


def test_adapter_uses_private_json_offline_process_and_cleans_files(adapter_models,video,monkeypatch):
    from pathlib import Path
    original=subprocess.run;requests=[]
    def run(args,**kwargs):
        if '-m' not in args:return original(args,**kwargs)
        request_path=Path(args[-1]);request=json.loads(request_path.read_text(encoding='utf-8'));requests.append(request_path)
        assert kwargs['env']['HF_HUB_OFFLINE']=='1' and kwargs['env']['MODELSCOPE_OFFLINE']=='1'
        assert kwargs['timeout']>=300 and kwargs['stdout'] is not subprocess.PIPE
        Path(request['result']).write_text(json.dumps(dict(engine_version='test',segments=[dict(start_ms=100,end_ms=800,text='hello',normalized_text='hello')])),encoding='utf-8')
        return subprocess.CompletedProcess(args,0)
    monkeypatch.setattr(subprocess,'run',run)
    result=adapter_models.recognize(video.read_bytes())
    assert result['segments'][0]['start_ms']==100
    assert requests and all(not path.exists() for path in requests)


def test_adapter_timeout_releases_files_and_worker_failure_is_public(adapter_models,video,monkeypatch):
    from pathlib import Path
    from knowledge_service.integrations.video_asr import ASRError
    original=subprocess.run;requests=[]
    def run(args,**kwargs):
        if '-m' not in args:return original(args,**kwargs)
        requests.append(Path(args[-1]))
        raise subprocess.TimeoutExpired(args,kwargs['timeout'])
    monkeypatch.setattr(subprocess,'run',run)
    with pytest.raises(ASRError) as error:adapter_models.recognize(video.read_bytes())
    assert error.value.code=='asr_timeout'
    assert all(not path.exists() for path in requests)


def test_model_identity_changes_fingerprint(adapter_models):
    before=adapter_models.configuration_fingerprint
    (adapter_models.model_root/'model.pt').write_bytes(b'updated-local-model')
    assert adapter_models.configuration_fingerprint!=before


def test_silent_video_without_audio_is_an_explicit_error(adapter_models,video,tmp_path):
    from knowledge_service.integrations.video_asr import ASRError
    path=tmp_path/'no-audio.mp4'
    subprocess.run(['ffmpeg','-v','error','-i',str(video),'-c:v','copy','-an','-y',str(path)],check=True)
    with pytest.raises(ASRError) as error:adapter_models.recognize(path.read_bytes())
    assert error.value.code=='asr_no_audio'


def test_video_public_upload_preview_asr_and_authorized_source_routes(tmp_path,video):
    from fastapi.testclient import TestClient
    from knowledge_service.api import create_app
    from knowledge_service.integrations.embeddings import HashingEncoder
    with TestClient(create_app(tmp_path/'api',encoder=HashingEncoder(),upload_temp=tmp_path/'uploads')) as client:
        adapter=Adapter();client.app.state.source_documents.video_asr.adapter=adapter
        project=client.post('/api/projects',json={'name':'Video API'}).json()['id']
        response=client.post(f'/api/projects/{project}/documents/upload/preview',
            files={'file':('speech#1.mp4',video.read_bytes(),'video/mp4')},data={'options':json.dumps({'extraction_mode':'documents'}),'preview_view':'chunks'})
        assert response.status_code==200,response.text
        assert response.json()['chunks']==[] and adapter.calls==0
        response=client.post(f'/api/projects/{project}/documents/upload',
            files={'file':('speech#1.mp4',video.read_bytes(),'video/mp4')},data={'options':json.dumps({'extraction_mode':'documents'})})
        assert response.status_code==201,response.text
        version=response.json()['document']['metadata']['source_document_version_id']
        prefix=f'/api/projects/{project}/source-documents/{version}'
        source=client.get(prefix).json();asset=source['assets'][0]
        rendered=client.get(prefix+'/rendered').text
        assert '<video controls' in rendered and 'data-asset-id="'+asset['id']+'"' in rendered
        assert client.get(prefix+'/source').content==video.read_bytes()
        assert client.get(prefix+'/assets/'+asset['id']).headers['content-type'].startswith('video/mp4')
        run=client.get(prefix+'/assets/'+asset['id']+'/asr').json()['run']
        assert run['segments'][0]['start_ms']==100 and adapter.calls==1
        location=client.get(prefix+'/locate',params={'chunk_version_id':source['chunks'][0]['version_id']}).json()
        assert location['start_ms']==100 and location['asset_id']==asset['id']
        adapter.fail=True
        assert client.post(prefix+'/assets/'+asset['id']+'/asr/retry').status_code==422
        assert client.get(prefix+'/assets/'+asset['id']).content==video.read_bytes()


def test_batch_documents_retains_video_with_no_speech_and_zero_chunks(tmp_path,video):
    from knowledge_service.services.service import KnowledgeService
    from knowledge_service.integrations.embeddings import HashingEncoder
    from knowledge_service.services.document_ingest_runner import DocumentIngestRunner
    from knowledge_service.models import IngestBatchSettings
    repo=Repository(target(tmp_path/'no-speech'));project=repo.create_project('No speech')['id']
    service=KnowledgeService(repo,HashingEncoder());adapter=Adapter();adapter.fail=True
    sources=SourceDocumentService(repo,SourceObjectStore(tmp_path/'objects'),asr_adapter=adapter)
    settings=IngestBatchSettings(extraction_mode='documents').model_dump()
    batch=repo.ingest_batches.create_batch(project,settings)
    repo.ingest_batches.add_item(project,batch['id'],'sample.mp4',video.read_bytes(),'hash','video',100,1000000)
    repo.ingest_batches.transition(project,batch['id'],'start');item=repo.ingest_batches.claim(batch['id'],'worker',60)
    result=DocumentIngestRunner(service,repo.ingest_batches,repo.ingest_checkpoints,sources).run(item,settings,'worker')
    assert result['document']['metadata']['status']=='ready'
    assert not [record for record in result['records'] if record['kind']=='chunk']
    assert any(w.get('code')=='asr_no_speech' for w in result['document']['metadata']['warnings'] if isinstance(w,dict))
    source=sources.get(project,result['document']['metadata']['source_document_version_id'])
    assert sources.asset(project,source['id'],source['assets'][0]['id'])['data']==video.read_bytes()
    repo.ingest_batches.finish(item['id'],'worker',item['lease_token'],'succeeded');repo.close()


def test_video_codec_must_be_playable_by_supported_browser(video,tmp_path):
    from knowledge_service.integrations.video_asr import inspect_video,ASRError
    path=tmp_path/'unsupported.mp4'
    subprocess.run(['ffmpeg','-v','error','-i',str(video),'-c:v','mpeg4','-c:a','aac','-y',str(path)],check=True)
    with pytest.raises(ASRError) as error:inspect_video(path.read_bytes(),'video/mp4')
    assert error.value.code=='asr_unsupported_codec'


@pytest.mark.parametrize('name',['speech#1.mp4','speech%?1.mp4','中文视频.mp4'])
def test_standalone_video_special_filename_keeps_one_playable_asset(tmp_path,video,name):
    parsed=DocumentParser().parse(video,name)
    repo=Repository(target(tmp_path/'filename'))
    project=repo.create_project('Video filename')['id']
    service=SourceDocumentService(repo,SourceObjectStore(tmp_path/'objects'))
    prepared=service.prepare(video.read_bytes(),name,text=parsed.text,assets=parsed.assets,provenance=parsed.provenance)
    assert len(prepared['assets'])==1
    assert prepared['assets'][0]['status']=='available'
    assert prepared['assets'][0]['original_path']==name and prepared['original_name']==name
    source=service.publish(project,'filename',prepared)
    asset=source['assets'][0]
    rendered=service.rendered(project,source['id'])['html']
    assert '<video controls' in rendered and 'data-asset-id="'+asset['id']+'"' in rendered
    assert f'/source-documents/{source["id"]}/assets/{asset["id"]}' in rendered
    assert service.source(project,source['id'])['original_name']==name
    assert service.asset(project,source['id'],asset['id'])['data']==video.read_bytes()
    from knowledge_service.services.document_preview import parsed_source_entry,source_preview
    preview,warnings=source_preview(parsed_source_entry(parsed))
    assert len(preview['images'])==1 and preview['images'][0]['status']=='available'
    repo.close()
