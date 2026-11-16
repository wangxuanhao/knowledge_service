"""Build the current workflow manual with embedded figures and a stable alias."""
from __future__ import annotations

import importlib.util
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / 'docs/知识平台完整操作手册.html'
MARKDOWN = ROOT / 'docs/知识平台操作手册.md'


def _renderer():
    spec = importlib.util.spec_from_file_location('current_manual_renderer', ROOT/'scripts/build_standalone_manual.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def render_manual() -> str:
    renderer = _renderer()
    output = renderer.build(MARKDOWN, embed_images=True)
    return output.read_text(encoding='utf-8')


def main() -> int:
    built = render_manual()
    OUTPUT.write_text(built, encoding='utf-8', newline='\n')
    print('PASS: current manual and compatibility entry built: ' + str(OUTPUT))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
