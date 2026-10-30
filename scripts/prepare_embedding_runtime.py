"""Offline export/validation; does not connect to business storage or edit .env."""
import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from knowledge_service.integrations.embedding_export import prepare_onnx_runtime
from knowledge_service.utils.diagnostics import redact


def main():
    parser = argparse.ArgumentParser(description='准备并验证本地 BGE-M3 的轻量 ONNX 推理制品')
    parser.add_argument('--model-dir', type=Path, required=True, help='现有模型目录；保留原始权重')
    parser.add_argument('--rebuild', action='store_true', help='创建新版本并验证后原子启用，保留旧版本')
    args = parser.parse_args()
    try:
        result = prepare_onnx_runtime(args.model_dir, rebuild=args.rebuild,
            progress=lambda event: print(json.dumps(event, ensure_ascii=True), flush=True))
    except Exception as exc:
        print(json.dumps({'error': redact(exc)}, ensure_ascii=True), file=sys.stderr)
        return 1
    print(json.dumps({'status': 'ready', 'artifact_dir': result['artifact_dir'],
                      'validation': result['validation']}, ensure_ascii=True), flush=True)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
