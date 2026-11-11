"""Document-level dates retain exact normalized-text evidence and manual intent."""
import hashlib
import io
import json
import time
import zipfile

import pytest
from fastapi.testclient import TestClient

from knowledge_service.api import create_app
from knowledge_service.integrations.embeddings import HashingEncoder
from knowledge_service.models import IngestSettings


def resolve(text, **settings):
    from knowledge_service.services.document_temporal import resolve_document_temporal
    return resolve_document_temporal(text, {'document_time_mode': 'auto', **settings})


def field(result, name):
    return result['metadata']['document_temporal']['fields'][name]


def test_auto_model_keeps_publication_separate_from_effective_time():
    value = IngestSettings(document_time_mode='auto', published_at='2026-10-01')
    assert value.valid_from is None
    assert value.metadata['effective_time_source'] == 'unknown'
    legacy = IngestSettings(published_at='2026-10-01')
    assert legacy.valid_from == '2026-10-01T00:00:00.000000Z'
    assert legacy.metadata['effective_time_source'] == 'published_at'


def test_headers_use_shanghai_and_exact_codepoint_evidence():
    text = '# 😀规则\n\n发布日期：2026-09-25  \n生效时间：2026-10-29\n是否生效：生效中\n\n正文。'
    value = resolve(text, metadata={'source_document_version_id': 'frozen-source'})
    assert value['published_at'] == '2026-09-24T16:00:00.000000Z'
    assert value['valid_from'] == '2026-10-28T16:00:00.000000Z'
    assert value['valid_until'] is None
    assert value['metadata']['effective_time_source'] == 'extracted'
    assert value['metadata']['source_document_version_id'] == 'frozen-source'
    evidence = value['metadata']['document_temporal']
    assert evidence['text_sha256'] == hashlib.sha256(text.encode()).hexdigest()
    assert evidence['coordinate_basis'] == 'normalized_search_text'
    for name in ('published_at', 'valid_from'):
        candidate = field(value, name)['candidates'][0]
        assert text[candidate['start_char']:candidate['end_char']] == candidate['raw']
        assert candidate['timezone'] == 'Asia/Shanghai'


def test_latest_revision_effective_replaces_first_but_preserves_both():
    text = ('# 服务费一览表\n发布日期：2023-03-12\n生效日期：未提供\n\n'
            '4.本表于2023年3月12日00：00：00首次生效，于2026年9月23日00：00：00最新修订生效。')
    value = resolve(text)
    assert value['valid_from'] == '2026-09-22T16:00:00.000000Z'
    assert {c['kind'] for c in field(value, 'valid_from')['candidates']} == {'first_effective', 'latest_effective'}
    revised = resolve('本规则于2026年10月22日修订，于2026年10月29日生效。')
    assert revised['metadata']['revised_at'] == '2026-10-21T16:00:00.000000Z'
    assert revised['valid_from'] == '2026-10-28T16:00:00.000000Z'


@pytest.mark.parametrize(('text', 'end'), [
    ('本规则自2025年10月1日起失效。', '2025-09-30T16:00:00.000000Z'),
    ('本规范有效至2025年10月1日。', '2025-10-01T16:00:00.000000Z'),
    ('# 规则[2025年10月01日已失效]\n正文', '2025-09-30T16:00:00.000000Z'),
    ('失效日期：2025-10-01', '2025-09-30T16:00:00.000000Z'),
])
def test_expiry_semantics(text, end):
    assert resolve(text)['valid_until'] == end


def test_own_range_is_inclusive_and_explicit_timezone_is_preserved():
    value = resolve('本规则有效期为2025年1月1日至2025年12月31日。')
    assert value['valid_from'] == '2024-12-31T16:00:00.000000Z'
    assert value['valid_until'] == '2025-12-31T16:00:00.000000Z'
    zoned = resolve('发布时间：2025-01-01T10:30:00+09:00')
    assert zoned['published_at'] == '2025-01-01T01:30:00.000000Z'
    assert field(zoned, 'published_at')['candidates'][0]['timezone'] == '+09:00'


