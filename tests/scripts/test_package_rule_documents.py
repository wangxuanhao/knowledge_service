"""Run the local package command through its public CLI and current ZIP reader."""
import base64
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import zipfile

import pytest

from knowledge_service.integrations.markdown_package import MarkdownPackageReader


SCRIPT = Path(__file__).resolve().parents[2] / 'scripts' / 'package_rule_documents.py'
PNG = base64.b64decode(
    'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAIAAACQd1PeAAAADElEQVR4nGP4z8AAAAMB'
    'AQDJ/pLvAAAAAElFTkSuQmCC'
)


@pytest.fixture(autouse=True)
def postgres_backend():
    """This offline command never opens a repository or database."""
    yield


def put(root, name, data):
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data.encode('utf-8') if isinstance(data, str) else data)
    return path


def cli(root, output, *args):
    assert SCRIPT.is_file(), 'package_rule_documents.py CLI is missing'
    env = dict(os.environ, PYTHONIOENCODING='utf-8')
    return subprocess.run(
        [sys.executable, str(SCRIPT), '--root', str(root), '--output-dir', str(output), *args],
        cwd=root.parent, env=env, capture_output=True, text=True, encoding='utf-8', timeout=40,
    )


def report(output):
    return json.loads((output / 'packaging-report.json').read_text(encoding='utf-8'))


def assert_failed(result, output, *details):
    assert result.returncode == 2, result.stdout + result.stderr
    for detail in details:
        assert detail.lower() in result.stderr.lower(), result.stderr
    assert not output.exists()
    assert not list(output.parent.glob(f'.{output.name}-*'))


def test_partial_selection_preserves_original_bytes_resolves_images_and_deduplicates(tmp_path):
    root, output = tmp_path / 'sources', tmp_path / 'packages'
    original = ('# 规则\r\n\r\n![standard](../图像/a%20b.png?raw=1#x)\r\n'
                '<table><tr><td><img src="../图像/a b.png"></td></tr></table>\r\n\r\n'
                '![reference][figure]\r\n\r\n[figure]: ../图像/a%20b.png\r\n'
                '![external](https://example.invalid/frozen.png)\r\n'
                f'![inline](data:image/png;base64,{base64.b64encode(PNG).decode()})\r\n').encode('utf-8-sig')
    put(root, '分类/one.md', original)
    put(root, 'two.md', '![shared](图像/a%20b.png)')
    put(root, 'unselected.md', '![missing](missing.png)')
    put(root, '图像/a b.png', PNG)
    put(root, '图像/unselected.png', PNG)
    selected = put(tmp_path, 'selected.txt', '# comment\n\n分类/one.md\ntwo.md\n分类/one.md\n'.encode('utf-8-sig'))
    before = {p.relative_to(root).as_posix(): p.read_bytes() for p in root.rglob('*') if p.is_file()}

    result = cli(root, output, '--list-file', str(selected), '--file', './two.md')

    assert result.returncode == 0, result.stderr
    archives = list(output.glob('*.zip'))
    assert len(archives) == 1
    with zipfile.ZipFile(archives[0]) as archive:
        assert set(archive.namelist()) == {'manifest.json', '分类/one.md', 'two.md', '图像/a b.png'}
        assert archive.read('分类/one.md') == original
        assert all(info.compress_type == zipfile.ZIP_STORED for info in archive.infolist())
    parsed = MarkdownPackageReader().read(archives[0].read_bytes())
    assert [doc['path'] for doc in parsed['documents']] == ['分类/one.md', 'two.md']
    assert len(parsed['documents'][0]['assets']) == 5
    assert all(asset['status'] == 'available' for doc in parsed['documents']
               for asset in doc['assets'] if asset['kind'] in {'local', 'base64'})
    receipt = report(output)
    package = receipt['packages'][0]
    assert package['documents'] == ['分类/one.md', 'two.md']
    assert package['images'] == ['图像/a b.png']
    assert package['size_bytes'] == archives[0].stat().st_size
    assert package['sha256'] == hashlib.sha256(archives[0].read_bytes()).hexdigest()
    assert receipt['warnings'] == [{'document': '分类/one.md', 'url': 'https://example.invalid/frozen.png',
                                    'code': 'external_image'}]
    assert {entry['path']: entry['sha256'] for entry in receipt['files']} == {
        name: hashlib.sha256(before[name]).hexdigest() for name in ('分类/one.md', 'two.md', '图像/a b.png')
    }
    assert before == {p.relative_to(root).as_posix(): p.read_bytes() for p in root.rglob('*') if p.is_file()}


