from ontology_seed import seed_ontology
import pytest
from fastapi.testclient import TestClient
from knowledge_service.api import create_app
from knowledge_service.integrations.embeddings import HashingEncoder
from knowledge_service.services.governance import writable
from knowledge_service.services.ontology import Ontology
from test_retrieval import activate_index

TTL = '''@prefix : <https://test/> .
@prefix owl: <http://www.w3.org/2002/07/owl#> .
:Person a owl:Class . :knows a owl:ObjectProperty .'''


@pytest.fixture
def review(tmp_path, monkeypatch):
    from knowledge_service.integrations.semantica_adapter import SemanticaExtractor
    def extract(self, text, ontology):
        self.review_candidates = [dict(predicate='isSubordinateTo', subject_id='a', object_id='b',
            subject='甲', object='乙', confidence=.8, reason='未知关系')]
        return [dict(id=k, kind='entity', type='https://test/Person', text=v) for k,v in [('a','甲'),('b','乙')]]
    monkeypatch.setattr(SemanticaExtractor, 'extract', extract)
    app=create_app(tmp_path/'reviews.sqlite',HashingEncoder())
    with TestClient(app) as client:
        service=app.state.service
        p=client.post('/api/projects',json={'name':'review','use_default_ontology':False}).json()['id']
        seed_ontology(service.repository, p,TTL,Ontology(TTL).summary())
        base='/api/projects/'+p
        result=client.post(base+'/documents',json={'title':'原文','text':'甲属于乙', 'resolve_entities':False,
            'valid_from':'2025-01-01T00:00:00Z','valid_until':'2027-01-01T00:00:00Z'})
        assert result.status_code==201,result.text
        candidate=client.get(base+'/reviews').json()['reviews'][0]
        yield client,service,p,base,candidate


def decide(review, **options):
    client,_,_,base,c=review
    return client.post(base+'/reviews/'+c['document_id']+'/'+c['id'],json={
        'action':'approve','target_type':'knows','note':'确认方向与含义','expected_version':c['document_version'],**options})


def projection_events(repo, project, after_seq=0):
    from knowledge_service.repository.projection_store import _decode_row

    rows = repo._db.execute('SELECT seq AS outbox_seq,operation,version_id,payload FROM projection_outbox '
        'WHERE project_id=? AND seq>? ORDER BY seq', (project, after_seq)).fetchall()
    raw_sequences = [int(row['outbox_seq']) for row in rows]
    assert raw_sequences == list(range(after_seq + 1, after_seq + 1 + len(rows)))
    events = [_decode_row(row) for row in rows]
    assert [event['seq'] for event in events] == raw_sequences
    return events


def install_failing_projection(service, monkeypatch):
    """Exercise the real leased delivery path; replace only external I/O."""
    from knowledge_service.services.projection_runtime import MilvusProjectionAdapter
    from knowledge_service.services.projection_sync import ProjectionSync

    attempts, writes, drains = [], [], []

    class Store:
        dim = service.encoder.dimension

        def check_collection(self, **kwargs):
            return True

        def upsert_rows(self, *args, **kwargs):
            writes.append(('upsert', args, kwargs))
            raise AssertionError('failed encoding must not reach external writes')

        def delete_records(self, record_ids, *, project_id, index_version, catalog):
            writes.append(('delete', list(record_ids), project_id, index_version, catalog.to_dict()))
            return True

    def fail(texts):
        assert not service.repository._db.in_transaction, 'embedding must run after authority commit'
        attempts.append(list(texts))
        raise RuntimeError('test embedding failure')

    monkeypatch.setattr(service.encoder, 'encode', fail)
    adapter = MilvusProjectionAdapter(Store(), encoder=service.encoder)
    sync = ProjectionSync(service.repository.projections, {'milvus': adapter}, schema_version=3)

    def drain(project):
        result = sync.drain_once(project, 'milvus')
        drains.append(result)
        return int(result)

    monkeypatch.setattr(service, 'projection_drain', drain)
    return attempts, writes, drains


def assert_failed_projection_prefix(repo, project, index, attempts, writes, drains):
    """Earlier document tombstones may deliver; no upsert or ACK may succeed."""
    queued=projection_events(repo,project,index['watermark'])
    assert all(event['payload']['_milvus_projection']['index_version']==index['index_version']
               and event['payload']['_milvus_projection']['embedding_model']==index['embedding_model']
               for event in queued)
    first=next(event for event in queued if event['operation']=='upsert'
               and event['payload']['_milvus_projection'].get('row') is not None)
    body=first['payload']['_milvus_projection']['row']
    assert body['version_id']==first['version_id']
    assert body['version_id'] in {row['version_id'] for row in repo.history(project,body['record_id'])}
    assert attempts==[[body['text']]]
    prefix=[event for event in queued if event['seq']<first['seq'] and event['operation']=='tombstone']
    document_ids={row['id'] for row in repo.current_records(project) if row['kind']=='document'}
    assert all(event['payload']['_milvus_projection']['record_id'] in document_ids for event in prefix)
    expected=[('delete', [event['payload']['_milvus_projection']['record_id']], project,
               index['index_version'], index['catalog']) for event in prefix]
    assert writes==expected
    assert len(drains)==1 and drains[0].status=='requires_rebuild' and int(drains[0])==0
    cursor=repo.projections.cursor(project,'milvus')
    assert cursor['status']=='requires_rebuild' and cursor['last_error']=='test embedding failure'
    assert cursor['claim_token'] is None
    assert cursor['applied_seq']==index['watermark']<repo.projections.project_watermark(project)
    assert repo._db.execute('SELECT COUNT(*) AS n FROM milvus_projection_receipts '
        'WHERE project_id=? AND index_version=? AND version_id=?',
        (project,index['index_version'],body['version_id'])).fetchone()['n']==0
    with pytest.raises(RuntimeError,match='requires_rebuild'):
        repo.projections.search_index(project,3)
    return queued,first