@pytest.mark.parametrize('text', [
    '商家应在2025年10月1日前退款，活动有效至2025年12月1日。',
    '根据《其他规则》，该规则自2025年10月1日起失效。',
    '> 本规则于2025年10月1日生效。',
    '```text\n本规则于2025年10月1日生效。\n```',
    '例如：本规则于2025年10月1日生效。',
    '“本规则于2025年10月1日生效。”',
    '`本规则于2025年10月1日生效。`',
    "'本规则于2025年10月1日生效。'",
    '# 《其他规范》下线公示通知\n发布日期：2023-06-02\n\n本规范自2023年6月9日起失效。',
])
def test_foreign_deadline_example_and_quoted_dates_do_not_set_boundaries(text):
    value = resolve(text)
    assert value['valid_from'] is None
    assert value['valid_until'] is None


def test_invalid_and_conflicting_dates_need_review():
    invalid = resolve('发布日期：2025-02-30\n生效日期：2025-13-01')
    assert invalid['published_at'] is None
    assert field(invalid, 'published_at')['status'] == 'review'
    assert field(invalid, 'valid_from')['candidates'][0]['raw'] == '2025-13-01'
    conflict = resolve('生效日期：2025-01-01\n\n本规则于2025年2月1日生效。')
    assert conflict['valid_from'] is None
    assert field(conflict, 'valid_from')['status'] == 'review'
    assert len(field(conflict, 'valid_from')['candidates']) == 2


def test_multiple_dates_in_one_header_are_ambiguous():
    value = resolve('生效日期：2025-01-01 或 2025-02-01')
    assert value['valid_from'] is None
    assert field(value, 'valid_from')['status'] == 'review'


def test_notice_ownership_uses_supplied_title_and_ignores_fenced_h1():
    value = resolve('本规范自2023年6月9日起废止。', title='《其他规范》下线公示通知')
    assert value['valid_until'] is None
    code = resolve('```markdown\n# 本规则[2025年10月1日已失效]\n```')
    assert code['valid_until'] is None


@pytest.mark.parametrize('foreign', ['商品于2025年2月1日失效', '活动有效至2025年2月1日',
                                     '活动有效期为2025年2月1日至2025年3月1日'])
def test_new_subject_after_comma_ends_document_date_ownership(foreign):
    value = resolve('本规则自2025年1月1日生效，' + foreign + '。')
    assert value['valid_from'] == '2024-12-31T16:00:00.000000Z'
    assert value['valid_until'] is None


@pytest.mark.parametrize('text', ['    本规则自2025年1月1日起生效。',
                                '“引文\n本规则自2025年1月1日起生效。\n”'])
def test_indented_code_and_multiline_quotation_are_excluded(text):
    assert resolve(text)['valid_from'] is None


def test_expired_title_and_active_source_status_preserve_literal_conflict():
    value = resolve('# 规则【2025年1月1日已失效】\n是否生效：生效中')
    assert 'title_status_conflict' in value['metadata']['document_temporal']['warnings']


@pytest.mark.parametrize('temporal', ['legacy', {'fields': 'legacy'}, {'fields': {'valid_from': 'legacy'}}])
def test_legacy_arbitrary_metadata_cannot_break_validation_or_resolution(temporal):
    settings = IngestSettings(metadata={'document_temporal': temporal})
    assert settings.valid_from is None
    value = resolve('生效日期：2025-01-01', metadata={'document_temporal': temporal})
    assert field(value, 'valid_from')['status'] == 'extracted'


