"""Small real ONNX/tokenizer tests; execute directly without the PG pytest fixture."""
from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch


PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

DEPENDENCIES_AVAILABLE = all(
    importlib.util.find_spec(name) for name in ("numpy", "onnx", "onnxruntime", "tokenizers")
)


def _digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _write_model(path, *, normalized=True, output_dtype="float32", output_name="sentence_embedding", external_data=False):
    import numpy as np
    import onnx
    from onnx import TensorProto, helper, numpy_helper

    inputs = [
        helper.make_tensor_value_info(name, TensorProto.INT64, ["batch", "sequence"])
        for name in ("input_ids", "attention_mask")
    ]
    nodes = [
        helper.make_node("Cast", ["input_ids"], ["ids"], to=TensorProto.FLOAT),
        helper.make_node("Cast", ["attention_mask"], ["mask"], to=TensorProto.FLOAT),
        helper.make_node("Mul", ["ids", "mask"], ["masked_ids"]),
        helper.make_node("ReduceSum", ["masked_ids", "axes"], ["sum"], keepdims=1),
        helper.make_node("Mul", ["sum", "zero"], ["zeros"]),
        helper.make_node("Add", ["zeros", "one"], ["ones"]),
        helper.make_node("Concat", ["sum", "ones"], ["features"], axis=1),
    ]
    if normalized:
        nodes.append(helper.make_node("LpNormalization", ["features"], ["normalized"], axis=1, p=2))
        current = "normalized"
    else:
        current = "features"
    dtype = TensorProto.FLOAT if output_dtype == "float32" else TensorProto.DOUBLE
    nodes.append(helper.make_node("Cast", [current], [output_name], to=dtype))
    graph = helper.make_graph(
        nodes,
        "small_sentence_encoder",
        inputs,
        [helper.make_tensor_value_info(output_name, dtype, ["batch", 2])],
        [
            helper.make_tensor("axes", TensorProto.INT64, [1], [1]),
            numpy_helper.from_array(np.array([0], dtype=np.float32), "zero"),
            numpy_helper.from_array(np.array([1], dtype=np.float32), "one"),
        ],
    )
    model = helper.make_model(graph, opset_imports=[helper.make_opsetid("", 17)])
    model.ir_version = 9
    onnx.checker.check_model(model)
    onnx.save_model(
        model, str(path), save_as_external_data=external_data,
        all_tensors_to_one_file=True, location="model.onnx.data", size_threshold=0,
    )


def _create_export(root, *, max_length=16, **model_options):
    from tokenizers import Tokenizer, models, pre_tokenizers, processors

    root = Path(root)
    source_names = (
        "config.json", "modules.json", "sentence_bert_config.json", "1_Pooling/config.json",
        "tokenizer.json", "pytorch_model.bin",
    )
    tokenizer = Tokenizer(models.WordLevel(
        {"[UNK]": 0, "<pad>": 1, "<s>": 2, "</s>": 3, "a": 4, "A": 5, "b": 6, "c": 7},
        unk_token="[UNK]",
    ))
    tokenizer.pre_tokenizer = pre_tokenizers.WhitespaceSplit()
    tokenizer.post_processor = processors.TemplateProcessing(
        single="<s> $A </s>", special_tokens=[("<s>", 2), ("</s>", 3)]
    )
    for name in source_names:
        target = root / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(b"dummy source weights" if name.endswith(".bin") else b"{}")
    tokenizer.save(str(root / "tokenizer.json"))
    version = root / "onnx-runtime" / "test-version"
    version.mkdir(parents=True)
    tokenizer.save(str(version / "tokenizer.json"))
    _write_model(version / "model.onnx", **model_options)
    manifest = {
        "schema_version": 1,
        "contract": "xlm-roberta-cls-normalized-v1",
        "artifact_dir": "test-version",
        "dtype": "float32",
        "provider": "CPUExecutionProvider",
        "opset": 17,
        "dimension": 2,
        "max_length": max_length,
        "do_lower_case": False,
        "pad_id": 1,
        "pad_token": "<pad>",
        "source_files": {name: _digest(root / name) for name in source_names},
        "artifact_files": {path.name: _digest(path) for path in version.iterdir()},
        "validation": {
            "passed": True, "max_abs_error": 0.000001, "min_cosine": 0.999999,
            "tokenization_equal": True, "max_norm_error": 0.000001,
        },
    }
    _write_manifest(root, manifest)
    return version, manifest


