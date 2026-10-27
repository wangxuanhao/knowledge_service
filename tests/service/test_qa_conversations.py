"""Private server-side QA snapshots, exercised with real viewer authentication."""
import pytest
from fastapi.testclient import TestClient

from knowledge_service.api import create_app
from knowledge_service.integrations.embeddings import HashingEncoder


@pytest.fixture
def conversations_api(tmp_path):
    app = create_app(tmp_path / 'conversations', encoder=HashingEncoder(), auth_disabled=False)
    with TestClient(app) as client:
        admin = client.post('/api/auth/login', json={
            'username': 'admin', 'password': 'admin123'}).json()['access_token']
        admin_headers = {'Authorization': f'Bearer {admin}'}
        viewers = []
        for username in ['alice', 'bob']:
            response = client.post('/api/users', headers=admin_headers, json={
                'username': username, 'password': 'pass123', 'role': 'viewer'})
            assert response.status_code == 201, response.text
            token = client.post('/api/auth/login', json={
                'username': username, 'password': 'pass123'}).json()['access_token']
            viewers.append({'Authorization': f'Bearer {token}'})
        projects = [client.post('/api/projects', headers=admin_headers,
                               json={'name': name}).json()['id'] for name in ['one', 'two']]
        yield client, app, projects, viewers


def _base(project):
    return f'/api/projects/{project}/qa/conversations'


def _create(client, project, headers, **payload):
    response = client.post(_base(project), headers=headers, json=payload)
    assert response.status_code == 201, response.text
    return response.json()['conversation']


def _message(text='退款规则是什么？', kind='user', **extra):
    return {'kind': kind, 'text': text, 'scope': {'valid_at': '2026-10-26'}, **extra}


def _save(client, project, headers, conversation, messages):
    return client.put(f"{_base(project)}/{conversation['id']}", headers=headers,
                      json={'expected_version': conversation['version'], 'messages': messages})


def test_viewer_can_create_and_restore_in_a_second_browser(conversations_api):
    client, app, (project, _), (alice, _) = conversations_api
    created = _create(client, project, alice)
    assert created['title'] == '新会话'
    assert created['version'] == 1 and created['messages'] == []
    assert created['message_count'] == 0
    saved = _save(client, project, alice, created, [_message(), _message('退款说明 [E1]', 'assistant')])
    assert saved.status_code == 200, saved.text
    saved = saved.json()['conversation']
    assert saved['version'] == 2 and saved['message_count'] == 2
    assert saved['title'] == '退款规则是什么？'
    # A second browser shares the running application, not its lifespan owner.
    second = TestClient(app)
    try:
        headers = {'Authorization': 'Bearer ' + second.post('/api/auth/login', json={
            'username': 'alice', 'password': 'pass123'}).json()['access_token']}
        restored = second.get(f"{_base(project)}/{created['id']}", headers=headers)
        assert restored.status_code == 200, restored.text
        assert restored.json()['conversation'] == saved
        listing = second.get(_base(project), headers=headers).json()
        assert listing['total'] == 1 and listing['has_more'] is False
        assert listing['conversations'] == [{key: value for key, value in saved.items() if key != 'messages'}]
    finally:
        second.close()


def test_owner_and_project_boundaries_hide_every_detail_operation(conversations_api):
    client, _, (project, other), (alice, bob) = conversations_api
    created = _create(client, project, alice, owner_id='forged')
    assert client.get(_base(project), headers=bob).json()['conversations'] == []
    for headers, target in [(bob, project), (alice, other)]:
        path = f"{_base(target)}/{created['id']}"
        assert client.get(path, headers=headers).status_code == 404
        assert client.put(path, headers=headers, json={'expected_version': 1, 'messages': []}).status_code == 404
        assert client.patch(path, headers=headers, json={'expected_version': 1, 'title': 'stolen'}).status_code == 404
        assert client.delete(path, headers=headers, params={'expected_version': 1}).status_code == 404
    assert client.get(f"{_base(project)}/{created['id']}", headers=alice).status_code == 200


def test_optimistic_conflicts_preserve_the_latest_snapshot_and_name(conversations_api):
    client, _, (project, _), (alice, _) = conversations_api
    created = _create(client, project, alice)
    saved = _save(client, project, alice, created, [_message('first')]).json()['conversation']
    conflict = _save(client, project, alice, created, [_message('lost')])
    assert conflict.status_code == 409, conflict.text
    path = f"{_base(project)}/{created['id']}"
    assert client.patch(path, headers=alice, json={'expected_version': 1, 'title': 'lost'}).status_code == 409
    assert client.get(path, headers=alice).json()['conversation'] == saved


