"""Raw reads remain exact when a frozen Markdown object is missing locally."""
import tempfile
import unittest
from pathlib import Path
from urllib.parse import quote

from fastapi import FastAPI
from fastapi.testclient import TestClient

from knowledge_service.api.source_documents import install
from knowledge_service.integrations.source_object_store import SourceObjectStore
from knowledge_service.services.source_documents import SourceDocumentService


class SourceRawRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.object_store = SourceObjectStore(Path(self.temporary.name) / 'sources')
        self.service = SourceDocumentService(None, self.object_store)
        self.path = '/api/projects/project/source-documents/frozen-version'

    def client(self, data, text=None, source_format='markdown', mime_type='text/markdown'):
        obj = self.object_store.put(data, mime_type)
        self.version = dict(obj, id='frozen-version', project_id='project', format=source_format,
                            original_name='平台基础规则_美团侵权投诉须知.md',
                            source_text=data.decode('utf-8-sig') if text is None else text)

        def get_version(project_id, version_id):
            if project_id != 'project' or version_id != 'frozen-version':
                raise KeyError('Source version not found')
            return dict(self.version)

        self.service.store.get_version = get_version
        app = FastAPI()
        app.state.source_documents = self.service
        install(app, None)
        return TestClient(app, raise_server_exceptions=False)

    def remove_object(self):
        self.object_path = self.object_store.root / self.version['storage_key']
        self.object_path.unlink()

    def test_missing_markdown_returns_verified_exact_frozen_bytes(self):
        raw = '# 平台规则\r\n\r\n侵权投诉须知。\r\n'.encode('utf-8')
        client = self.client(raw)
        self.remove_object()

        response = client.get(self.path + '/source')

        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.content, raw)
        self.assertIn(quote(self.version['original_name'], safe=''),
                      response.headers['content-disposition'])
        self.assertFalse(self.object_path.exists(), 'Read must not repair source storage implicitly')

    def test_missing_markdown_preserves_original_utf8_bom(self):
        raw = '# 规则\n'.encode('utf-8-sig')
        client = self.client(raw)
        self.remove_object()

        response = client.get(self.path + '/source')

        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.content, raw)

    def test_missing_object_with_different_frozen_text_is_explicitly_unavailable(self):
        client = self.client(b'# Original\n', text='# Changed\n')
        self.remove_object()

        for suffix in ('/source', '/package'):
            response = client.get(self.path + suffix)
            self.assertEqual(response.status_code, 503, response.text)
            self.assertEqual(response.json()['detail']['code'], 'source_object_unavailable')

    def test_missing_non_markdown_is_not_reconstructed_from_extracted_text(self):
        client = self.client(b'Original source', source_format='docx', mime_type='application/octet-stream')
        self.remove_object()

        response = client.get(self.path + '/source')

        self.assertEqual(response.status_code, 503, response.text)
        self.assertEqual(response.json()['detail']['code'], 'source_object_unavailable')

    def test_existing_object_checksum_error_is_not_replaced_with_database_text(self):
        client = self.client(b'# Frozen\n')
        (self.object_store.root / self.version['storage_key']).write_bytes(b'corrupt')

        response = client.get(self.path + '/source')

        self.assertEqual(response.status_code, 500)
        with self.assertRaisesRegex(ValueError, 'checksum mismatch'):
            self.service.source('project', 'frozen-version')

    def test_existing_package_returns_selected_markdown_and_original_archive(self):
        package = b'PK\x03\x04stored archive bytes'
        client = self.client(package, text='# Selected\n', mime_type='application/zip')

        self.assertEqual(client.get(self.path + '/source').content, b'# Selected\n')
        self.assertEqual(client.get(self.path + '/package').content, package)


if __name__ == '__main__':
    unittest.main()
