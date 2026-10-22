"""Ordinary Milvus delivery keeps its claim alive during cold encoding."""
from threading import Event, current_thread, main_thread
from types import SimpleNamespace

from knowledge_service.domain.search_filter_catalog import default_catalog
from knowledge_service.domain.search_projection import ProjectionInput, build_projection
from knowledge_service.repository.projection_store import ClaimedBatch, ProjectionLeaseLost
from knowledge_service.services.projection_runtime import MilvusProjectionAdapter
from knowledge_service.services.projection_sync import ProjectionSync


class DeliveryAuthority:
    def __init__(self, *, fail_heartbeat=False):
        self.fail_heartbeat = fail_heartbeat
        self.background_renewed = Event()
        self.background_thread = None
        self.acknowledged = []
        self.failures = []
        self.state = {'phase': 'ready', 'index_version': 2,
                      'catalog': default_catalog().to_dict()}
        row = build_projection(ProjectionInput({
            'project_id': 'p', 'id': 'r', 'version_id': 'v2',
            'kind': 'chunk', 'text': 'cold model work'}),
            default_catalog(), index_version=2)
        event = {'seq': 1, 'operation': 'upsert', 'payload': {
            '_milvus_projection': {'index_version': 2,
                'catalog_fingerprint': default_catalog().fingerprint,
                'embedding_model': 'real-model', 'row': row}}}
        self.claim = ClaimedBatch((event,), 'owner', 'token',
            '2099-01-01T00:00:00Z', 1, 1, 'p', 'milvus', 3,
            index_version=2, catalog=self.state['catalog'])

    def claim_contiguous_batch(self, *_args, **_kwargs):
        return self.claim

    def cursor(self, *_args):
        return {'rebuild_state': self.state}

    def renew_claim(self, claim):
        if current_thread() is not main_thread():
            self.background_thread = current_thread()
            self.background_renewed.set()
            if self.fail_heartbeat:
                raise ProjectionLeaseLost('renewal was rejected')
        return claim

    def ack_contiguous(self, claim, _schema):
        self.acknowledged.append(claim.to_seq)

    def mark_failed(self, _claim, error, _schema):
        self.failures.append(error)


def delivery(*, fail_heartbeat=False):
    authority = DeliveryAuthority(fail_heartbeat=fail_heartbeat)
    writes = []
    store = SimpleNamespace(check_collection=lambda **_kwargs: None,
        upsert_rows=lambda rows, **_kwargs: writes.extend(rows))

    def encode(texts):
        # A successful heartbeat must occur while encoding is still blocked.
        # One second is only a deadlock/test failure bound, not a lease delay.
        authority.background_renewed.wait(1)
        if authority.fail_heartbeat and authority.background_thread is not None:
            authority.background_thread.join(timeout=1)
        return [[1., 0.] for _ in texts]

    encoder = SimpleNamespace(identity='real-model', encode=encode)
    adapter = MilvusProjectionAdapter(store, encoder=encoder,
                                     fence_is_current=lambda *_args: True)
    sync = ProjectionSync(authority, {'milvus': adapter}, schema_version=3)
    sync.REBUILD_HEARTBEAT_SECONDS = .01
    return authority, writes, sync


def test_ordinary_incremental_renews_in_background_before_encoding_finishes():
    authority, writes, sync = delivery()
    result = sync.drain_once('p', 'milvus')
    assert authority.background_renewed.is_set(), 'ordinary delivery had no heartbeat'
    assert result.status == 'applied'
    assert len(writes) == 1
    assert authority.acknowledged == [1]


def test_failed_heartbeat_during_encoding_stops_before_upsert_and_ack():
    authority, writes, sync = delivery(fail_heartbeat=True)
    result = sync.drain_once('p', 'milvus')
    assert result.status == 'requires_rebuild'
    assert not writes, 'lost heartbeat authorized an external write'
    assert not authority.acknowledged
    assert any(isinstance(error, ProjectionLeaseLost) for error in authority.failures)
