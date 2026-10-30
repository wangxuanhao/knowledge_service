"""Offline export eligibility is inspectable without loading any model library."""
import importlib
import importlib.util
import json
from pathlib import Path
import sys
import subprocess
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))


class ExportContractTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(importlib.util.find_spec(
            'knowledge_service.integrations.embedding_export'),
            'offline preparation must have a separate, lightweight module')
        self.export = importlib.import_module('knowledge_service.integrations.embedding_export')
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.write('modules.json', [
            {'idx': 0, 'name': '0', 'path': '', 'type': 'sentence_transformers.models.Transformer'},
            {'idx': 1, 'name': '1', 'path': '1_Pooling', 'type': 'sentence_transformers.models.Pooling'},
            {'idx': 2, 'name': '2', 'path': '2_Normalize', 'type': 'sentence_transformers.models.Normalize'}])
        self.write('config.json', {'model_type': 'xlm-roberta', 'architectures': ['XLMRobertaModel'],
                                  'hidden_size': 1024, 'pad_token_id': 1})
        self.write('1_Pooling/config.json', {'word_embedding_dimension': 1024,
            'pooling_mode_cls_token': True, 'pooling_mode_mean_tokens': False})
        self.write('sentence_bert_config.json', {'max_seq_length': 8192, 'do_lower_case': False})
        self.write('tokenizer_config.json', {'tokenizer_class': 'XLMRobertaTokenizer', 'pad_token': '<pad>'})
        self.write('tokenizer.json', {})
        (self.root / 'pytorch_model.bin').write_bytes(b'fixture weights')

    def write(self, name, value):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value), encoding='utf-8')

    def test_exact_dense_pipeline_is_eligible_without_model_imports(self):
        result = self.export.inspect_pipeline(self.root)
        self.assertEqual(result['dimension'], 1024)
        self.assertEqual(result['max_length'], 8192)
        probe = '''
import importlib.abc, sys
sys.path.insert(0, sys.argv[1])
class BlockML(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname.split('.')[0] in {'torch','transformers','sentence_transformers'}:
            raise AssertionError('heavy import: ' + fullname)
sys.meta_path.insert(0, BlockML())
from knowledge_service.integrations.embedding_export import inspect_pipeline
inspect_pipeline(sys.argv[2])
'''
        checked = subprocess.run([sys.executable, '-I', '-c', probe,
                                  str(Path(__file__).resolve().parents[2]), str(self.root)],
                                 capture_output=True, text=True, timeout=30)
        self.assertEqual(checked.returncode, 0, checked.stdout + checked.stderr)

    def test_extra_projection_is_rejected(self):
        modules = json.loads((self.root / 'modules.json').read_text())
        modules.append({'type': 'sentence_transformers.models.Dense', 'path': '3_Dense'})
        self.write('modules.json', modules)
        with self.assertRaisesRegex(ValueError, 'pipeline|流水线'):
            self.export.inspect_pipeline(self.root)

    def test_non_cls_pooling_is_rejected(self):
        self.write('1_Pooling/config.json', {'word_embedding_dimension': 1024,
            'pooling_mode_cls_token': True, 'pooling_mode_mean_tokens': True})
        with self.assertRaisesRegex(ValueError, 'pooling|池化'):
            self.export.inspect_pipeline(self.root)

    def test_custom_transformer_options_are_rejected(self):
        self.write('sentence_bert_config.json', {'max_seq_length': 8192,
            'do_lower_case': False, 'model_args': {'trust_remote_code': True}})
        with self.assertRaisesRegex(ValueError, 'custom|自定义'):
            self.export.inspect_pipeline(self.root)

    def test_prompts_are_rejected(self):
        self.write('config_sentence_transformers.json', {'prompts': {'query': 'query: '}})
        with self.assertRaisesRegex(ValueError, 'prompt|提示'):
            self.export.inspect_pipeline(self.root)

    def test_alternate_weights_are_rejected(self):
        (self.root / 'model.safetensors').write_bytes(b'alternate preferred weights')
        with self.assertRaisesRegex(ValueError, 'weight|权重'):
            self.export.inspect_pipeline(self.root)

    def test_missing_weights_are_rejected(self):
        (self.root / 'pytorch_model.bin').unlink()
        with self.assertRaisesRegex(ValueError, 'weight|权重'):
            self.export.inspect_pipeline(self.root)

    def test_invalid_module_path_is_rejected(self):
        modules = json.loads((self.root / 'modules.json').read_text())
        modules[1]['path'] = '../outside'
        self.write('modules.json', modules)
        with self.assertRaisesRegex(ValueError, 'pipeline|流水线'):
            self.export.inspect_pipeline(self.root)

    def test_failed_activation_keeps_previous_active_manifest(self):
        activate = getattr(self.export, '_activate_export', None)
        self.assertTrue(callable(activate), 'activation must be independently testable')
        destination = self.root / 'onnx-runtime'
        staging = destination / 'new.staging'
        staging.mkdir(parents=True)
        (staging / 'model.onnx').write_bytes(b'new model')
        active = destination / 'active.json'
        active.write_text('{"artifact_dir":"old"}')
        with patch.object(self.export.os, 'replace', side_effect=OSError('activation denied')):
            with self.assertRaisesRegex(OSError, 'activation denied'):
                activate(destination, staging, 'new', {'artifact_dir': 'new'})
        self.assertEqual(json.loads(active.read_text()), {'artifact_dir': 'old'})

    def test_successful_activation_preserves_previous_version(self):
        activate = getattr(self.export, '_activate_export', None)
        self.assertTrue(callable(activate), 'activation must be independently testable')
        destination = self.root / 'onnx-runtime'
        previous = destination / 'old'
        previous.mkdir(parents=True)
        (previous / 'model.onnx').write_bytes(b'old model')
        staging = destination / 'new.staging'
        staging.mkdir()
        (staging / 'model.onnx').write_bytes(b'new model')
        activate(destination, staging, 'new', {'artifact_dir': 'new'})
        self.assertEqual((previous / 'model.onnx').read_bytes(), b'old model')
        self.assertEqual((destination / 'new/model.onnx').read_bytes(), b'new model')
        self.assertEqual(json.loads((destination / 'active.json').read_text()), {'artifact_dir': 'new'})


if __name__ == '__main__':
    unittest.main()
