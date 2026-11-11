from knowledge_service.services.evidence import _source_location


def test_assertion_source_location_clips_chunk_mapping_to_verified_quote(monkeypatch):
    """The entity inspector must not send every block of its model chunk to the reader."""
    from knowledge_service.services.evidence import resolve_assertion_evidence
    text = '# 规则\n\n第一段无关。\n\n通知适用《电子商务法》。\n\n末段无关。'
    quote = '《电子商务法》'
    start = text.index(quote)
    paragraph_start = text.index('通知')
    paragraph = '通知适用《电子商务法》。'
    assertion = dict(id='a', kind='entity', status='accepted', document_id='doc',
                     document_version_id='doc-v', chunk_id='chunk', quote=quote,
                     start_char=start, end_char=start + len(quote), payload={'text': quote})
    document = dict(id='doc', kind='document', version=2, version_id='doc-v', text=text,
                    metadata={'source_document_version_id': 'source-v'})
    chunk = dict(id='chunk', kind='chunk', version_id='chunk-v', source_id='doc', text=text,
                 metadata={'source_version_id': 'doc-v', 'start_char': 0, 'end_char': len(text)})
    spans = [dict(block_id='unrelated', chunk_start_char=6, chunk_end_char=12,
                  block_start_char=0, block_end_char=6, payload={}),
             dict(block_id='evidence', chunk_start_char=paragraph_start,
                  chunk_end_char=paragraph_start + len(paragraph), block_start_char=0,
                  block_end_char=len(paragraph), payload={})]

    class Repo:
        def get_assertion(self, *args): return assertion
        def get_record_version(self, *args): return document
        def history(self, *args): return [chunk]

    class Store:
        def __init__(self, repo): pass
        def locate(self, *args, **kwargs):
            return dict(document_version_id='source-v', status='available', spans=spans,
                        block_id='unrelated', blocks=[], tab='reading')

    monkeypatch.setattr('knowledge_service.repository.source_store.SourceStore', Store)
    result = resolve_assertion_evidence(Repo(), 'p', 'a')
    assert result['location']['mode'] == 'exact'
    mapped = result['source_location']
    assert len(mapped['spans']) == 1, 'only the verified evidence block should be highlighted'
    span = mapped['spans'][0]
    assert span['block_id'] == mapped['block_id'] == 'evidence'
    assert paragraph[span['block_start_char']:span['block_end_char']] == quote
    assert text[span['chunk_start_char']:span['chunk_end_char']] == quote


def test_legacy_evidence_never_uses_latest_source():
    class Repo:
        def get_record(self, *args):
            raise AssertionError('Must use frozen document')
    assert _source_location(Repo(), 'p', {'metadata': {}}, {}) == {
        'code': 'source_mapping_unavailable'}


def test_source_location_uses_frozen_version_and_chunk(monkeypatch):
    calls = []
    class Store:
        def __init__(self, repo):
            pass
        def locate(self, project_id, version_id, chunk_version_id=None, **kwargs):
            calls.append((project_id, version_id, chunk_version_id))
            return {'document_version_id': version_id, 'status': 'available', 'spans': []}
    monkeypatch.setattr('knowledge_service.repository.source_store.SourceStore', Store)
    result = _source_location(object(), 'p', {'metadata': {'source_document_version_id': 'frozen-v'}},
                              {'version_id': 'chunk-v'})
    assert calls == [('p', 'frozen-v', 'chunk-v')]
    assert result['document_version_id'] == 'frozen-v'


def test_exact_asr_quote_recomputes_video_time_from_only_intersecting_segments():
    from knowledge_service.services.evidence import _clip_source_location
    spans=[dict(asr_segment_id='first',chunk_start_char=0,chunk_end_char=4,block_start_char=0,block_end_char=4),
           dict(asr_segment_id='second',chunk_start_char=5,chunk_end_char=9,block_start_char=0,block_end_char=4)]
    segments=[dict(id='first',asset_id='asset',asr_run_id='run',start_ms=100,end_ms=400),
              dict(id='second',asset_id='asset',asr_run_id='run',start_ms=600,end_ms=900)]
    resolution=dict(location=dict(mode='exact',start_char=5,end_char=9),chunk={'start_char':0},
                    source_location=dict(status='available',spans=spans,segments=segments,asset_id='asset',
                        start_ms=100,end_ms=900,asr_segment_id='first',asr_run_id='run'))
    _clip_source_location(resolution)
    mapped=resolution['source_location']
    assert mapped['precision']=='evidence'
    assert mapped['segments']==[segments[1]]
    assert mapped['asr_segment_id']=='second' and mapped['asr_run_id']=='run'
    assert mapped['start_ms']==600 and mapped['end_ms']==900 and mapped['asset_id']=='asset'