def test_manual_fields_win_and_repeated_resolution_preserves_origin():
    text = '发布日期：2025-01-01\n生效日期：2025-02-01\n失效日期：2025-03-01'
    value = resolve(text, valid_from='2025-02-10', published_at='2025-01-10')
    assert value['valid_from'] == '2025-02-10T00:00:00.000000Z'
    assert field(value, 'valid_from')['status'] == 'manual'
    assert value['published_at'] == '2025-01-10T00:00:00.000000Z'
    assert resolve(text, **value) == value
    auto = resolve(text)
    revalidated = IngestSettings.model_validate(auto).model_dump()
    assert revalidated['metadata']['effective_time_source'] == 'extracted'
    assert resolve(text, **revalidated)['metadata']['document_temporal'] == auto['metadata']['document_temporal']


@pytest.mark.parametrize('manual', [{}, {'valid_from': '2026-01-01'}, {'valid_until': '2024-01-01'}])
def test_reversed_auto_interval_preserves_only_manual_boundaries(manual):
    value = resolve('生效日期：2025-06-01\n失效日期：2025-01-01', **manual)
    assert value['valid_from'] == ('2026-01-01T00:00:00.000000Z' if 'valid_from' in manual else None)
    assert value['valid_until'] == ('2024-01-01T00:00:00.000000Z' if 'valid_until' in manual else None)
    assert 'reversed_interval' in value['metadata']['document_temporal']['warnings']


def test_raw_expired_status_does_not_invent_a_date():
    value = resolve('发布日期：2025-01-01\n是否生效：已失效')
    assert value['valid_from'] is None
    assert value['valid_until'] is None
    temporal = value['metadata']['document_temporal']
    assert temporal['source_status']['raw'] == '已失效'
    assert 'expired_status_without_date' in temporal['warnings']


@pytest.fixture
def client(tmp_path):
    with TestClient(create_app(tmp_path / 'temporal.sqlite', encoder=HashingEncoder(),
                               upload_temp=tmp_path / 'uploads')) as value:
        yield value


def test_normal_ingest_inherits_dates_filters_and_retry_metadata(client):
    project = client.post('/api/projects', json={'name': 'document time'}).json()['id']
    text = '# 规则\n发布日期：2025-01-01\n生效日期：2025-02-01\n失效日期：2025-03-01\n\n正文规则。'
    service = client.app.state.service
    payload = {'title': '规则', 'text': text, 'document_time_mode': 'auto', 'extraction_mode': 'documents'}
    result = service.ingest(project, payload)
    document = result['document']
    chunks = result['records']
    assert chunks
    for row in [document, *chunks]:
        assert row['valid_from'] == '2025-01-31T16:00:00.000000Z'
        assert row['valid_until'] == '2025-02-28T16:00:00.000000Z'
        assert row['metadata']['document_temporal'] == document['metadata']['document_temporal']
    assert chunks[0]['metadata']['source_version_id']
    assert len(service.scoped(project, {'kinds': ['document']})) == 1
    assert service.scoped(project, {'kinds': ['document'], 'valid_at': '2025-03-01'}) == []
    assert len(service.scoped(project, {'kinds': ['document'], 'valid_at': '2025-02-01'})) == 1
    replay = service.ingest(project, {**payload, '_document_id': document['id']})
    assert replay['replayed'] is True
    assert replay['document']['metadata']['effective_time_source'] == 'extracted'
    assert replay['document']['metadata']['document_temporal'] == document['metadata']['document_temporal']


def test_raw_and_upload_previews_share_read_only_document_time(client):
    project = client.post('/api/projects', json={'name': 'preview time'}).json()['id']
    text = '# 规则\n发布日期：2025-01-01\n\n业务正文。'
    settings = {'document_time_mode': 'auto', 'extraction_mode': 'documents'}
    raw = client.post(f'/api/projects/{project}/documents/preview', json={**settings, 'title': '规则', 'text': text})
    upload = client.post(f'/api/projects/{project}/documents/upload/preview',
        files={'file': ('rule.md', text.encode())}, data={'options': json.dumps(settings)})
    assert raw.status_code == upload.status_code == 200
    assert raw.json()['document_time'] == upload.json()['document_time']
    assert raw.json()['document_time']['valid_from'] is None
    assert raw.json()['document_time']['published_at'] == '2024-12-31T16:00:00.000000Z'
    assert client.post(f'/api/projects/{project}/records/page', json={}).json()['records'] == []


