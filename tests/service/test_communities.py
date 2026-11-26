import json
from threading import Event
import time

from fastapi.testclient import TestClient

from knowledge_service.api import create_app
from knowledge_service.integrations.embeddings import HashingEncoder


def put_entity(repo, project, key, *, platform=None, entity_type=None):
    metadata = ({'applicability': {'platforms': {'mode': 'specific', 'values': [platform]}}}
                if platform else {})
    record = {'id': key, 'kind': 'entity', 'text': key, 'metadata': metadata}
    if entity_type is not None:
        record['type'] = entity_type
    return repo.put_record(project, record)


def put_relation(repo, project, key, subject, target, *, platform=None):
    metadata = ({'applicability': {'platforms': {'mode': 'specific', 'values': [platform]}}}
                if platform else {})
    return repo.put_record(project, {'id': key, 'kind': 'relation', 'type': 'related',
                                    'text': key, 'subject_id': subject, 'object_id': target,
                                    'metadata': metadata})


def wait_status(client, url, scope, predicate):
    deadline = time.monotonic() + 12
    while time.monotonic() < deadline:
        result = client.post(url + '/status', json={'scope': scope})
        assert result.status_code == 200, result.text
        status = result.json()
        if predicate(status):
            return status
        time.sleep(.05)
    raise AssertionError(status)


def test_partition_is_deterministic_and_excludes_isolates():
    from knowledge_service.services.community_partition import partition

    nodes = ['a', 'b', 'c', 'd', 'e', 'f', 'isolated']
    edges = [('a', 'b'), ('b', 'c'), ('a', 'c'), ('d', 'e'), ('d', 'f'),
             ('e', 'f'), ('c', 'd')]
    result = partition(nodes, edges)
    assert result == partition(reversed(nodes), reversed(edges))
    leaves = [set(members) for level, members in result
              if not any(set(other) < set(members) for _, other in result)]
    assert set(map(frozenset, leaves)) == {
        frozenset({'a', 'b', 'c'}), frozenset({'d', 'e', 'f'})}


def test_community_spans_documents_connected_by_an_accepted_relation(tmp_path, monkeypatch):
    from knowledge_service.services import community_reports

    def report_with_one_source(_title, content, _config):
        first = next(item for item in content['evidence'] if item['kind'] == 'document')
        return {'status': 'ready', 'title': '共同主题', 'summary': '跨文档社区',
                'findings': [{'text': '已核实的发现', 'citations': [first['id']]}]}

    monkeypatch.setattr(community_reports, 'build_report', report_with_one_source)
    app = create_app(tmp_path / 'cross-source-community', encoder=HashingEncoder())
    repo = app.state.service.repository
    project = repo.create_project('cross-source')['id']
    doc_ids = set()
    for suffix in ('a', 'b'):
        entity = put_entity(repo, project, f'entity-{suffix}')
        doc = repo.put_record(project, {'id': f'doc-{suffix}', 'kind': 'document', 'text': '不同文档的依据'})
        doc_ids.add(doc['id'])
        chunk = repo.put_record(project, {'id': f'chunk-{suffix}', 'kind': 'chunk', 'text': doc['text'],
             'source_id': doc['id'], 'metadata': {'source_version_id': doc['version_id'],
                                                 'start_char': 0, 'end_char': len(doc['text'])}})
        candidate = repo.create_assertion(project, {
            'id': f'candidate-{suffix}', 'kind': 'entity', 'payload': {}, 'actor': 'reviewer',
            'document_id': doc['id'], 'document_version_id': doc['version_id'],
            'chunk_id': chunk['id'], 'quote': doc['text'], 'start_char': 0, 'end_char': len(doc['text'])})
        repo.transition_assertion(project, candidate['id'], 1, 'accepted',
                                  'reviewed', 'reviewer', canonical_record_id=entity['id'])
        event = repo.list_assertion_events(project, candidate['id'])[-1]
        repo.add_record_version_assertion(project, entity['id'], entity['version_id'],
                                          candidate['id'], event['id'])
    put_relation(repo, project, 'linked', 'entity-a', 'entity-b')

    url = f'/api/projects/{project}/communities'
    with TestClient(app) as client:
        assert client.post(url + '/build', json={}).status_code == 200
        wait_status(client, url, {'kind': 'project'}, lambda status: status['state'] == 'ready')
        listing = client.post(url, json={}).json()
        assert listing['total'] == 1
        detail = client.post(url + '/detail', json={'community_id': listing['communities'][0]['id']}).json()
        assert {row['id'] for row in detail['members']} == {'entity-a', 'entity-b'}
        assert {item['document_id'] for item in detail['evidence'] if item['kind'] == 'document'} == doc_ids
        assert detail['source_document_count'] == 2
        assert detail['cited_document_count'] == 1


