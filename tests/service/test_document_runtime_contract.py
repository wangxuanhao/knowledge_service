from importlib import metadata
from pathlib import Path
import tomllib

import pytest


ROOT = Path(__file__).resolve().parents[2]


def test_semantica_runtime_extra_pins_parser_stack():
    project = tomllib.loads((ROOT / 'pyproject.toml').read_text(encoding='utf-8'))
    dependencies = project['project']['optional-dependencies']['semantica-runtime']

    assert 'semantica[parse-docling]>=0.7,<0.8' in dependencies
    assert 'docling>=2.130,<3' in dependencies


def test_installed_parser_stack_meets_declared_floor():
    pytest.importorskip('semantica')
    pytest.importorskip('docling')
    from semantica.parse import DoclingParser

    assert DoclingParser is not None
    assert tuple(map(int, metadata.version('semantica').split('.')[:2])) >= (0, 7)
    assert tuple(map(int, metadata.version('docling').split('.')[:2])) >= (2, 130)


def test_disabled_ocr_overrides_docling_default():
    pytest.importorskip('semantica')
    pytest.importorskip('docling')
    from docling.datamodel.base_models import InputFormat
    from knowledge_service.integrations.document_parser import DocumentParser

    parser = DocumentParser(ocr_mode='disabled')._new_docling_parser()

    options = parser._converter.format_to_options[InputFormat.PDF].pipeline_options
    assert options.do_ocr is False