@pytest.mark.parametrize('endpoint', ['', '/upload', '/upload/jobs'])
def test_public_document_routes_resolve_dates_and_keep_frozen_source(client, endpoint):
    project = client.post('/api/projects', json={'name': 'entry time'}).json()['id']
    text = '# 规则\n发布日期：2025-01-01\n生效日期：2025-02-01\n\n正文。'
    options = {'document_time_mode': 'auto', 'extraction_mode': 'documents'}
    url = f'/api/projects/{project}/documents' + endpoint
    if endpoint:
        response = client.post(url, files={'file': ('rules.md', text.encode())},
                               data={'options': json.dumps(options)})
    else:
        response = client.post(url, json={**options, 'title': '规则', 'text': text})
    assert response.status_code == (202 if endpoint.endswith('/jobs') else 201), response.text
    if endpoint.endswith('/jobs'):
        for _ in range(150):
            job = client.get('/api/jobs/' + response.json()['id']).json()
            if job['status'] not in ('queued', 'running'):
                break
            time.sleep(.05)
        assert job['status'] == 'completed', job
        document = client.app.state.service.data.current_records(project, kinds=['document'])[0]
    else:
        document = response.json()['document']
    assert document['valid_from'] == '2025-01-31T16:00:00.000000Z'
    assert document['metadata']['effective_time_source'] == 'extracted'
    if endpoint:
        source_id = document['metadata']['source_document_version_id']
        sources = client.app.state.source_documents
        assert sources.source(project, source_id)['data'] == text.encode()
        chunk = client.app.state.service.data.current_records(project, kinds=['chunk'])[0]
        assert sources.locate(project, source_id, chunk['version_id'])['status'] == 'available'


def test_zip_batch_resolves_each_document_independently(client):
    project = client.post('/api/projects', json={'name': 'batch time'}).json()['id']
    batch = client.post(f'/api/projects/{project}/ingest-batches', json={'settings': {
        'document_time_mode': 'auto', 'extraction_mode': 'documents', 'requested_concurrency': 1}}).json()
    base = f'/api/projects/{project}/ingest-batches/{batch["id"]}'
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, 'w') as archive:
        archive.writestr('dated.md', '# 规则\n生效日期：2025-02-01\n\n正文。')
        archive.writestr('unknown.md', '# 未知\n发布日期：2025-01-01\n\n正文。')
        archive.writestr('code.md', '# 代码\n\n`本规则自2025年1月1日起生效。`')
    raw = buffer.getvalue()
    response = client.post(base + '/files', files={'file': ('rules.zip', raw)})
    assert response.status_code == 201, response.text
    assert client.post(base + '/start').status_code == 202
    for _ in range(150):
        items = client.get(base + '/items').json()['items']
        if all(item['status'] in ('completed', 'needs_attention') for item in items):
            break
        time.sleep(.1)
    assert all(item['status'] == 'completed' for item in items), items
    docs = {item['original_name']: item['result']['document'] for item in items}
    assert docs['dated.md']['valid_from'] == '2025-01-31T16:00:00.000000Z'
    assert docs['unknown.md']['valid_from'] is None
    assert docs['unknown.md']['metadata']['effective_time_source'] == 'unknown'
    assert docs['code.md']['valid_from'] is None
    for document in docs.values():
        source_id = document['metadata']['source_document_version_id']
        assert client.get(f'/api/projects/{project}/source-documents/{source_id}/package').content == raw


