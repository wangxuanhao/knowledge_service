"""Build and statically verify the current offline operation manual."""
from __future__ import annotations

import importlib.util
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / 'docs/知识平台完整操作手册.html'


def _renderer():
    spec = importlib.util.spec_from_file_location('current_manual_renderer', ROOT/'scripts/verify_redesign_manual.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def render_manual() -> str:
    renderer = _renderer()
    return renderer.build_html(renderer.MARKDOWN.read_text(encoding='utf-8-sig'))


def main() -> int:
    renderer = _renderer()
    source = renderer.MARKDOWN.read_text(encoding='utf-8-sig')
    built = renderer.build_html(source)
    errors = renderer.verify(source, built)
    if errors:
        for error in errors:
            print('FAIL: ' + error)
        return 1
    OUTPUT.write_text(built, encoding='utf-8', newline='\n')
    print('PASS: current manual built and statically checked: ' + str(OUTPUT))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
