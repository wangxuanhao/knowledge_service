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



def test_exact_ocr_quote_in_mixed_chunk_focuses_only_its_image():
    from knowledge_service.services.evidence import _clip_source_location
    spans=[dict(block_id='body',chunk_start_char=0,chunk_end_char=4,block_start_char=0,block_end_char=4),
        dict(ocr_block_id='box',chunk_start_char=6,chunk_end_char=10,block_start_char=0,block_end_char=4),
        dict(asr_segment_id='speech',chunk_start_char=12,chunk_end_char=16,block_start_char=0,block_end_char=4)]
    box=dict(id='box',asset_id='image',ocr_run_id='ocr-run')
    segment=dict(id='speech',asset_id='video',asr_run_id='asr-run',start_ms=100,end_ms=900)
    resolution=dict(location=dict(mode='exact',start_char=6,end_char=10),chunk={'start_char':0},
        source_location=dict(status='available',spans=spans,boxes=[box],segments=[segment],asset_id='video',
            block_id='body',start_ms=100,end_ms=900,asr_run_id='asr-run'))
    _clip_source_location(resolution)
    focused=resolution['source_location']
    assert focused['asset_id']=='image'
    assert focused['boxes']==[box] and focused['segments']==[]
    assert focused['block_id'] is None and focused['start_ms'] is None



def test_mixed_video_location_keeps_independent_asset_time_groups():
    import json, threading
    from types import SimpleNamespace
    from knowledge_service.repository.source_store import SourceStore
    spans=[dict(block_id=None,ocr_block_id=None,asr_segment_id='s'+str(i),payload='{}',
        chunk_start_char=i*6,chunk_end_char=i*6+4,block_start_char=0,block_end_char=4) for i in range(2)]
    segments={str(i):dict(id='s'+str(i),asset_id='v'+str(i),asr_run_id='r'+str(i),
        start_ms=i*40000+100,end_ms=i*40000+900) for i in range(2)}
    class DB:
        def execute(self, query, params):
            if query.startswith('SELECT * FROM chunk_source_spans'):
                return SimpleNamespace(fetchall=lambda:spans)
            # 证据块按整批 ID 取回（批量语义与逐条查询等价）。
            ids = params[2:]
            by_id = {value['id']: value for value in segments.values()}
            return SimpleNamespace(fetchall=lambda: [by_id[i] for i in ids if i in by_id])
    store=SourceStore(SimpleNamespace(_lock=threading.RLock(),_db=DB()))
    store.get_version=lambda *args: {'blocks':[]}
    result=store.locate('p','v','chunk')
    assert result['asset_id'] is None and result['start_ms'] is None
    assert [(g['asset_id'],g['start_ms'],g['end_ms']) for g in result['media_locations']]==[
        ('v0',100,900),('v1',40100,40900)]
    from knowledge_service.services.evidence import _clip_source_location
    resolution=dict(location=dict(mode='exact',start_char=6,end_char=10),chunk={'start_char':0},source_location=result)
    _clip_source_location(resolution)
    focused=resolution['source_location']
    assert focused['asset_id']=='v1' and focused['start_ms']==40100
    assert len(focused['media_locations'])==1



def test_exact_quotes_choose_each_image_in_a_two_image_chunk():
    from knowledge_service.services.evidence import _clip_source_location
    spans=[dict(ocr_block_id='b'+str(i),chunk_start_char=i*6,chunk_end_char=i*6+4,
        block_start_char=0,block_end_char=4) for i in range(2)]
    boxes=[dict(id='b'+str(i),asset_id='image'+str(i),ocr_run_id='run'+str(i)) for i in range(2)]
    for index in range(2):
        resolution=dict(location=dict(mode='exact',start_char=index*6,end_char=index*6+4),chunk={'start_char':0},
            source_location=dict(status='available',spans=spans,boxes=boxes,asset_id='image1'))
        _clip_source_location(resolution)
        focused=resolution['source_location']
        assert focused['asset_id']=='image'+str(index) and focused['boxes']==[boxes[index]]
        assert len(focused['media_locations'])==1
