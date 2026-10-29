"""Remote imports validate destinations and use a pinned transport seam."""
import io
import pytest
from PIL import Image


def png():
    stream=io.BytesIO();Image.new('RGB',(8,8),'red').save(stream,format='PNG');return stream.getvalue()


def test_remote_import_pins_public_ip_and_returns_frozen_bytes():
    from knowledge_service.integrations.remote_images import RemoteImageImporter
    calls=[]
    def transport(url,address,timeout,max_bytes):
        calls.append((url,address));return 200,{'Content-Type':'image/png'},png()
    importer=RemoteImageImporter(resolver=lambda host,port:['93.184.216.34'],transport=transport)
    result=importer.fetch('https://example.com/figure.png')
    assert result['data']==png() and result['mime_type']=='image/png'
    assert calls==[('https://example.com/figure.png','93.184.216.34')]


@pytest.mark.parametrize('url',['http://127.0.0.1/x','http://169.254.169.254/latest','http://localhost/x','https://user:pass@example.com/x','file:///etc/passwd','http://example.com:8080/x','https://[::1]/x'])
def test_private_or_unsafe_urls_never_reach_transport(url):
    from knowledge_service.integrations.remote_images import RemoteImageImporter,RemoteImageError
    importer=RemoteImageImporter(resolver=lambda h,p:['127.0.0.1'],transport=lambda *a:pytest.fail('unsafe request'))
    with pytest.raises(RemoteImageError): importer.fetch(url)


def test_redirect_and_mixed_dns_answers_are_revalidated():
    from knowledge_service.integrations.remote_images import RemoteImageImporter,RemoteImageError
    calls=[]
    def transport(*args): calls.append(args);return 302,{'Location':'http://169.254.169.254/x'},b''
    importer=RemoteImageImporter(resolver=lambda h,p:['93.184.216.34'],transport=transport)
    with pytest.raises(RemoteImageError): importer.fetch('https://example.com/x')
    assert len(calls)==1
    importer=RemoteImageImporter(resolver=lambda h,p:['93.184.216.34','10.0.0.1'],transport=lambda *a:pytest.fail('mixed DNS'))
    with pytest.raises(RemoteImageError): importer.fetch('https://example.com/x')


def test_rebinding_cannot_change_transport_address_and_invalid_bytes_fail():
    from knowledge_service.integrations.remote_images import RemoteImageImporter,RemoteImageError
    answers=iter([['93.184.216.34'],['127.0.0.1']]);seen=[]
    def transport(url,address,*args): seen.append(address);return 200,{'Content-Type':'image/png'},b'not image'
    importer=RemoteImageImporter(resolver=lambda *a:next(answers),transport=transport)
    with pytest.raises(RemoteImageError): importer.fetch('https://example.com/x')
    assert seen==['93.184.216.34']


@pytest.mark.parametrize('status,headers,data',[(200,{'Content-Type':'text/html'},b'<html>bad</html>'),(401,{},b''),(200,{'Content-Type':'image/png'},b'x'*300)])
def test_http_auth_mime_and_size_failures_are_bounded(status,headers,data):
    from knowledge_service.integrations.remote_images import RemoteImageImporter,RemoteImageError
    importer=RemoteImageImporter(resolver=lambda *a:['93.184.216.34'],transport=lambda *a:(status,headers,data),max_bytes=200)
    with pytest.raises(RemoteImageError):importer.fetch('https://example.com/x')


def test_remote_source_freezes_duplicate_occurrences_in_order_and_never_fetches_on_read(tmp_path):
    from knowledge_service.repository import Repository
    from knowledge_service.integrations.source_object_store import SourceObjectStore
    from knowledge_service.services.source_documents import SourceDocumentService
    from knowledge_service.integrations.remote_images import RemoteImageImporter
    calls=[]
    def transport(url,*args):calls.append(url);return 200,{'Content-Type':'image/png'},png()
    importer=RemoteImageImporter(resolver=lambda *a:['93.184.216.34'],transport=transport)
    repo=Repository(tmp_path/'remote.sqlite');project=repo.create_project('remote')['id']
    sources=SourceDocumentService(repo,SourceObjectStore(tmp_path/'objects'),remote_image_importer=importer)
    markdown='Before\n\n![one](https://example.com/x) ![two](https://example.com/x)\n\nAfter'
    version=sources.persist(project,'doc',markdown.encode(),'a.md',metadata={'document_parser':'builtin-text'})
    rendered=sources.rendered(project,version['id'])
    assert len(version['assets'])==2 and len(calls)==1
    assert rendered['html'].index('alt="one"')<rendered['html'].index('alt="two"')
    assert all(a['provenance']['original_url']=='https://example.com/x' for a in version['assets'])
    assert sources.source(project,version['id'])['data']==markdown.encode()
    assert len(calls)==1
    repo.close()