def _write_manifest(root, manifest):
    (Path(root) / "onnx-runtime" / "active.json").write_text(json.dumps(manifest), encoding="utf-8")


@unittest.skipUnless(DEPENDENCIES_AVAILABLE, "optional ONNX runtime dependencies are not installed")
class OnnxEmbeddingRuntimeTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="onnx-embedding-test-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.version, self.manifest = _create_export(self.root)

    def encoder(self):
        from knowledge_service.integrations.onnx_embeddings import OnnxSentenceEncoder
        return OnnxSentenceEncoder(self.root, local_files_only=True)

    def read_manifest(self):
        from knowledge_service.integrations.embedding_artifacts import read_export_manifest
        return read_export_manifest(self.root)

    def test_runtime_module_has_required_public_api(self):
        for module_name, attribute in (
            ("embedding_artifacts", "read_export_manifest"),
            ("embedding_artifacts", "file_sha256"),
            ("onnx_embeddings", "OnnxSentenceEncoder"),
        ):
            spec = importlib.util.find_spec(f"knowledge_service.integrations.{module_name}")
            self.assertIsNotNone(spec, f"The lightweight runtime module {module_name} is missing")
            module = __import__(f"knowledge_service.integrations.{module_name}", fromlist=[attribute])
            self.assertTrue(callable(getattr(module, attribute, None)))

    def test_real_ort_dynamic_batches_strip_case_empty_and_input_order(self):
        import numpy as np

        encoder = self.encoder()
        self.assertEqual(encoder.get_sentence_embedding_dimension(), 2)
        texts = [" b c ", "A", "", "a", "   ", None]
        vectors = encoder.encode(texts, batch_size=2)
        sums = np.array([18, 10, 5, 9, 5, 5], dtype=np.float32)
        expected = np.column_stack((sums, np.ones(len(texts), dtype=np.float32)))
        expected /= np.linalg.norm(expected, axis=1, keepdims=True)
        np.testing.assert_allclose(vectors, expected, atol=1e-6)
        self.assertEqual(vectors.dtype, np.float32)
        self.assertEqual(encoder.encode([], batch_size=4).shape, (0, 2))
        for batch_size in (1, 3, 10):
            np.testing.assert_allclose(encoder.encode(texts, batch_size=batch_size), expected, atol=1e-6)

    def test_longest_first_right_truncation_preserves_special_tokens(self):
        import numpy as np

        vector = self.encoder().encode([" ".join(["A"] * 100)])[0]
        expected = np.array([2 + 14 * 5 + 3, 1], dtype=np.float32)
        expected /= np.linalg.norm(expected)
        np.testing.assert_allclose(vector, expected, atol=1e-6)

    def test_long_sequences_use_singleton_batches_and_keep_max_length(self):
        import onnxruntime

        self.manifest["max_length"] = 2048
        _write_manifest(self.root, self.manifest)
        real_session = onnxruntime.InferenceSession
        calls = []

        class RecordingSession:
            def __init__(self, *args, **kwargs):
                self.session = real_session(*args, **kwargs)
                self.options = kwargs.get("sess_options")
                self.providers = kwargs.get("providers")

            def __getattr__(self, name):
                return getattr(self.session, name)

            def run(self, output_names, feed):
                calls.append((output_names, {name: array.copy() for name, array in feed.items()}))
                return self.session.run(output_names, feed)

        with patch.object(onnxruntime, "InferenceSession", RecordingSession):
            encoder = self.encoder()
        texts = ["A " * 1050, "a", "b " * 1200, "c", "A " * 1060]
        self.assertEqual(encoder.encode(texts, batch_size=4).shape, (5, 2))
        long_calls = [feed for _, feed in calls if feed["input_ids"].shape[1] > 1024]
        self.assertEqual(len(long_calls), 3)
        self.assertTrue(all(feed["input_ids"].shape[0] == 1 for feed in long_calls))
        self.assertEqual(max(feed["input_ids"].shape[1] for feed in long_calls), 1202)
        for outputs, feed in calls:
            self.assertEqual(outputs, ["sentence_embedding"])
            self.assertEqual(set(feed), {"input_ids", "attention_mask"})
            self.assertTrue(all(str(value.dtype) == "int64" for value in feed.values()))

    def test_absent_activation_is_the_only_optional_result(self):
        (self.root / "onnx-runtime" / "active.json").unlink()
        self.assertIsNone(self.read_manifest())
        with self.assertRaisesRegex((ValueError, RuntimeError, FileNotFoundError), "(?i)(activ|export)"):
            self.encoder()

    def test_new_source_weights_or_optional_configs_invalidate_old_export(self):
        for name in ('model.safetensors', 'model.safetensors.index.json',
                     'pytorch_model.bin.index.json', 'adapter_config.json', 'added_tokens.json',
                     'tokenizer_config.json', 'special_tokens_map.json', 'config_sentence_transformers.json'):
            with self.subTest(name=name):
                extra = self.root / name
                extra.write_text('{}', encoding='utf-8')
                try:
                    with self.assertRaisesRegex(ValueError, '(?i)(source|weight|presence)'):
                        self.read_manifest()
                finally:
                    extra.unlink()

    def test_hashes_are_validated_once_during_initialization(self):
        from knowledge_service.integrations import embedding_artifacts, onnx_embeddings

        with patch.object(
            onnx_embeddings, "read_export_manifest", wraps=embedding_artifacts.read_export_manifest
        ) as read:
            encoder = self.encoder()
            (self.root / "pytorch_model.bin").write_bytes(b"source changed after initialization")
            for _ in range(3):
                self.assertEqual(encoder.encode(["A"]).shape, (1, 2))
            self.assertEqual(read.call_count, 1)
        with self.assertRaisesRegex(ValueError, "(?i)(hash|sha256|checksum)"):
            self.encoder()

    def test_manifest_returns_verified_paths_and_public_hash(self):
        from knowledge_service.integrations.embedding_artifacts import file_sha256

        version, manifest = self.read_manifest()
        self.assertEqual(version, self.version.resolve())
        self.assertEqual(manifest, self.manifest)
        self.assertEqual(file_sha256(self.root / "pytorch_model.bin"), self.manifest["source_files"]["pytorch_model.bin"])

    def test_source_and_artifact_corruption_or_missing_files_fail_closed(self):
        for name, target in (
            ("source", self.root / "pytorch_model.bin"),
            ("artifact", self.version / "model.onnx"),
        ):
            original = target.read_bytes()
            with self.subTest(name=name, action="corruption"):
                target.write_bytes(original + b"corrupt")
                with self.assertRaisesRegex(ValueError, "(?i)(hash|sha256|checksum)"):
                    self.read_manifest()
                target.write_bytes(original)
            with self.subTest(name=name, action="missing"):
                target.unlink()
                with self.assertRaisesRegex(ValueError, "(?i)(missing|file)"):
                    self.read_manifest()
                target.write_bytes(original)

    def test_external_data_cannot_be_unlisted(self):
        external_data = self.version / "model.onnx.data"
        external_data.write_bytes(b"a separately exported tensor")
        with self.assertRaisesRegex(ValueError, "(?i)(unlisted|untracked|artifact_files|manifest)"):
            self.read_manifest()
        self.manifest["artifact_files"][external_data.name] = _digest(external_data)
        _write_manifest(self.root, self.manifest)
        self.assertEqual(self.read_manifest()[0], self.version.resolve())

    def test_real_external_tensor_data_is_verified_and_executable(self):
        _write_model(self.version / "model.onnx", external_data=True)
        self.manifest["artifact_files"] = {path.name: _digest(path) for path in self.version.iterdir()}
        _write_manifest(self.root, self.manifest)
        self.assertIn("model.onnx.data", self.manifest["artifact_files"])
        self.assertEqual(self.encoder().encode(["A", "b c"]).shape, (2, 2))
        (self.version / "model.onnx.data").write_bytes(b"corrupted external tensor")
        with self.assertRaisesRegex(ValueError, "(?i)(hash|sha256|checksum)"):
            self.encoder()

    def test_tokenization_and_normalization_validation_are_mandatory(self):
        for field, value in (("tokenization_equal", False), ("max_norm_error", 1e-4)):
            for missing in (True, False):
                with self.subTest(field=field, missing=missing):
                    manifest = copy.deepcopy(self.manifest)
                    if missing:
                        del manifest["validation"][field]
                    else:
                        manifest["validation"][field] = value
                    _write_manifest(self.root, manifest)
                    with self.assertRaisesRegex(ValueError, "(?i)(tokenization|norm|validation)"):
                        self.read_manifest()

    def test_unsafe_paths_are_rejected(self):
        for unsafe in ("../escape", "/absolute", "C:/absolute", "C:relative", "\\\\server\\share", "nested/../../escape"):
            with self.subTest(unsafe=unsafe):
                manifest = copy.deepcopy(self.manifest)
                manifest["artifact_dir"] = unsafe
                _write_manifest(self.root, manifest)
                with self.assertRaisesRegex(ValueError, "(?i)(path|relative|escape|directory)"):
                    self.read_manifest()
        for field in ("source_files", "artifact_files"):
            manifest = copy.deepcopy(self.manifest)
            manifest[field]["../escape"] = "0" * 64
            _write_manifest(self.root, manifest)
            with self.assertRaisesRegex(ValueError, "(?i)(path|relative|escape)"):
                self.read_manifest()

    def test_unvalidated_incompatible_or_incomplete_manifests_are_rejected(self):
        cases = [
            ("schema_version", 2), ("contract", "mean-pooling"), ("dtype", "float16"),
            ("provider", "CUDAExecutionProvider"), ("opset", 16), ("dimension", 0),
            ("max_length", True), ("do_lower_case", True), ("pad_id", 0), ("pad_token", "[PAD]"),
            ("validation", {"passed": False, "max_abs_error": 0, "min_cosine": 1}),
            ("validation", {"passed": True, "max_abs_error": 0.001, "min_cosine": 1}),
            ("validation", {"passed": True, "max_abs_error": 0, "min_cosine": 0.99}),
            ("validation", {"passed": True, "max_abs_error": float("nan"), "min_cosine": 1}),
        ]
        for field, value in cases:
            with self.subTest(field=field, value=value):
                manifest = copy.deepcopy(self.manifest)
                manifest[field] = value
                _write_manifest(self.root, manifest)
                with self.assertRaises(ValueError):
                    self.read_manifest()
        for field, required in (("source_files", "pytorch_model.bin"), ("artifact_files", "model.onnx")):
            manifest = copy.deepcopy(self.manifest)
            del manifest[field][required]
            _write_manifest(self.root, manifest)
            with self.assertRaisesRegex(ValueError, "(?i)(required|missing|manifest)"):
                self.read_manifest()
        (self.root / "onnx-runtime" / "active.json").write_text("{invalid", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "(?i)(json|manifest)"):
            self.read_manifest()

    def test_runtime_rejects_bad_output_contract_instead_of_repairing(self):
        for options in (
            {"normalized": False}, {"output_dtype": "float64"}, {"output_name": "last_hidden_state"},
        ):
            with self.subTest(options=options):
                _write_model(self.version / "model.onnx", **options)
                self.manifest["artifact_files"]["model.onnx"] = _digest(self.version / "model.onnx")
                _write_manifest(self.root, self.manifest)
                with self.assertRaisesRegex((ValueError, RuntimeError), "(?i)(output|normalized|float32|contract)"):
                    self.encoder().encode(["A"], normalize_embeddings=False)

    def test_fresh_process_executes_ort_when_heavy_ml_imports_are_blocked(self):
        program = """
import importlib.abc
import sys
class BlockHeavyML(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname.split('.')[0] in {'torch', 'transformers', 'sentence_transformers'}:
            raise AssertionError('Forbidden heavy ML import: ' + fullname)
sys.meta_path.insert(0, BlockHeavyML())
from knowledge_service.integrations.onnx_embeddings import OnnxSentenceEncoder
encoder = OnnxSentenceEncoder(sys.argv[1])
result = encoder.encode(['A', 'b c'])
assert result.shape == (2, 2) and str(result.dtype) == 'float32'
assert not {'torch', 'transformers', 'sentence_transformers'}.intersection(sys.modules)
print('real ORT inference without heavy ML imports')
"""
        completed = subprocess.run(
            [sys.executable, "-c", program, str(self.root)], cwd=PROJECT_ROOT,
            capture_output=True, text=True, timeout=45,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertIn("real ORT inference without heavy ML imports", completed.stdout)


if __name__ == "__main__":
    unittest.main(verbosity=2)