def test_selectors_follow_command_line_order_and_manifest_preserves_explicit_metadata(tmp_path):
    root = tmp_path / 'sources'
    put(root, 'a.md', '# A')
    put(root, 'b.md', '# B')
    selected = put(tmp_path, 'selected.txt', 'b.md\n')
    output = tmp_path / 'ordered'
    result = cli(root, output, '--list-file', str(selected), '--file', 'a.md', '--file', 'b.md')
    assert result.returncode == 0, result.stderr
    assert report(output)['packages'][0]['documents'] == ['b.md', 'a.md']

    manifest = put(tmp_path, 'input.json', json.dumps({'documents': [
        {'path': 'a.md', 'title': '原样标题', 'metadata': {'platform': 'explicit', 'nested': {'x': [1, True]}}},
        {'path': './a.md'}, 'b.md',
    ]}, ensure_ascii=False).encode('utf-8-sig'))
    output = tmp_path / 'manifest-packages'
    result = cli(root, output, '--manifest', str(manifest))
    assert result.returncode == 0, result.stderr
    package = MarkdownPackageReader().read(next(output.glob('*.zip')).read_bytes())
    assert package['manifest']['documents'] == [
        {'path': 'a.md', 'title': '原样标题', 'metadata': {'platform': 'explicit', 'nested': {'x': [1, True]}}},
        {'path': 'b.md'},
    ]


@pytest.mark.parametrize(('reference', 'detail'), [
    ('missing.png', 'missing'), ('asset.svg', 'unsupported'), ('../../outside.png', 'unsafe'),
    ('/absolute.png', 'unsafe'), ('file:///outside.png', 'unsafe'), ('bad%00.png', 'unsafe'),
])
def test_rejects_missing_unsupported_or_unsafe_images_without_publishing(tmp_path, reference, detail):
    root, output = tmp_path / 'sources', tmp_path / 'packages'
    put(root, 'folder/rule.md', f'<img src="{reference}">')
    put(root, 'folder/asset.svg', '<svg/>')
    put(tmp_path, 'outside.png', PNG)
    result = cli(root, output, '--file', 'folder/rule.md')
    assert_failed(result, output, 'folder/rule.md', reference, detail)


@pytest.mark.parametrize('selection', ['../outside.md', '/absolute.md', 'C:/absolute.md', 'missing.md', 'wrong.txt'])
def test_rejects_invalid_document_selection(tmp_path, selection):
    root, output = tmp_path / 'sources', tmp_path / 'packages'
    put(root, 'rule.md', '# rule')
    put(root, 'wrong.txt', '# wrong')
    put(tmp_path, 'outside.md', '# outside')
    assert_failed(cli(root, output, '--file', selection), output, selection)


@pytest.mark.parametrize(('flag', 'value'), [
    ('--max-documents', '0'), ('--max-documents', '1001'), ('--max-documents', 'x'),
    ('--max-zip-bytes', '0'), ('--max-zip-bytes', '25000001'),
])
def test_rejects_invalid_limits(tmp_path, flag, value):
    root, output = tmp_path / 'sources', tmp_path / 'packages'
    put(root, 'rule.md', '# rule')
    assert_failed(cli(root, output, '--file', 'rule.md', flag, value), output, flag)


def test_rejects_existing_output_source_output_and_mixed_selection(tmp_path):
    root = tmp_path / 'sources'
    put(root, 'rule.md', '# rule')
    output = tmp_path / 'existing'
    output.mkdir()
    marker = put(output, 'keep.txt', 'keep')
    result = cli(root, output, '--file', 'rule.md')
    assert result.returncode == 2 and 'exist' in result.stderr.lower()
    assert marker.read_text() == 'keep'
    output = root / 'packages'
    assert_failed(cli(root, output, '--file', 'rule.md'), output, 'root')
    manifest = put(tmp_path, 'manifest.json', '{"documents":["rule.md"]}')
    output = tmp_path / 'mixed'
    assert_failed(cli(root, output, '--manifest', str(manifest), '--file', 'rule.md'), output, 'manifest')