def test_failed_upload_retry_reuses_dates_and_frozen_source(client, tmp_path, monkeypatch):
    from knowledge_service.models import UploadOptions
    from knowledge_service.services.document_uploads import StagedUpload

    project = client.post('/api/projects', json={'name': 'retry time'}).json()['id']
    text = '# 规则\n生效日期：2025-02-01\n\n正文。'
    path = tmp_path / 'retry.md'
    path.write_text(text, encoding='utf-8')
    staged = StagedUpload(path, path.name, len(path.read_bytes()), hashlib.sha256(path.read_bytes()).hexdigest())
    options = UploadOptions(document_time_mode='auto', extraction_mode='documents')
    service = client.app.state.service
    uploads = client.app.state.document_uploads
    sources = client.app.state.source_documents
    with monkeypatch.context() as patch:
        def fail(*args, **kwargs):
            raise RuntimeError('simulated indexing interruption')
        patch.setattr(service, 'write', fail)
        with pytest.raises(RuntimeError) as error:
            uploads.ingest(service, project, staged, options, task_id='temporal-retry', sources=sources)
        assert str(error.value.__cause__) == 'simulated indexing interruption'
    failed = service.data.current_records(project, kinds=['document'])[0]
    evidence = failed['metadata']['document_temporal']
    monkeypatch.setattr(uploads, 'parse', lambda *_: pytest.fail('retry reparsed frozen original'))
    document = uploads.ingest(service, project, staged, options, task_id='temporal-retry', sources=sources)['document']
    assert document['metadata']['document_temporal'] == evidence
    assert document['metadata']['source_document_version_id'] == failed['metadata']['source_document_version_id']
    assert document['metadata']['effective_time_source'] == 'extracted'
    path.unlink()
    assert uploads.ingest(service, project, staged, options, task_id='temporal-retry', sources=sources)['replayed']


def test_legacy_direct_manual_ingest_preserves_existing_time_origin(client):
    project = client.post('/api/projects', json={'name': 'legacy time'}).json()['id']
    service = client.app.state.service
    for source in ('explicit', 'published_at', 'unknown'):
        result = service.ingest(project, {'title': source, 'text': 'Legacy rule.',
            'extraction_mode': 'documents', 'valid_from': '2025-01-01',
            'metadata': {'effective_time_source': source}})
        assert result['document']['metadata']['effective_time_source'] == source


def test_supplied_title_only_notice_has_same_preview_and_ingest_dates(client):
    project = client.post('/api/projects', json={'name': 'notice time'}).json()['id']
    body = {'title': '《其他规范》下线公示通知', 'text': '本规范自2023年6月9日起废止。',
            'document_time_mode': 'auto', 'extraction_mode': 'documents'}
    preview = client.post(f'/api/projects/{project}/documents/preview', json=body).json()['document_time']
    response = client.post(f'/api/projects/{project}/documents', json=body)
    assert response.status_code == 201, response.text
    document = response.json()['document']
    assert document['valid_until'] == preview['valid_until'] is None
    assert document['metadata']['document_temporal'] == preview['document_temporal']


@pytest.mark.parametrize('body', ['```text\n本规则自2025年1月1日起生效。\n```',
                                '> 本规则自2025年1月1日起生效。',
                                '引用代码 `本规则自2025年1月1日起生效。`'])
def test_upload_normalization_keeps_code_and_quotation_dates_ineligible(client, body):
    project = client.post('/api/projects', json={'name': 'normalized exclusions'}).json()['id']
    text = '# 规则\n\n发布日期：2024-12-01\n\n' + body
    options = {'document_time_mode': 'auto', 'extraction_mode': 'documents'}
    url = f'/api/projects/{project}/documents/upload'
    preview = client.post(url + '/preview', files={'file': ('rule.md', text.encode())},
                          data={'options': json.dumps(options)})
    response = client.post(url, files={'file': ('rule.md', text.encode())},
                           data={'options': json.dumps(options)})
    assert response.status_code == 201, response.text
    document = response.json()['document']
    assert document['valid_from'] == preview.json()['document_time']['valid_from'] is None
    temporal = document['metadata']['document_temporal']
    assert temporal['text_sha256'] == hashlib.sha256(document['text'].encode()).hexdigest()
    assert temporal['coordinate_basis'] == 'normalized_search_text'
    candidate = temporal['fields']['published_at']['candidates'][0]
    assert document['text'][candidate['start_char']:candidate['end_char']] == candidate['raw']