def test_community_routes_limit_scope_and_refresh_only_for_qualified_changes(tmp_path, monkeypatch):
    from knowledge_service.services import community_reports

    calls = []

    def model(messages, _config):
        calls.append(messages)
        raise RuntimeError('测试模型暂不可用')

    monkeypatch.setattr(community_reports, 'complete', model)
    app = create_app(tmp_path / 'community', encoder=HashingEncoder())
    repo = app.state.service.repository
    project = repo.create_project('project')['id']
    scope = {'kind': 'platform', 'platform': '美团'}
    put_entity(repo, project, 'meituan', platform='美团', entity_type='internal:违规行为')
    put_entity(repo, project, 'meituan-peer', platform='美团')
    put_relation(repo, project, 'paired', 'meituan', 'meituan-peer', platform='美团')
    put_entity(repo, project, 'meituan-orphan', platform='美团')
    put_entity(repo, project, 'taobao', platform='淘宝')
    put_entity(repo, project, 'platform-unknown')
    repo.create_assertion(project, {'id': 'pending-entity', 'kind': 'entity',
                                    'payload': {'text': 'candidate'}})
    url = f'/api/projects/{project}/communities'
    with TestClient(app) as client:
        assert client.post(url, json={'scope': scope}).json()['status']['state'] == 'disabled'
        assert client.post(url, json={'scope': {'kind': 'project', 'valid_at': '2026-01-01'}}).status_code == 422
        initial = client.post(url + '/build', json={'scope': scope})
        assert initial.status_code == 200, initial.text
        first = wait_status(client, url, scope, lambda status: status['state'] == 'partial')
        assert first['counts']['entities'] == 3
        assert first['counts']['covered_entities'] == 2
        assert first['counts']['isolated_entities'] == 1
        listing = client.post(url, json={'scope': scope, 'anchor': 'meituan'}).json()
        assert listing['total'] == 1
        assert 'keyword_match' not in listing['communities'][0]
        assert client.post(url, json={'scope': scope, 'keyword': 'taobao'}).json()['total'] == 0
        assert client.post(url, json={'scope': scope, 'keyword': '违规'}).json()['total'] == 0
        matched = client.post(url, json={'scope': scope, 'keyword': 'PAIRED'}).json()
        assert matched['total'] == 1
        assert matched['communities'][0]['keyword_match'] == {
            'field': 'relationship', 'before': '', 'term': 'paired', 'after': '', 'source_title': None,
        }
        community = listing['communities'][0]
        detail = client.post(url + '/detail', json={'scope': scope, 'community_id': community['id']}).json()
        assert [item['id'] for item in detail['members']] == ['meituan', 'meituan-peer']
        assert client.post(url, json={'scope': scope, 'anchor': 'meituan-orphan'}).json()['total'] == 0
        assert detail['report_status'] == 'failed'
        assert len(calls) == 1

        request = {'scope': scope, 'question': 'meituan', 'community_ids': [community['id']]}
        answer = client.post(url + '/ask', json=request)
        assert answer.status_code == 200, answer.text
        assert not answer.json()['generated']
        assert answer.json()['evidence'][0]['record_id'] == 'meituan'
        assert 'keyword_match' not in answer.json()['communities'][0]
        assert len(calls) == 1
        keyword_answer = client.post(url + '/ask', json={
            'scope': scope, 'question': 'Does PAIRED appear here?', 'match_only': True,
        }).json()
        assert keyword_answer['coverage']['retrieval'] == 'keyword'
        assert keyword_answer['communities'][0]['keyword_match'] == matched['communities'][0]['keyword_match']
        follow_up = {'scope': scope, 'question': '那第二次呢？', 'match_only': True}
        assert client.post(url + '/ask', json=follow_up).json()['communities'] == []
        followed = client.post(url + '/ask', json={**follow_up, 'conversation_context': [
            {'question': 'PAIRED 首次违规怎么处置？'}]}).json()
        assert followed['communities'][0]['id'] == community['id']

        put_entity(repo, project, 'taobao2', platform='淘宝')
        app.state.community_refresh.refresh()
        same = client.post(url + '/status', json={'scope': scope}).json()
        assert same['revision'] == first['revision']
        assert len(calls) == 1

        repo.put_record(project, {'id': 'platform-universal', 'kind': 'entity', 'text': '通用实体',
                                  'metadata': {'applicability': {'platforms': {'mode': 'all', 'values': []}}}})
        put_relation(repo, project, 'shared-relation', 'meituan', 'platform-universal', platform='美团')
        put_relation(repo, project, 'out-of-scope-relation', 'meituan', 'taobao', platform='美团')
        latest = wait_status(client, url, scope, lambda status:
                             status['state'] == 'partial' and status['revision'] != first['revision'])
        assert latest['counts']['entities'] == 4
        assert latest['counts']['covered_entities'] == 3
        assert latest['counts']['isolated_entities'] == 1
        assert latest['counts']['reports_failed'] == 1
        assert latest['counts']['relationships'] == 2
        listing = client.post(url, json={'scope': scope, 'anchor': 'platform-universal'}).json()
        assert listing['total'] == 1
        joined = client.post(url + '/detail', json={'scope': scope,
                             'community_id': listing['communities'][0]['id']}).json()
        assert [row['id'] for row in joined['relationships']] == ['paired', 'shared-relation']
        mismatch = {'scope': scope, 'expected_revision': first['revision']}
        assert client.post(url + '/detail', json={
            **mismatch, 'community_id': listing['communities'][0]['id']}).status_code == 409
        assert client.post(url + '/ask', json={**mismatch, 'question': 'meituan'}).status_code == 409