@pytest.mark.parametrize('entries', [[{'path': 'rule.md', 'metadata': []}], [{'path': 'rule.md', 'title': None}], []])
def test_rejects_bad_manifest(tmp_path, entries):
    root, output = tmp_path / 'sources', tmp_path / 'packages'
    put(root, 'rule.md', '# rule')
    manifest = put(tmp_path, 'manifest.json', json.dumps({'documents': entries}))
    assert_failed(cli(root, output, '--manifest', str(manifest)), output)


def test_greedy_split_obeys_document_count_and_exact_stored_zip_bytes(tmp_path):
    root = tmp_path / 'sources'
    for name in ('a.md', 'b.md', 'c.md'):
        put(root, name, '# rule\n' + 'x' * 500)
    selected = ['--file', 'a.md', '--file', 'b.md', '--file', 'c.md']
    one = tmp_path / 'one'
    result = cli(root, one, *selected, '--max-documents', '1')
    assert result.returncode == 0, result.stderr
    single_size = report(one)['packages'][0]['size_bytes']
    assert len(report(one)['packages']) == 3
    counted = tmp_path / 'counted'
    result = cli(root, counted, *selected, '--max-documents', '2')
    assert result.returncode == 0, result.stderr
    assert [p['documents'] for p in report(counted)['packages']] == [['a.md', 'b.md'], ['c.md']]
    bounded = tmp_path / 'bounded'
    result = cli(root, bounded, *selected, '--max-zip-bytes', str(single_size))
    assert result.returncode == 0, result.stderr
    assert len(report(bounded)['packages']) == 3
    assert all(p['size_bytes'] == single_size for p in report(bounded)['packages'])
    failed = tmp_path / 'too-small'
    assert_failed(cli(root, failed, *selected, '--max-zip-bytes', str(single_size - 1)), failed, 'a.md', 'limit')


def test_failure_after_staging_first_package_publishes_no_finalized_outputs(tmp_path):
    root, output = tmp_path / 'sources', tmp_path / 'packages'
    put(root, 'good.md', '# good')
    put(root, 'bad.md', '![missing](missing.png)')
    assert_failed(cli(root, output, '--file', 'good.md', '--file', 'bad.md', '--max-documents', '1'),
                  output, 'bad.md', 'missing.png')


@pytest.mark.parametrize('bad_content', ['# rule\n' + 'x' * 1_000_001, '![x](x.png)\n' * 201, '   \n'],
                         ids=['text-limit', 'image-count', 'empty-text'])
def test_rejects_service_text_and_image_limits(tmp_path, bad_content):
    root, output = tmp_path / 'sources', tmp_path / 'packages'
    put(root, 'rule.md', bad_content)
    put(root, 'x.png', PNG)
    assert_failed(cli(root, output, '--file', 'rule.md'), output, 'rule.md')


def test_invalid_image_bytes_fail_before_the_service_could_drop_them(tmp_path):
    root, output = tmp_path / 'sources', tmp_path / 'packages'
    put(root, 'rule.md', '![broken](broken.png)')
    put(root, 'broken.png', b'not an image')
    assert_failed(cli(root, output, '--file', 'rule.md'), output, 'rule.md', 'broken.png')


@pytest.mark.parametrize('link_kind', ['file', 'directory'])
def test_rejects_symlink_files_and_parent_directories(tmp_path, link_kind):
    root, output = tmp_path / 'sources', tmp_path / 'packages'
    put(root, 'real/image.png', PNG)
    link = root / ('linked.png' if link_kind == 'file' else 'linked')
    try:
        link.symlink_to(root / ('real/image.png' if link_kind == 'file' else 'real'),
                        target_is_directory=link_kind == 'directory')
    except OSError as exc:
        pytest.skip(f'System does not permit symlinks: {exc}')
    reference = 'linked.png' if link_kind == 'file' else 'linked/image.png'
    put(root, 'rule.md', f'![linked]({reference})')
    assert_failed(cli(root, output, '--file', 'rule.md'), output, 'rule.md', reference, 'symlink')