def test_inaccessible_remote_image_keeps_visible_placeholder_and_warning(tmp_path):
    from knowledge_service.repository import Repository
    from knowledge_service.integrations.source_object_store import SourceObjectStore
    from knowledge_service.services.source_documents import SourceDocumentService
    from knowledge_service.integrations.remote_images import RemoteImageImporter
    importer=RemoteImageImporter(resolver=lambda *a:['93.184.216.34'],transport=lambda *a:(404,{},b''))
    repo=Repository(tmp_path/'missing.sqlite');project=repo.create_project('missing')['id']
    sources=SourceDocumentService(repo,SourceObjectStore(tmp_path/'objects'),remote_image_importer=importer)
    version=sources.persist(project,'doc',b'Article\n\n![missing](https://example.com/x)','a.md')
    assert version['assets'][0]['status']=='missing'
    assert version['parser_metadata']['warnings']
    assert 'src="https:' not in sources.rendered(project,version['id'])['html']
    assert 'data-asset-id=' in sources.rendered(project,version['id'])['html']


def test_dns_resolution_respects_the_import_deadline():
    import threading,time
    from knowledge_service.integrations.remote_images import RemoteImageImporter,RemoteImageError
    release=threading.Event()
    def resolver(*a):release.wait(3);return ['93.184.216.34']
    importer=RemoteImageImporter(resolver=resolver,transport=lambda *a:pytest.fail('expired resolution'),timeout=.02)
    started=time.monotonic()
    try:
        with pytest.raises(RemoteImageError):importer.fetch('https://example.com/x')
        assert time.monotonic()-started<1
    finally:release.set()


@pytest.mark.parametrize('drip_status',[True,False])
def test_pinned_transport_deadline_interrupts_slow_status_and_headers(drip_status):
    import socketserver,threading,time
    from knowledge_service.integrations.remote_images import _request
    stopped=threading.Event()
    class Handler(socketserver.BaseRequestHandler):
        def handle(self):
            self.request.recv(8192)
            try:
                if not drip_status:self.request.sendall(b'HTTP/1.1 200 OK\r\n')
                slow=b'HTTP/1.1 200 OK\r\n' if drip_status else b'Content-Type: image/png\r\n'
                for char in slow:
                    if stopped.wait(.01):return
                    self.request.sendall(bytes([char]))
                self.request.sendall((b'Content-Type: image/png\r\n' if drip_status else b'')+b'Content-Length: 0\r\n\r\n')
            except OSError:pass
    class Server(socketserver.ThreadingTCPServer):daemon_threads=True
    with Server(('127.0.0.1',0),Handler) as server:
        thread=threading.Thread(target=server.serve_forever,kwargs={'poll_interval':.01},daemon=True);thread.start()
        started=time.monotonic()
        try:
            with pytest.raises(Exception):_request(f'http://127.0.0.1:{server.server_address[1]}/image','127.0.0.1',.04,200)
            assert time.monotonic()-started<.15
        finally:stopped.set();server.shutdown();thread.join(1)


@pytest.mark.parametrize('package',[False,True])
def test_remote_preparation_happens_outside_publication_transaction(tmp_path,package):
    import zipfile
    from types import SimpleNamespace
    from knowledge_service.repository import Repository
    from knowledge_service.services.service import KnowledgeService
    from knowledge_service.integrations.embeddings import HashingEncoder
    from knowledge_service.integrations.source_object_store import SourceObjectStore
    from knowledge_service.services.source_documents import SourceDocumentService
    from knowledge_service.services.ingest_batches import IngestBatches
    from knowledge_service.integrations.remote_images import RemoteImageImporter
    repo=Repository(tmp_path/'network-uow.sqlite');project=repo.create_project('remote')['id'];observed=[]
    def transport(*args):observed.append(repo._db.in_transaction);return 200,{'Content-Type':'image/png'},png()
    sources=SourceDocumentService(repo,SourceObjectStore(tmp_path/'objects'),remote_image_importer=RemoteImageImporter(resolver=lambda *a:['93.184.216.34'],transport=transport))
    markdown=b'Article\n\n![image](https://example.com/x)'
    if package:
        stream=io.BytesIO()
        with zipfile.ZipFile(stream,'w') as archive:archive.writestr('article.md',markdown)
        application=IngestBatches(KnowledgeService(repo,HashingEncoder()),repo.ingest_batches,sources=sources)
        batch=application.create(project,{'extraction_mode':'documents'})
        application.upload(project,batch['id'],SimpleNamespace(filename='article.zip',file=io.BytesIO(stream.getvalue())),'key')
    else:sources.persist(project,'doc',markdown,'a.md')
    assert observed==[False]


