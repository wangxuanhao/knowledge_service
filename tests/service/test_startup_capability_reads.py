"""Cheap public startup policy and identity-scoped OCR capability validation.

This module also runs with unittest, without the PostgreSQL pytest fixtures.
"""
import hashlib
import json
import os
import sys
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from knowledge_service.api.auth import install
from knowledge_service.api.security_middleware import install_auth_middleware
from knowledge_service.integrations import image_ocr


class _UnavailableDependencies:
    def __getattr__(self, name):
        raise AssertionError(f'startup policy touched dependency: {name}')


class StartupPolicyReadsTests(unittest.TestCase):
    def test_public_policy_uses_resolved_application_setting_without_dependencies(self):
        for disabled in (False, True):
            with self.subTest(auth_disabled=disabled):
                app = FastAPI()
                app.state.auth_disabled = disabled
                app.state.service = _UnavailableDependencies()
                users = _UnavailableDependencies()
                if not disabled:
                    install_auth_middleware(app, users=users, jwt_secret='test-secret')
                install(app, users=users, jwt_secret='test-secret', ttl_seconds=60)
                # The process environment may disagree with an explicit create_app argument.
                with patch.dict(os.environ, {'KG_AUTH_DISABLED': '0' if disabled else '1'}):
                    with TestClient(app) as client:
                        response = client.get('/api/auth/policy', headers={'Authorization': 'Bearer stale'})
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.json(), {'auth_enabled': not disabled})
                self.assertEqual(response.headers.get('cache-control'), 'no-store')

    def test_absent_policy_setting_fails_closed_and_protected_api_stays_private(self):
        app = FastAPI()
        users = _UnavailableDependencies()
        install_auth_middleware(app, users=users, jwt_secret='test-secret')
        install(app, users=users, jwt_secret='test-secret', ttl_seconds=60)
        with TestClient(app) as client:
            policy = client.get('/api/auth/policy')
            private = client.get('/api/auth/me')
        self.assertEqual(policy.status_code, 200)
        self.assertEqual(policy.json(), {'auth_enabled': True})
        self.assertEqual(private.status_code, 401)


class OcrCapabilityReadsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.model = self.root / 'model.onnx'
        self.model.write_bytes(b'verified-local-model')
        self.manifest = self.root / 'manifest.json'
        self.manifest_payload = {
            'runtime': {'rapidocr': '>=3.9,<4', 'onnxruntime': '>=1.20,<2'},
            'files': [{'role': 'det', 'path': 'model.onnx',
                       'size_bytes': self.model.stat().st_size,
                       'sha256': hashlib.sha256(self.model.read_bytes()).hexdigest()}],
        }
        self.write_manifest()
        self.versions = {'rapidocr': '3.9.2', 'onnxruntime': '1.20.1'}
        self.enterContext(patch.object(image_ocr, 'MANIFEST', self.manifest))
        self.enterContext(patch.object(image_ocr.verify_models, '__defaults__', (self.manifest,)))
        self.enterContext(patch.object(image_ocr, 'version', lambda name: self.versions[name]))
        self.enterContext(patch.dict(os.environ, {
            'KG_IMAGE_OCR_DEVICE': 'cpu', 'KG_IMAGE_OCR_BACKEND': 'rapidocr',
            'KG_IMAGE_OCR_LANGUAGES': 'ch,en', 'KG_IMAGE_OCR_CONFIG': '',
        }))
        self.model_reads = []
        read_bytes = Path.read_bytes

        def observed_read(path):
            if path.resolve() == self.model.resolve():
                self.model_reads.append(path)
            return read_bytes(path)

        self.enterContext(patch.object(Path, 'read_bytes', observed_read))
        self.adapter = image_ocr.RapidOCRAdapter(self.root)

    def write_manifest(self):
        self.manifest.write_text(json.dumps(self.manifest_payload), encoding='utf-8')

    def test_unchanged_capability_reads_hash_each_model_once(self):
        self.assertTrue(self.adapter.available())
        self.assertTrue(self.adapter.available())
        self.assertTrue(self.adapter.available())
        self.assertEqual(len(self.model_reads), 1)

    def test_new_adapters_share_validation_for_repeated_capability_requests(self):
        for _ in range(3):
            self.assertTrue(image_ocr.RapidOCRAdapter(self.root).available())
        self.assertEqual(len(self.model_reads), 1)

    def test_concurrent_capability_requests_share_one_full_validation(self):
        with ThreadPoolExecutor(max_workers=4) as executor:
            results = list(executor.map(lambda _: image_ocr.RapidOCRAdapter(self.root).available(), range(8)))
        self.assertEqual(results, [True] * 8)
        self.assertEqual(len(self.model_reads), 1)

    def test_replaced_model_invalidates_success_and_repaired_model_retries_failure(self):
        self.assertTrue(self.adapter.available())
        original_stat = self.model.stat()
        replacement = self.root / 'replacement.onnx'
        replacement.write_bytes(b'tampered-local-model')
        os.utime(replacement, ns=(original_stat.st_atime_ns, original_stat.st_mtime_ns))
        replacement.replace(self.model)
        self.assertFalse(self.adapter.available())
        self.assertFalse(self.adapter.available())
        self.assertEqual(len(self.model_reads), 2)
        self.model.write_bytes(b'verified-local-model')
        self.assertTrue(self.adapter.available())
        self.assertEqual(len(self.model_reads), 3)

    def test_missing_model_is_rechecked_when_the_file_appears(self):
        contents = self.model.read_bytes()
        self.model_reads.clear()
        self.model.unlink()
        self.assertFalse(self.adapter.available())
        self.assertFalse(self.adapter.available())
        self.assertEqual(len(self.model_reads), 0)
        self.model.write_bytes(contents)
        self.assertTrue(self.adapter.available())
        self.assertEqual(len(self.model_reads), 1)

    def test_manifest_change_invalidates_validation(self):
        self.assertTrue(self.adapter.available())
        self.manifest_payload['files'][0]['sha256'] = '0' * 64
        self.write_manifest()
        self.assertFalse(self.adapter.available())
        self.assertFalse(self.adapter.available())
        self.assertEqual(len(self.model_reads), 2)

    def test_configuration_file_changes_invalidate_validation(self):
        config = self.root / 'config.yaml'
        config.write_text('EngineConfig: {onnxruntime: {use_cuda: false}}', encoding='utf-8')
        self.adapter.config_path = str(config)
        self.assertTrue(self.adapter.available())
        config.write_text('EngineConfig: {onnxruntime: {use_cuda: true}}', encoding='utf-8')
        self.assertFalse(self.adapter.available())
        self.assertFalse(self.adapter.available())
        self.assertEqual(len(self.model_reads), 2)
        config.unlink()
        self.assertFalse(self.adapter.available())
        config.write_text('EngineConfig: {onnxruntime: {use_cuda: false}}', encoding='utf-8')
        self.assertTrue(self.adapter.available())

    def test_runtime_version_change_invalidates_and_recovers_cached_failure(self):
        self.assertTrue(self.adapter.available())
        self.versions['onnxruntime'] = '2.0.0'
        self.assertFalse(self.adapter.available())
        self.assertFalse(self.adapter.available())
        self.assertEqual(len(self.model_reads), 2)
        self.versions['onnxruntime'] = '1.20.2'
        self.assertTrue(self.adapter.available())

    def test_settings_changes_do_not_reuse_old_availability(self):
        self.assertTrue(self.adapter.available())
        for key, invalid in (('KG_IMAGE_OCR_DEVICE', 'cuda'), ('KG_IMAGE_OCR_BACKEND', 'remote')):
            with self.subTest(setting=key), patch.dict(os.environ, {key: invalid}):
                self.assertFalse(self.adapter.available())
        self.adapter.languages = 'fr'
        self.assertFalse(self.adapter.available())
        self.adapter.languages = 'ch,en'
        self.adapter.max_pixels = 0
        self.assertFalse(self.adapter.available())
        self.adapter.max_pixels = 1000
        self.assertTrue(self.adapter.available())

    def test_engine_initialization_still_fully_verifies_after_capability_cache_hit(self):
        self.assertTrue(self.adapter.available())
        self.assertTrue(self.adapter.available())
        engine = object()
        with patch.dict(sys.modules, {'rapidocr': SimpleNamespace(RapidOCR=lambda **kwargs: engine)}):
            self.adapter._initialize()
        self.assertIs(self.adapter._engine, engine)
        self.assertEqual(len(self.model_reads), 2)


if __name__ == '__main__':
    unittest.main()