def test_titles_are_automatic_until_explicitly_named(conversations_api):
    client, _, (project, _), (alice, _) = conversations_api
    created = _create(client, project, alice)
    saved = _save(client, project, alice, created, [_message('  ' + '题' * 120 + '  ')])
    saved = saved.json()['conversation']
    assert saved['title'] == '题' * 80
    path = f"{_base(project)}/{created['id']}"
    renamed = client.patch(path, headers=alice, json={
        'expected_version': saved['version'], 'title': '  我的会话  '})
    assert renamed.status_code == 200, renamed.text
    renamed = renamed.json()['conversation']
    assert renamed['title'] == '我的会话'
    updated = _save(client, project, alice, renamed, [_message('changed first question')])
    assert updated.json()['conversation']['title'] == '我的会话'
    named = _create(client, project, alice, title='指定名称')
    assert _save(client, project, alice, named, [_message()]).json()['conversation']['title'] == '指定名称'


def test_list_pagination_search_and_updated_order(conversations_api):
    client, _, (project, _), (alice, _) = conversations_api
    first = _create(client, project, alice, title='退款甲')
    second = _create(client, project, alice, title='运费乙')
    third = _create(client, project, alice, title='退款丙')
    _save(client, project, alice, first, [_message()])
    listing = client.get(_base(project), headers=alice, params={'limit': 2}).json()
    assert [row['id'] for row in listing['conversations']] == [first['id'], third['id']]
    assert listing['total'] == 3 and listing['has_more'] is True
    tail = client.get(_base(project), headers=alice, params={'limit': 2, 'offset': 2}).json()
    assert [row['id'] for row in tail['conversations']] == [second['id']]
    assert tail['has_more'] is False
    found = client.get(_base(project), headers=alice, params={'q': '退款'}).json()
    assert found['total'] == 2
    assert client.get(_base(project), headers=alice, params={'q': '%'}).json()['total'] == 0


def test_deletion_preserves_other_conversations_documents_and_frozen_provenance(conversations_api):
    from test_provenance import _capture, _mapped, _source
    from knowledge_service.services.provenance import ProvenanceService
    client, app, (project, _), (alice, _) = conversations_api
    repo = app.state.service.repository
    document, chunk, _ = _source(repo, project)
    record, _ = _mapped(repo, project, sources=[(document, chunk, None)])
    provenance = ProvenanceService(repo)
    run, answer, evidence = _capture(provenance, project, [record])
    provenance.complete_answer(project, answer, run, 'fact [E1]', 'llm', evidence)
    trace_path = f'/api/projects/{project}/answers/{answer}/evidence/E1/provenance'
    trace = client.get(trace_path, headers=alice).json()
    created = _create(client, project, alice)
    other = _create(client, project, alice)
    saved = _save(client, project, alice, created, [_message('fact [E1]', 'assistant', answer_id=answer)])
    response = client.delete(f"{_base(project)}/{created['id']}", headers=alice,
                             params={'expected_version': saved.json()['conversation']['version']})
    assert response.status_code == 204 and response.content == b''
    assert client.get(f"{_base(project)}/{created['id']}", headers=alice).status_code == 404
    assert client.get(f"{_base(project)}/{other['id']}", headers=alice).status_code == 200
    assert repo.get_record(project, document['id'])['id'] == document['id']
    assert client.get(trace_path, headers=alice).json() == trace


def test_stale_delete_from_another_browser_cannot_erase_new_messages(conversations_api):
    client, app, (project, _), (alice, _) = conversations_api
    created = _create(client, project, alice)
    second = TestClient(app)
    try:
        updated = _save(second, project, alice, created, [_message('新消息')])
        assert updated.status_code == 200, updated.text
        path = f"{_base(project)}/{created['id']}"
        response = client.delete(path, headers=alice, params={'expected_version': created['version']})
        assert response.status_code == 409, response.text
        assert client.get(path, headers=alice).json()['conversation'] == updated.json()['conversation']
        assert client.delete(path, headers=alice).status_code == 422
        assert client.delete(path, headers=alice, params={'expected_version': 0}).status_code == 422
        deleted = client.delete(path, headers=alice,
            params={'expected_version': updated.json()['conversation']['version']})
        assert deleted.status_code == 204
    finally:
        second.close()