def test_community_read_post_permissions():
    from knowledge_service.api.security_middleware import requires_write_access

    url = '/api/projects/project/communities'
    for endpoint in ('', '/status', '/detail', '/ask'):
        assert not requires_write_access('POST', url + endpoint)
    assert requires_write_access('POST', url + '/build')


def test_community_reports_retry_in_place_and_cite_frozen_sources(tmp_path, monkeypatch):
    from knowledge_service.services import community_reports

    entered, release = Event(), Event()
    calls = []

    def model(messages, _config):
        payload = json.loads(messages[1]['content'])
        source = payload['evidence'][0]
        calls.append(payload)
        if len(calls) == 1:
            entered.set()
            assert release.wait(10)
            raise RuntimeError('模型连接失败')
        if 'mapped_findings' in payload:
            return {'answer': '社区综合', 'citations': [source['id']]}
        if '"score"' in messages[0]['content']:
            return {'findings': [{'text': '本批社区的依据', 'citations': [source['id']], 'score': 80}]}
        if 'question' in payload:
            assert payload['reports'][0]['findings'][0]['citations'] == [source['id']]
            return {'answer': '复核回答', 'citations': [source['id']]}
        return {'title': '社区报告', 'summary': '已核验的依据',
                'findings': [{'text': '来源可定位', 'citations': [source['id']]}]}

    monkeypatch.setattr(community_reports, 'complete', model)
    app = create_app(tmp_path / 'community-retry', encoder=HashingEncoder())
    repo = app.state.service.repository
    encoder = HashingEncoder()

    class TestVectors:
        def __init__(self):
            self.rows = {}
            self.fail_next = False

        def upsert(self, project, scope_key, revision, model, rows):
            for row in rows:
                self.rows[project, scope_key, revision, model, row['id']] = row['fingerprint']
            if self.fail_next:
                self.fail_next = False
                raise RuntimeError('模拟批次写入后失败')

        def search(self, project, scope_key, revision, model, query_vector, limit, *, expected):
            assert len(query_vector) and limit == 8
            return [{'id': ident, 'fingerprint': fingerprint, 'score': .9}
                    for ident, fingerprint in expected.items()
                    if self.rows.get((project, scope_key, revision, model, ident)) == fingerprint]

        def discard(self, project, scope_key, revision, model):
            self.rows = {key: value for key, value in self.rows.items()
                         if key[:4] != (project, scope_key, revision, model)}

    vectors = TestVectors()
    monkeypatch.setattr(app.state.communities, '_vectors', lambda: (encoder, vectors))
    project = repo.create_project('project')['id']
    entity = put_entity(repo, project, 'reviewed-entity')
    put_entity(repo, project, 'related-entity')
    put_relation(repo, project, 'reviewed-relation', 'reviewed-entity', 'related-entity')
    applicability = {'platforms': {'mode': 'specific', 'values': ['抖音电商']}}
    doc = repo.put_record(project, {'id': 'source', 'kind': 'document', 'text': '复核规则依据',
                                    'metadata': {'applicability': applicability}})
    chunk = repo.put_record(project, {'id': 'source-chunk', 'kind': 'chunk', 'text': doc['text'],
        'source_id': doc['id'], 'metadata': {'source_version_id': doc['version_id'], 'applicability': applicability,
                                             'start_char': 0, 'end_char': len(doc['text'])}})
    candidate = repo.create_assertion(project, {
        'id': 'candidate', 'kind': 'entity', 'payload': {}, 'actor': 'reviewer',
        'document_id': doc['id'], 'document_version_id': doc['version_id'],
        'chunk_id': chunk['id'], 'quote': '规则', 'start_char': 2, 'end_char': 4})
    repo.transition_assertion(project, candidate['id'], 1, 'accepted',
                              'reviewed', 'reviewer', canonical_record_id=entity['id'])
    event = repo.list_assertion_events(project, candidate['id'])[-1]
    repo.add_record_version_assertion(project, entity['id'], entity['version_id'],
                                     candidate['id'], event['id'])
    url = f'/api/projects/{project}/communities'
    scope = {'kind': 'project'}
    with TestClient(app) as client:
        queued = client.post(url + '/build', json={}).json()['job']['id']
        try:
            assert entered.wait(8)
            same = client.post(url + '/build', json={}).json()['job']['id']
            assert same == queued
            listing = client.post(url, json={}).json()
            community = listing['communities'][0]
            assert community['report_status'] == 'pending'
            assert listing['status']['counts']['covered_entities'] == 2
        finally:
            release.set()
        wait_status(client, url, scope, lambda state: state['state'] == 'partial')
        retry = client.post(url + '/build', json={'retry_failed': True}).json()['job']['id']
        assert retry != queued
        state = wait_status(client, url, scope, lambda state: state['state'] == 'ready')
        assert state['revision'] == listing['status']['revision']
        assert client.post(url + '/ask', json={'mode': 'semantic', 'question': '有哪些主题？'}).status_code == 409
        browse_query = {'mode': 'semantic', 'keyword': '没有字面匹配的描述'}
        assert client.post(url, json=browse_query).status_code == 409
        assert client.post(url + '/build', json={'vectorize': True}).status_code == 200
        vector_status = wait_status(client, url, scope, lambda state: state['vector_index']['state'] == 'ready')
        assert vector_status['vector_index']['indexed'] == 1
        assert vector_status['revision'] == state['revision']
        recalled = client.post(url, json=browse_query)
        assert recalled.status_code == 200, recalled.text
        assert recalled.json()['total'] == 1
        assert recalled.json()['communities'][0]['id'] == community['id']
        assert recalled.json()['communities'][0]['semantic_score'] == .9
        assert client.post(url, json={**browse_query, 'anchor': 'absent'}).json()['total'] == 0
        semantic = client.post(url + '/ask', json={'mode': 'semantic', 'question': '有哪些主题？'}).json()
        assert semantic['communities'][0]['semantic_score'] == .9
        assert semantic['coverage']['reports_with_eligible_evidence'] == 1
        global_answer = client.post(url + '/ask', json={'mode': 'global', 'level': 0,
                                                        'question': '有哪些主题？', 'generate': True}).json()
        assert global_answer['generated'], global_answer
        assert global_answer['answer'] == '社区综合'
        assert global_answer['coverage']['reports_mapped'] == global_answer['coverage']['target_communities'] == 1
        assert global_answer['coverage']['complete']
        rule = client.post(url + '/ask', json={'mode': 'global', 'level': 0,
                                               'question': '现在适用什么规则？', 'generate': True}).json()
        assert not rule['generated']
        assert rule['coverage']['reports_with_eligible_evidence'] == 0
        assert not rule['evidence']
        def stream_model(messages, config, emit):
            emit('delta', {'text': '逐段回答'})
            evidence = json.loads(messages[1]['content'])['evidence']
            return {'answer': '逐段回答', 'citations': [evidence[0]['id']]}

        def stream_events(question, **options):
            streamed = client.post(url + '/ask', headers={'Accept': 'text/event-stream'},
                                   json={'question': question, 'generate': True, **options})
            return [(name.removeprefix('event: '), json.loads(data.removeprefix('data: ')))
                    for block in streamed.text.strip().split('\n\n') for name, data in [block.splitlines()]]

        with monkeypatch.context() as patch:
            patch.setattr(community_reports, '_stream_completion', stream_model)
            for options in ({'community_ids': [community['id']]}, {'mode': 'semantic'}, {'mode': 'global', 'level': 0}):
                events = stream_events('有哪些主题？', **options)
                assert events[0] == ('progress', {'stage': 'retrieving'})
                assert events[1][0] == 'evidence', events
                assert global_answer['citations'][0] in {item['id'] for item in events[1][1]['evidence']}
                assert ('delta', {'text': '逐段回答'}) in events[2:-1]
                assert events[-1][0] == 'done' and events[-1][1]['citations'] == global_answer['citations']
            insufficient = stream_events('现在适用什么规则？', mode='global', level=0)
            assert [name for name, _ in insufficient] == ['progress', 'done']
            assert not insufficient[-1][1]['evidence']

        original_searches = []
        search_service = app.state.communities.search_service
        real_search = search_service.search

        def raw_search(_project, request):
            original_searches.append(request)
            return real_search(_project, request)

        with monkeypatch.context() as patch:
            patch.setattr(search_service, 'search', raw_search)
            patch.setattr(community_reports, 'complete', lambda *_: {'questions': [
                {'query': '复核规则依据适用于哪个平台？'}, {'query': '{platform} 有哪些其他限制？', 'platform_from': 1},
            ]})
            patch.setattr(community_reports, '_stream_completion', stream_model)
            events = stream_events('有哪些主题？', mode='hybrid', plan=True)
        assert ('progress', {'stage': 'planning'}) in events
        assert [data['subquestion']['number'] for name, data in events if name == 'evidence'] == [1, 2]
        assert events[-1][0] == 'done' and events[-1][1]['generated']
        planned = events[-1][1]
        assert planned['answer_purpose'] == 'document_explanation'
        assert planned['subquestions'][1]['platform'] == '抖音电商'
        assert planned['subquestions'][0]['channels'] == ['keyword', 'semantic', 'original_keyword']
        assert planned['evidence'][0]['source_platforms'] == ['抖音电商']
        assert original_searches[-1]['applicability'] == applicability

        repo.put_record(project, {'id': doc['id'], 'kind': 'document', 'text': '修订后的文本'},
                        expected_version=1)
        detail = client.post(url + '/detail', json={'community_id': community['id']}).json()
        assert detail['source_document_count'] == detail['cited_document_count'] == 1
        page = client.post(url + '/detail', json={'community_id': community['id'], 'offset': 1, 'limit': 1}).json()
        assert page['source_document_count'] == page['cited_document_count'] == 1
        source = detail['evidence'][0]
        assert source['kind'] == 'document'
        assert source['quote'] == '规则'
        assert source['document_version_id'] == doc['version_id']
        assert source['chunk_version_id'] == chunk['version_id']
        assert source['assertion_event_id'] == event['id']
        assert detail['report']['findings'][0]['citations'] == [source['id']]
        answer = client.post(url + '/ask', json={
            'question': '规则', 'community_ids': [community['id']], 'generate': True})
        assert answer.status_code == 200, answer.text
        assert answer.json()['generated']
        assert answer.json()['citations'] == [source['id']]

        broad = client.post(url + '/ask', json={'question': '项目有哪些主要主题？', 'generate': True}).json()
        assert broad['generated']
        assert broad['coverage']['retrieval'] == 'representative'
        assert broad['coverage']['matched_communities'] == 0
        assert broad['coverage']['included_communities'] == 1
        assert 'keyword_match' not in broad['communities'][0]
        matched_only = client.post(url + '/ask', json={
            'question': '项目有哪些主要主题？', 'generate': True, 'match_only': True,
        })
        assert matched_only.status_code == 200, matched_only.text
        empty = matched_only.json()
        assert empty['communities'] == empty['evidence'] == []
        assert not empty['generated'] and empty['generation_error'] is None

        vectors.fail_next = True
        put_entity(repo, project, 'newly-isolated')
        same_group = wait_status(client, url, scope, lambda current:
                                 current['state'] == 'ready' and current['revision'] != state['revision'])
        assert same_group['counts']['isolated_entities'] == 1
        assert same_group['vector_index']['state'] == 'failed'
        assert {key[2] for key in vectors.rows} == {state['revision'], same_group['revision']}
        assert client.post(url + '/build', json={'vectorize': True}).status_code == 200
        wait_status(client, url, scope, lambda current:
                    current['state'] == 'ready' and current['vector_index']['state'] == 'ready')
        assert {key[2] for key in vectors.rows} == {same_group['revision']}
        assert client.post(url, json={}).json()['communities'][0]['id'] == community['id']
        assert client.post(url + '/detail', json={
            'community_id': community['id'], 'expected_revision': state['revision']}).status_code == 409
        assert client.post(url + '/ask', json={
            'community_ids': [community['id']], 'question': '规则',
            'expected_revision': state['revision']}).status_code == 409

        app.state.community_refresh.close()

        def withdraw_during_generation(_messages, _config):
            repo.transition_assertion(project, candidate['id'], 2, 'superseded', '支持已撤回', 'reviewer')
            return {'answer': '这段旧答案不应返回', 'citations': [source['id']]}

        monkeypatch.setattr(community_reports, 'complete', withdraw_during_generation)
        raced = client.post(url + '/ask', json={
            'question': '规则', 'community_ids': [community['id']], 'generate': True}).json()
        assert not raced['generated']
        assert not raced['evidence']
        assert '这段旧答案' not in raced['answer']
        assert not raced['status']['browseable']
        assert client.post(url, json={}).json()['communities'] == []
        assert client.post(url + '/detail', json={'community_id': community['id']}).status_code == 409