@pytest.mark.parametrize('raw', ['legacy', {'text': 'legacy'}, ['legacy']])
def test_invalid_legacy_revision_metadata_is_nonfatal_and_warned(raw):
    text = '生效日期：2025-01-01'
    value = resolve(text, metadata={'revised_at': raw})
    assert value['valid_from'] == '2024-12-31T16:00:00.000000Z'
    assert 'invalid_legacy_revised_at' in value['metadata']['document_temporal']['warnings']
    assert field(value, 'revised_at')['status'] == 'review'
    assert field(value, 'revised_at')['supplied_value'] == raw
    assert resolve(text, **value) == value


@pytest.mark.parametrize('legacy', [False, True])
def test_source_exclusions_keep_old_blocks_and_appended_ocr_offsets(legacy):
    from knowledge_service.integrations.markdown_parser import parse_markdown
    from knowledge_service.services.chunking import split_source_blocks
    from knowledge_service.services.document_temporal import resolve_document_temporal, source_temporal_exclusions

    original = '# 😀规则\n\n`本规则自2025年1月1日起生效。`\n\n> 本规则于2025年2月1日生效。'
    parsed = parse_markdown(original, 'source-version')
    if legacy:
        for block in parsed['blocks']:
            block['payload'].pop('temporal_excluded_ranges', None)
    chunks, base = split_source_blocks(parsed['blocks'], {'chunk_size': 100, 'chunk_overlap': 10}, return_text=True)
    ocr = '本规则自2026年1月1日起生效。'
    text = base + '\n\n' + ocr
    chunks.append({'start_char': len(base) + 2, 'text': ocr, 'source_spans': [{
        'ocr_block_id': 'ocr-1', 'chunk_start_char': 0, 'chunk_end_char': len(ocr)}]})
    source = {'blocks': parsed['blocks'], 'source_text': original}
    excluded = source_temporal_exclusions(source)
    value = resolve_document_temporal(text, {'document_time_mode': 'auto'}, excluded_ranges=excluded)
    assert value['valid_from'] == '2025-12-31T16:00:00.000000Z'
    candidate = field(value, 'valid_from')['candidates'][0]
    assert candidate['start_char'] > len(base)
    assert text[candidate['start_char']:candidate['end_char']] == '2026年1月1日'


def test_many_unmatched_opening_quotes_keep_later_document_clause_eligible():
    text = '“' * 4000 + '本规则自2025年1月1日起生效。”\n本规则自2026年1月1日起生效。'
    assert resolve(text)['valid_from'] == '2025-12-31T16:00:00.000000Z'


def test_oversized_table_code_exclusions_use_original_stream_offsets(client):
    project = client.post('/api/projects', json={'name': 'table exclusions'}).json()['id']
    text = ('# 规则\n\n| `本规则自2025年1月1日起生效。` | 内容 |\n|---|---|\n'
            + '\n'.join('| `本规则自2027年1月1日起生效。` | ' + '内容' * 20 + ' |' for _ in range(8))
            + '\n\n本规则自2026年1月1日起生效。')
    options = {'document_time_mode': 'auto', 'extraction_mode': 'documents',
               'chunk_size': 100, 'chunk_overlap': 0}
    response = client.post(f'/api/projects/{project}/documents/upload',
        files={'file': ('table.md', text.encode())}, data={'options': json.dumps(options)})
    assert response.status_code == 201, response.text
    document = response.json()['document']
    assert document['valid_from'] == '2025-12-31T16:00:00.000000Z'
    assert any(chunk['metadata'].get('repeated_header') for chunk in response.json()['records'])
    candidates = document['metadata']['document_temporal']['fields']['valid_from']['candidates']
    assert len(candidates) == 1
    assert document['text'][candidates[0]['start_char']:candidates[0]['end_char']] == '2026年1月1日'