def test_migration_grants_custom_application_role_snapshot_dml():
    """Run only the new migration in a rollback-only test database schema."""
    from pathlib import Path
    from uuid import uuid4

    import pg_support
    import psycopg
    from psycopg import sql

    suffix = uuid4().hex
    role, schema = 'qa_test_' + suffix, 'qa_schema_' + suffix
    source = (Path(__file__).resolve().parents[2] / 'knowledge_service' / 'repository'
              / 'migrations' / '0028_qa_conversations.sql').read_text(encoding='utf-8')
    with psycopg.connect(pg_support.admin_dsn()) as connection:
        with connection.transaction(force_rollback=True):
            connection.execute(sql.SQL('CREATE ROLE {}').format(sql.Identifier(role)))
            connection.execute(sql.SQL('CREATE SCHEMA {}').format(sql.Identifier(schema)))
            connection.execute(sql.SQL('SET LOCAL search_path TO {}').format(sql.Identifier(schema)))
            connection.execute('CREATE TABLE projects (id text PRIMARY KEY)')
            connection.execute("SELECT set_config('knowledge_service.app_role', %s, true)", (role,))
            connection.execute(source)
            for privilege in ('SELECT', 'INSERT', 'UPDATE', 'DELETE'):
                allowed = connection.execute('SELECT has_table_privilege(%s,%s,%s)',
                    (role, schema + '.qa_conversations', privilege)).fetchone()[0]
                assert allowed, f'custom application role lacks {privilege}'


def test_snapshots_allow_only_display_fields_and_sanitize_evidence_recursively(conversations_api):
    client, _, (project, _), (alice, _) = conversations_api
    created = _create(client, project, alice)
    evidence = {'citation': 'E1', 'text_preview': 'x' * 300, 'provenance_ref': 'frozen:ref',
        'version_id': 'v1', 'valid_from': '2020', 'valid_until': None, 'answer_eligible': True,
        'token': 'secret', 'provider': {'headers': {'Authorization': 'secret'}},
        'source_eligibility': [{'document_version_id': 'doc-v1', 'valid_until': None, 'token': 'secret'}],
        'sources': [{'document_id': 'doc', 'quote': 'q' * 300, 'headers': {'token': 'secret'}}],
        'metadata': {'published_at': '2020', 'provider': 'secret', 'applicability': {
            'platforms': {'mode': 'specific', 'values': ['A'], 'headers': 'secret'}}}}
    messages = [_message(project_id='foreign', token='secret', scope={
        'valid_at': '2020', 'provider': {'token': 'secret'}, 'filters': {
            'field': 'kind', 'op': 'eq', 'value': 'document', 'headers': 'secret'}}),
        _message('answer', 'assistant', project_id='foreign', evidence=[evidence],
            historical_references=[evidence], future_references=[evidence], uncertain_references=[evidence],
            answer_id='answer', retrieval_run_id='run', answer_purpose='document_explanation',
            answer_label='文档说明', generation={'requested': True, 'called': False, 'provider': 'secret'},
            timings_ms={'total': 12, 'headers': 'secret'}, platform_groups=[{'platform': 'A', 'provider': 'secret'}],
            error={'message': 'failed', 'headers': 'secret'}, provider={'headers': 'secret'})]
    response = _save(client, project, alice, created, messages)
    assert response.status_code == 200, response.text
    stored = response.json()['conversation']['messages']
    assert all(message['project_id'] == project for message in stored)
    assert 'secret' not in response.text and 'provider' not in response.text and 'headers' not in response.text
    summary = stored[1]['evidence'][0]
    assert summary['text_preview'] == 'x' * 240 and summary['sources'][0]['quote'] == 'q' * 240
    assert summary['source_eligibility'] == [{'document_version_id': 'doc-v1', 'valid_until': None}]
    assert summary['provenance_ref'] == 'frozen:ref'
    assert stored[1]['answer_label'] == '文档说明'


def test_snapshot_rejects_invalid_shapes_and_limits_without_truncating_text(conversations_api):
    client, _, (project, _), (alice, _) = conversations_api
    created = _create(client, project, alice)
    for messages in [[_message(kind='system')], [_message(text=1)], [_message(scope=[])],
                     [_message()] * 1001, [_message(text='a' * (8 * 1024 * 1024))]]:
        response = _save(client, project, alice, created, messages)
        assert response.status_code == 422, response.text[:200]
    text = '长文本' * 20000
    response = _save(client, project, alice, created, [_message(text=text)])
    assert response.status_code == 200, response.text[:200]
    assert response.json()['conversation']['messages'][0]['text'] == text