def test_pending_then_approve_is_atomic_and_preserves_graph(review):
    client,service,p,base,c=review
    before=service.repository.current_records(p)
    assert not [r for r in before if r['kind']=='relation']
    assert c['status']=='pending' and c['evidence']=='甲属于乙'
    response=decide(review)
    assert response.status_code==200,response.text
    after=service.repository.current_records(p)
    assert [r for r in before if r['kind']=='entity']==[r for r in after if r['kind']=='entity']
    relation=next(r for r in after if r['kind']=='relation')
    assert relation['valid_from']
    assert 'review_candidates' not in relation['metadata']
    assert client.get(base+'/reviews').json()['reviews'][0]['status']=='approved'
    assert decide(review).status_code==409
    assert len(service.repository.history(p,c['document_id']))==3


def test_rejection_does_not_write_edge(review):
    assert decide(review,action='reject',target_type='').status_code==200
    client,service,p,base,c=review
    assert not [r for r in service.repository.current_records(p) if r['kind']=='relation']
    assert client.get(base+'/reviews').json()['reviews'][0]['status']=='rejected'


def test_validation_failure_keeps_pending(review):
    assert decide(review,target_type='unknown').status_code==422
    assert decide(review,note='   ').status_code==422
    client,service,p,base,c=review
    assert client.get(base+'/reviews').json()['reviews'][0]['status']=='pending'
    assert len(service.repository.history(p,c['document_id']))==2


def test_new_ontology_allows_candidate_but_does_not_migrate_entities(review):
    client,service,p,base,c=review
    turtle=TTL+'\n:isSubordinateTo a owl:ObjectProperty .'
    new=seed_ontology(service.repository, p,turtle,Ontology(turtle).summary())
    assert decide(review,expected_ontology_id=c['ontology_id']).status_code==409
    result=decide(review,target_type='isSubordinateTo',expected_ontology_id=new['id'])
    assert result.status_code==200,result.text
    rows=service.repository.current_records(p)
    assert next(r for r in rows if r['kind']=='relation')['ontology_id']==new['id']
    assert all(r['ontology_id']==c['ontology_id'] for r in rows if r['kind']=='entity')


def test_source_edit_blocks_approval_and_preserves_original_evidence(review):
    client,service,p,base,c=review
    doc=service.repository.history(p,c['document_id'])[-1]
    revised=writable(doc);revised['text']='已修改原文'
    updated=service.repository.put_record(p,revised,expected_version=doc['version'])
    response=decide(review,expected_version=updated['version'])
    assert response.status_code==422,response.text
    item=client.get(base+'/reviews').json()['reviews'][0]
    assert item['evidence']=='甲属于乙' and item['source_changed']


def test_other_project_cannot_review_document(review):
    client,_,_,_,c=review
    other=client.post('/api/projects',json={'name':'other'}).json()['id']
    response=client.post('/api/projects/'+other+'/reviews/'+c['document_id']+'/'+c['id'],
        json={'action':'reject','note':'拒绝','expected_version':c['document_version']})
    assert response.status_code==404


def test_embedding_failure_leaves_no_partial_approval(review,monkeypatch):
    client,service,p,base,c=review
    repo=service.repository
    index=activate_index(repo,p,service.encoder)
    attempts,writes,drains=install_failing_projection(service,monkeypatch)
    before=repo.current_records(p)
    response=decide(review)
    assert response.status_code==200,response.text
    assert client.get(base+'/reviews').json()['reviews'][0]['status']=='approved'
    assert len(repo.history(p,c['document_id']))==3
    after=repo.current_records(p)
    assert [r for r in before if r['kind']=='entity']==[r for r in after if r['kind']=='entity']
    relation=next(r for r in after if r['kind']=='relation')
    assert relation['id']==response.json()['record_id']
    assertion=repo.get_assertion(p,c['id'])
    assert assertion['status']=='accepted' and assertion['canonical_record_id']==relation['id']
    assert repo.list_assertion_events(p,c['id'])[-1]['to_status']=='accepted'
    queued,first=assert_failed_projection_prefix(repo,p,index,attempts,writes,drains)
    assert first['version_id']==relation['version_id']
    relation_event=next(event for event in queued if event['operation']=='upsert'
                        and event['version_id']==relation['version_id'])
    envelope=relation_event['payload']['_milvus_projection']
    assert envelope['index_version']==index['index_version']
    assert envelope['embedding_model']==service.encoder.identity
    assert envelope['row']['version_id']==relation['version_id']
    assert 'embedding' not in envelope['row']
    assert decide(review).status_code==409


def test_merged_endpoint_redirect_and_deleted_endpoint_block(review):
    client,service,p,base,c=review
    original=next(r for r in service.repository.current_records(p) if r['id']==c['subject_id'])
    replacement=writable(original);replacement['id']='canonical'
    service.repository.put_record(p,replacement,expected_version=0)
    removed=writable(original);removed['metadata']={**original['metadata'],'_deleted':True}
    deleted=service.repository.put_record(p,removed,expected_version=original['version'])
    assert decide(review).status_code==422
    removed['metadata']['merged_into']='canonical'
    service.repository.put_record(p,removed,expected_version=deleted['version'])
    result=decide(review)
    assert result.status_code==200,result.text
    edge=next(r for r in service.repository.current_records(p) if r['kind']=='relation')
    assert edge['subject_id']=='canonical'