def test_markdown_dimensions_and_invalid_payload_status_do_not_depend_on_ocr(tmp_path):
    from knowledge_service.repository import Repository
    from knowledge_service.integrations.source_object_store import SourceObjectStore
    from knowledge_service.services.source_documents import SourceDocumentService
    repo=Repository(tmp_path/'dimensions.sqlite');project=repo.create_project('dimensions')['id']
    sources=SourceDocumentService(repo,SourceObjectStore(tmp_path/'objects'))
    version=sources.persist(project,'doc',b'![valid](a.png) ![invalid](b.png)','a.md',assets=[
        dict(kind='local',status='available',original_path='a.png',data=png(),mime_type='image/png'),
        dict(kind='local',status='available',original_path='b.png',data=b'invalid',mime_type='image/png')])
    assert version['assets'][0]['width']==8 and version['assets'][0]['height']==8
    assert version['assets'][1]['status']=='missing' and version['assets'][1]['source_object_id'] is None
    assert version['parser_metadata']['warnings']


def test_decompression_bomb_image_keeps_readable_text_and_missing_placeholder(tmp_path,monkeypatch):
    from knowledge_service.repository import Repository
    from knowledge_service.integrations.source_object_store import SourceObjectStore
    from knowledge_service.services.source_documents import SourceDocumentService
    from PIL import Image
    monkeypatch.setattr(Image,'MAX_IMAGE_PIXELS',1)
    repo=Repository(tmp_path/'bomb.sqlite');project=repo.create_project('bomb')['id']
    sources=SourceDocumentService(repo,SourceObjectStore(tmp_path/'objects'))
    version=sources.persist(project,'doc',b'Article\n\n![large](a.png)','a.md',assets=[dict(kind='local',status='available',original_path='a.png',data=png(),mime_type='image/png')])
    assert version['source_text'].startswith('Article')
    assert version['assets'][0]['status']=='missing'
    assert version['parser_metadata']['warnings']


def test_shared_local_bytes_preserve_each_reference_alt_text(tmp_path):
    from knowledge_service.repository import Repository
    from knowledge_service.integrations.source_object_store import SourceObjectStore
    from knowledge_service.services.source_documents import SourceDocumentService
    repo=Repository(tmp_path/'duplicate-local.sqlite');project=repo.create_project('duplicate')['id']
    sources=SourceDocumentService(repo,SourceObjectStore(tmp_path/'objects'))
    version=sources.persist(project,'doc',b'![first](a.png) ![second](a.png)','a.md',assets=[dict(kind='local',status='available',original_path='a.png',alt='first',data=png())])
    assert [a['alt_text'] for a in version['assets']]==['first','second']


def test_asset_occurrence_mapping_is_linear_and_decoding_is_capped(monkeypatch):
    from knowledge_service.services.source_documents import SourceDocumentService
    from knowledge_service.integrations import document_media
    class CountedAsset(dict):
        lookups=0
        def get(self,*args):type(self).lookups+=1;return super().get(*args)
    inspected=[]
    monkeypatch.setattr(document_media,'inspect_image',lambda data,mime:(inspected.append(data) or dict(width=8,height=8,mime_type='image/png')))
    assets=[CountedAsset(kind='local',status='available',original_path='same.png',data=b'image',mime_type='image/png') for _ in range(500)]
    prepared=SourceDocumentService(None,object()).prepare(b'', 'a.md',text=' '.join(f'![image {i}](same.png)' for i in range(500)),assets=assets)
    assert len(inspected)<=200
    assert CountedAsset.lookups<20_000
    assert len(prepared['assets'])==500 and prepared['assets'][-1]['status']=='missing'