def test_public_ontology_expansion_ledger_survives_snapshot_without_provider_details(conversations_api):
    client, _, (project, _), (alice, _) = conversations_api
    created = _create(client, project, alice)
    ledger = {'enabled': True, 'matched': True, 'has_hierarchy': True,
        'terms': ['urn:Parent'], 'subclasses': ['urn:Child'],
        'matched_terms': ['父类'], 'expanded_subclasses': ['子类'],
        'labels': ['父类', '子类'], 'type_iris': ['urn:Parent', 'urn:Child'],
        'keyword_terms': ['规则', '父类', '子类'], 'type_hits': 3,
        'strategy': 'ontology_type_channel_and_expanded_query'}
    response = _save(client, project, alice, created, [_message('answer', 'assistant',
        ontology_expansion={**ledger, 'provider': {'token': 'secret'}, 'headers': 'secret'})])
    assert response.status_code == 200, response.text
    stored = response.json()['conversation']['messages'][0]
    assert stored.get('ontology_expansion') == ledger
    assert 'secret' not in response.text


def test_parameters_require_valid_versions_titles_and_pagination(conversations_api):
    client, _, (project, _), (alice, _) = conversations_api
    created = _create(client, project, alice)
    for params in [{'limit': 0}, {'limit': 101}, {'offset': -1}]:
        assert client.get(_base(project), headers=alice, params=params).status_code == 422
    for title in ['', '   ', '字' * 81, '\x00']:
        assert client.post(_base(project), headers=alice, json={'title': title}).status_code == 422
        assert client.patch(f"{_base(project)}/{created['id']}", headers=alice,
                            json={'expected_version': 1, 'title': title}).status_code == 422
    for version in [0, True, '1', 1.5]:
        assert client.put(f"{_base(project)}/{created['id']}", headers=alice,
                          json={'expected_version': version, 'messages': []}).status_code == 422


def test_finite_json_integer_display_metadata_does_not_overflow_validation(conversations_api):
    client, _, (project, _), (alice, _) = conversations_api
    created = _create(client, project, alice)
    large_number = 10 ** 500
    response = _save(client, project, alice, created, [_message('answer', 'assistant',
        timings_ms={'total': large_number})])
    assert response.status_code == 200, response.text
    assert response.json()['conversation']['messages'][0]['timings_ms']['total'] == large_number


def test_private_writes_still_require_login_and_viewer_knowledge_writes_are_forbidden(conversations_api):
    client, _, (project, _), (alice, _) = conversations_api
    for method, suffix, payload in [('get', '', None), ('post', '', {}),
            ('get', '/unknown', None), ('put', '/unknown', {'expected_version': 1, 'messages': []}),
            ('patch', '/unknown', {'expected_version': 1, 'title': 'x'}), ('delete', '/unknown', None)]:
        kwargs = {'json': payload} if payload is not None else {}
        assert getattr(client, method)(_base(project) + suffix, **kwargs).status_code == 401
    for method, path in [('post', f'/api/projects/{project}/records'),
                         ('put', f'/api/projects/{project}/records/x'),
                         ('patch', f'/api/projects/{project}'),
                         ('delete', f'/api/projects/{project}'),
                         ('post', _base(project) + '/x/extra')]:
        assert client.request(method, path, headers=alice, json={}).status_code == 403


def test_auth_disabled_mode_uses_a_stable_server_identity(tmp_path):
    app = create_app(tmp_path / 'disabled', encoder=HashingEncoder(), auth_disabled=True)
    with TestClient(app) as client:
        project = client.post('/api/projects', json={'name': 'test'}).json()['id']
        created = _create(client, project, {})
        assert client.get(f"{_base(project)}/{created['id']}").json()['conversation'] == created


def test_json_control_characters_in_question_text_round_trip_without_loss(tmp_path):
    app = create_app(tmp_path / 'json-text', encoder=HashingEncoder(), auth_disabled=True)
    with TestClient(app) as client:
        project = client.post('/api/projects', json={'name': 'text'}).json()['id']
        created = _create(client, project, {})
        text = '原文\x00保留\n换行\t制表符'
        response = _save(client, project, {}, created, [_message(text)])
        assert response.status_code == 200, response.text
        assert response.json()['conversation']['messages'][0]['text'] == text
        restored = client.get(f"{_base(project)}/{created['id']}").json()['conversation']
        assert restored['messages'][0]['text'] == text
        assert client.get(_base(project)).json()['conversations'][0]['message_count'] == 1
