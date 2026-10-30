"""Validate activated embedding exports using only the Python standard library.

An absent activation permits the caller's original backend. An activation that
exists is a commitment to this export and must fail closed if it is invalid.
"""
from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path, PureWindowsPath
import re


_SOURCE_FILES = frozenset({
    "config.json", "modules.json", "sentence_bert_config.json",
    "1_Pooling/config.json", "tokenizer.json", "pytorch_model.bin",
})
_ARTIFACT_FILES = frozenset({"model.onnx", "tokenizer.json"})
_SHA256 = re.compile(r"[0-9a-f]{64}")
OPTIONAL_SOURCE_FILES = (
    "tokenizer_config.json", "special_tokens_map.json", "config_sentence_transformers.json",
)
UNSUPPORTED_SOURCE_FILES = (
    "model.safetensors", "model.safetensors.index.json", "pytorch_model.bin.index.json",
    "adapter_config.json", "adapter_model.safetensors", "adapter_model.bin", "added_tokens.json",
)


class EmbeddingArtifactError(ValueError):
    """The activated ONNX export is incomplete, incompatible, or modified."""


def file_sha256(path: str | Path) -> str:
    """Hash a possibly large model file without loading it into memory."""
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _relative_path(root: Path, value: object, field: str) -> Path:
    if not isinstance(value, str) or not value or "\\" in value or ":" in value or "\x00" in value:
        raise EmbeddingArtifactError(f"ONNX manifest {field} must be a safe relative path.")
    parts = value.split("/")
    if any(
        part in {"", ".", ".."} or part.rstrip(" .") != part or PureWindowsPath(part).is_reserved()
        for part in parts
    ):
        raise EmbeddingArtifactError(f"ONNX manifest {field} must be a safe relative path.")
    try:
        target = root.joinpath(*parts).resolve()
        target.relative_to(root)
    except (OSError, ValueError, RuntimeError):
        raise EmbeddingArtifactError(f"ONNX manifest {field} path escapes its directory.") from None
    return target


def _json_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise EmbeddingArtifactError("ONNX manifest contains duplicate JSON keys.")
        result[key] = value
    return result


def _validate_contract(manifest: dict) -> None:
    constants = {
        "schema_version": 1, "contract": "xlm-roberta-cls-normalized-v1",
        "dtype": "float32", "provider": "CPUExecutionProvider", "opset": 17,
        "do_lower_case": False, "pad_id": 1, "pad_token": "<pad>",
    }
    for field, expected in constants.items():
        value = manifest.get(field)
        if type(value) is not type(expected) or value != expected:
            raise EmbeddingArtifactError(f"ONNX manifest has an incompatible or missing {field}.")
    for field in ("dimension", "max_length"):
        value = manifest.get(field)
        if type(value) is not int or value <= 0:
            raise EmbeddingArtifactError(f"ONNX manifest {field} must be a positive integer.")
    validation = manifest.get("validation")
    if not isinstance(validation, dict) or validation.get("passed") is not True:
        raise EmbeddingArtifactError("ONNX manifest has no passed numerical validation.")
    if validation.get("tokenization_equal") is not True:
        raise EmbeddingArtifactError("ONNX manifest has no passed tokenization validation.")
    for field, minimum, maximum in (
        ("max_abs_error", 0, 1e-4), ("min_cosine", 0.99999, 1.000001), ("max_norm_error", 0, 1e-5),
    ):
        value = validation.get(field)
        if type(value) not in (float, int) or not math.isfinite(value) or not minimum <= value <= maximum:
            raise EmbeddingArtifactError(f"ONNX manifest validation {field} is outside the accepted range.")


def _verify_files(root: Path, values: object, required: frozenset[str], field: str) -> None:
    if not isinstance(values, dict) or not required.issubset(values):
        raise EmbeddingArtifactError(f"ONNX manifest {field} is missing required files.")
    for relative, expected in values.items():
        path = _relative_path(root, relative, field)
        if not isinstance(expected, str) or _SHA256.fullmatch(expected) is None:
            raise EmbeddingArtifactError(f"ONNX manifest {field} contains an invalid SHA256.")
        if not path.is_file():
            raise EmbeddingArtifactError(f"ONNX manifest {field} file is missing: {relative}")
        try:
            actual = file_sha256(path)
        except OSError:
            raise EmbeddingArtifactError(f"ONNX manifest {field} file cannot be read: {relative}") from None
        if actual != expected:
            raise EmbeddingArtifactError(f"ONNX manifest {field} SHA256 mismatch: {relative}")


def _verify_source_presence(root: Path, files: object) -> None:
    if not isinstance(files, dict):
        raise EmbeddingArtifactError("ONNX source_files is missing.")
    for name in UNSUPPORTED_SOURCE_FILES:
        path = root / name
        if path.exists() or path.is_symlink():
            raise EmbeddingArtifactError("ONNX source weights or tokenizer selection changed; prepare a supported export again.")
    for name in OPTIONAL_SOURCE_FILES:
        path = root / name
        if (path.exists() or path.is_symlink()) != (name in files):
            raise EmbeddingArtifactError(f"ONNX source file presence changed: {name}")


def read_export_manifest(source_root: str | Path) -> tuple[Path, dict] | None:
    """Read one activation snapshot and fully validate its source and artifacts.

    Call once during encoder initialization, outside the request path. Version
    directories are immutable; all files, including external ONNX tensor data,
    must be listed and hashed in ``artifact_files``.
    """
    source_root = Path(source_root).resolve()
    runtime_root = _relative_path(source_root, "onnx-runtime", "runtime directory")
    active = runtime_root / "active.json"
    if not active.exists() and not active.is_symlink():
        return None
    active = _relative_path(runtime_root, "active.json", "activation")
    try:
        manifest = json.loads(active.read_text(encoding="utf-8"), object_pairs_hook=_json_object)
    except (OSError, UnicodeError, json.JSONDecodeError):
        raise EmbeddingArtifactError("The activated ONNX export manifest is not readable valid JSON.") from None
    if not isinstance(manifest, dict):
        raise EmbeddingArtifactError("The activated ONNX export manifest must be a JSON object.")
    _validate_contract(manifest)
    artifact_dir = _relative_path(runtime_root, manifest.get("artifact_dir"), "artifact_dir")
    if not artifact_dir.is_dir():
        raise EmbeddingArtifactError("ONNX manifest artifact directory is missing.")
    _verify_source_presence(source_root, manifest.get("source_files"))
    _verify_files(source_root, manifest.get("source_files"), _SOURCE_FILES, "source_files")
    _verify_files(artifact_dir, manifest.get("artifact_files"), _ARTIFACT_FILES, "artifact_files")
    try:
        for path in artifact_dir.rglob("*"):
            relative = path.relative_to(artifact_dir).as_posix()
            _relative_path(artifact_dir, relative, "artifact_files")
            if path.is_symlink() or path.is_junction():
                raise EmbeddingArtifactError("ONNX artifact directory must not contain linked paths.")
            if path.is_file() and relative not in manifest["artifact_files"]:
                raise EmbeddingArtifactError("ONNX artifact_files omits an unlisted artifact file.")
    except OSError:
        raise EmbeddingArtifactError("The ONNX artifact directory cannot be inspected.") from None
    return artifact_dir, manifest
