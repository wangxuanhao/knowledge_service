"""The local facade selects a prepared runtime without loading model libraries."""
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from knowledge_service.integrations.embeddings import LocalEncoder


class RuntimeSelectionTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        (self.root / 'config.json').write_text(json.dumps({'hidden_size': 2}))
        self.calls = []

    def model(self, name):
        def create(path, **kwargs):
            self.calls.append(name)
            return SimpleNamespace(get_sentence_embedding_dimension=lambda: 2,
                encode=lambda texts, **options: [[1., 0.] for _ in texts])
        return create

    def encode(self, runtime, *, prepared=False):
        if prepared:
            directory = self.root / 'onnx-runtime'
            directory.mkdir()
            (directory / 'active.json').write_text('{}')
        with patch.dict('os.environ', {'KG_LOCAL_EMBEDDING_RUNTIME': runtime}), patch.dict(sys.modules, {
                'sentence_transformers': SimpleNamespace(SentenceTransformer=self.model('legacy')),
                'knowledge_service.integrations.onnx_embeddings': SimpleNamespace(
                    OnnxSentenceEncoder=self.model('onnx'))}):
            return LocalEncoder(self.root).encode(['未成年'])

    def test_prepared_model_uses_onnx_by_default(self):
        self.assertEqual(self.encode('auto', prepared=True), [[1., 0.]])
        self.assertEqual(self.calls, ['onnx'])

    def test_unprepared_generic_model_keeps_legacy_compatibility(self):
        self.encode('auto')
        self.assertEqual(self.calls, ['legacy'])

    def test_explicit_legacy_runtime_allows_rollback(self):
        self.encode('sentence-transformers', prepared=True)
        self.assertEqual(self.calls, ['legacy'])

    def test_explicit_onnx_requires_preparation(self):
        with self.assertRaisesRegex(RuntimeError, 'ONNX|onnx'):
            self.encode('onnx')
        self.assertEqual(self.calls, [])

    def test_unknown_runtime_is_an_actionable_error(self):
        with self.assertRaisesRegex(RuntimeError, 'KG_LOCAL_EMBEDDING_RUNTIME'):
            self.encode('surprise')
        self.assertEqual(self.calls, [])


if __name__ == '__main__':
    unittest.main()
