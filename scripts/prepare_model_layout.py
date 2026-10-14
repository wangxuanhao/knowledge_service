"""Copy a verified local embedding model, then change only its path and identity."""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import sys

# Scripts must use this checkout, even when another worktree is installed editable.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from dotenv import set_key

from knowledge_service.integrations.embeddings import LocalEncoder


def digest(path):
    result = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            result.update(block)
    return result.hexdigest()


def prepare(source, target, *, env_path=None, verify_encoding=False):
    source, target = source.resolve(), target.resolve()
    if source == target:
        raise ValueError('Source and target must differ')
    modules = json.loads((source / 'modules.json').read_text(encoding='utf-8'))
    files = list(p for p in source.iterdir() if p.is_file())
    for module in modules:
        relative = module.get('path')
        if not relative:
            continue
        path = (source / relative).resolve()
        if not path.is_relative_to(source):
            raise ValueError('Module path escapes model root')
        if path.is_dir():
            files.extend(p for p in path.rglob('*') if p.is_file())
    target.mkdir(parents=True, exist_ok=True)
    for path in files:
        destination = target / path.relative_to(source)
        destination.parent.mkdir(parents=True, exist_ok=True)
        if not destination.exists() or digest(destination) != digest(path):
            if destination.exists():
                raise ValueError('Target has conflicting model files')
            shutil.copy2(path, destination)
        if digest(destination) != digest(path):
            raise ValueError('Copied model checksum mismatch')
    identity = LocalEncoder(source).identity
    encoder = LocalEncoder(target, identity=identity)
    if verify_encoding:
        vector = encoder.encode(['模型目录迁移验证'])[0]
        if len(vector) != encoder.dimension:
            raise ValueError('Embedding dimension validation failed')
    if env_path is not None:
        if not verify_encoding:
            raise ValueError('Updating configuration requires verified encoding')
        # Keep every unrelated setting and retain the old object files for rollback.
        set_key(str(env_path), 'KG_EMBEDDING_PATH', target.as_posix(), quote_mode='never')
        set_key(str(env_path), 'KG_EMBEDDING_IDENTITY', identity, quote_mode='never')
    return {'files': len(files), 'dimension': encoder.dimension, 'target': str(target),
            'encoding_verified': verify_encoding, 'previous_files_retained': True}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, default=Path('data/model'))
    parser.add_argument('--target', type=Path, default=Path('data/model/embedding/bge-m3'))
    parser.add_argument('--env', type=Path)
    parser.add_argument('--verify-encoding', action='store_true')
    args = parser.parse_args()
    print(json.dumps(prepare(args.source, args.target, env_path=args.env,
                            verify_encoding=args.verify_encoding), ensure_ascii=False))
