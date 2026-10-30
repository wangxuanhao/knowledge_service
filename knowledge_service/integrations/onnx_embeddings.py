"""CPU BGE-M3 inference without importing Torch, Transformers, or ST.

The offline export owns XLM-RoBERTa, CLS pooling, and L2 normalization. This
adapter only tokenizes text and executes that verified sentence-embedding graph.
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import onnxruntime as ort
from tokenizers import Tokenizer

from .embedding_artifacts import EmbeddingArtifactError, read_export_manifest


class OnnxSentenceEncoder:
    """A local-only subset of SentenceTransformer used by ``LocalEncoder``."""

    def __init__(self, path: str | Path, *, local_files_only: bool = True):
        # This adapter never uses a model hub, including when called by older
        # callers that pass local_files_only=False.
        export = read_export_manifest(path)
        if export is None:
            raise EmbeddingArtifactError("No activated ONNX embedding export was found.")
        artifact_dir, self.manifest = export
        self._dimension = self.manifest["dimension"]
        self._max_length = self.manifest["max_length"]
        self._pad_id = self.manifest["pad_id"]
        self._tokenizer = Tokenizer.from_file(str(artifact_dir / "tokenizer.json"))
        self._tokenizer.enable_truncation(
            max_length=self._max_length, strategy="longest_first", direction="right",
        )
        # Padding each runtime batch avoids a single long document padding all
        # documents in an encode request to the same expensive sequence length.
        self._tokenizer.no_padding()
        options = ort.SessionOptions()
        options.intra_op_num_threads = 4
        options.inter_op_num_threads = 1
        self._session = ort.InferenceSession(
            str(artifact_dir / "model.onnx"), sess_options=options,
            providers=["CPUExecutionProvider"],
        )
        self._check_session_contract()

    def _check_session_contract(self) -> None:
        inputs = self._session.get_inputs()
        if (
            {node.name for node in inputs} != {"input_ids", "attention_mask"}
            or any(node.type != "tensor(int64)" or len(node.shape) != 2 for node in inputs)
        ):
            raise EmbeddingArtifactError("ONNX embedding input contract requires two rank-2 int64 token tensors.")
        outputs = self._session.get_outputs()
        if len(outputs) != 1 or outputs[0].name != "sentence_embedding":
            raise EmbeddingArtifactError("ONNX output contract requires sentence_embedding only.")
        output = outputs[0]
        if output.type != "tensor(float)" or len(output.shape) != 2:
            raise EmbeddingArtifactError("ONNX sentence_embedding output must be rank-2 float32.")
        if isinstance(output.shape[1], int) and output.shape[1] != self._dimension:
            raise EmbeddingArtifactError("ONNX sentence_embedding output dimension differs from its manifest.")
        if self._session.get_providers() != ["CPUExecutionProvider"]:
            raise EmbeddingArtifactError("ONNX embedding runtime must use CPUExecutionProvider.")

    def get_sentence_embedding_dimension(self) -> int:
        return self._dimension

    def encode(self, texts, normalize_embeddings: bool = True, batch_size: int = 4) -> np.ndarray:
        """Return float32 unit vectors in input order, including an empty batch.

        Normalization is part of the export contract, even when the compatibility
        argument ``normalize_embeddings`` is false. Invalid graph output is
        rejected, never repaired by normalizing or casting it at runtime.
        """
        if type(batch_size) is not int or batch_size <= 0:
            raise ValueError("ONNX embedding batch_size must be a positive integer.")
        encodings = [self._tokenizer.encode(str(text).strip()) for text in texts]
        result = np.empty((len(encodings), self._dimension), dtype=np.float32)
        order = sorted(range(len(encodings)), key=lambda index: len(encodings[index].ids))
        cursor = 0
        while cursor < len(order):
            limit = 1 if len(encodings[order[cursor]].ids) > 1024 else batch_size
            batch = []
            while cursor < len(order) and len(batch) < limit:
                index = order[cursor]
                if batch and len(encodings[index].ids) > 1024:
                    break
                batch.append(index)
                cursor += 1
            sequence_length = max(len(encodings[index].ids) for index in batch)
            input_ids = np.full((len(batch), sequence_length), self._pad_id, dtype=np.int64)
            attention_mask = np.zeros_like(input_ids)
            for row, index in enumerate(batch):
                encoding = encodings[index]
                length = len(encoding.ids)
                input_ids[row, :length] = encoding.ids
                attention_mask[row, :length] = encoding.attention_mask
            vectors = self._session.run(
                ["sentence_embedding"], {"input_ids": input_ids, "attention_mask": attention_mask},
            )[0]
            if (
                not isinstance(vectors, np.ndarray) or vectors.dtype != np.float32
                or vectors.shape != (len(batch), self._dimension) or not np.isfinite(vectors).all()
            ):
                raise EmbeddingArtifactError("ONNX sentence_embedding output has invalid float32 shape or finite values.")
            norms = np.linalg.norm(vectors, axis=1)
            if not np.allclose(norms, 1.0, rtol=1e-4, atol=1e-5):
                raise EmbeddingArtifactError("ONNX sentence_embedding output is not L2 normalized.")
            result[batch] = vectors
        return result
