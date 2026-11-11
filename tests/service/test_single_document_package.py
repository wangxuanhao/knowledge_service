"""The upload boundary accepts exactly one logical document per ZIP."""
import base64
import io
import json
import stat
import zipfile
from pathlib import Path

import pytest

from knowledge_service.integrations.document_parser import ParsedDocument

PNG = base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAIAAACQd1PeAAAADElEQVR4nGP4z8AAAAMBAQDJ/pLvAAAAAElFTkSuQmCC')


@pytest.fixture(autouse=True)
def postgres_backend():
    """Package inspection is offline and never opens the shared database."""
    yield


def package(files, manifest=None):
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, 'w') as archive:
        if manifest is not None:
            archive.writestr('manifest.json', json.dumps(manifest, ensure_ascii=False))
        for name, value in files.items():
            archive.writestr(name, value)
    return stream.getvalue()


def reader(**kwargs):
    from knowledge_service.integrations.document_package import SingleDocumentPackageReader
    return SingleDocumentPackageReader(**kwargs)


def v2(**document):
    return {'schema_version': 2, 'document': document}


def test_markdown_and_referenced_assets_produce_one_document_without_loading_unused_members(monkeypatch):
    data = package({'rule.md': '# Rule\n![x](assets/p.png)', 'assets/p.png': PNG,
                    'assets/unreferenced.bin': b'unused'})
    original_read = zipfile.ZipFile.read
    reads = []
    def record_read(self, name, *args, **kwargs):
        reads.append(name.filename if isinstance(name, zipfile.ZipInfo) else name)
        return original_read(self, name, *args, **kwargs)
    monkeypatch.setattr(zipfile.ZipFile, 'read', record_read)

    result = reader().read(data, 'rules.zip')

    assert result['filename'] == 'rules.zip'
    assert len(result['documents']) == 1
    document = result['documents'][0]
    assert document['path'] == 'rule.md'
    assert document['assets'][0]['data'] == PNG
    assert document['assets'][0]['resolved_path'] == 'assets/p.png'
    assert document['assets'][0]['status'] == 'available'
    assert 'assets/unreferenced.bin' not in reads
    assert {'code': 'unused_package_file', 'path': 'assets/unreferenced.bin'} in result['warnings']


@pytest.mark.parametrize('files', [
    {'a.md': '# A', 'b.md': '# B'},
    {'assets/01.png': b'png', 'assets/02.png': b'png'},
    {'a.md': '# A', 'nested/other.md': '# B'},
])
def test_no_manifest_rejects_ambiguous_roots_and_unordered_images(files):
    with pytest.raises(ValueError, match='(?i)(one|single|main|主|一份)'):
        reader().read(package(files))


def test_v2_selects_explicit_main_and_preserves_metadata():
    metadata = {'platform': 'explicit', 'nested': {'v': [1, True]}}
    result = reader().read(package({'rule.md': '# Rule', 'other.md': '# Attachment'},
                                 v2(entry='rule.md', title='规则', metadata=metadata)))
    assert len(result['documents']) == 1
    document = result['documents'][0]
    assert document['path'] == 'rule.md'
    assert document['title'] == '规则'
    assert document['metadata'] == metadata
    assert document['parsed']['text'] == '# Rule'


def test_one_outer_directory_is_normalized_before_resolving_references():
    inner = v2(entry='正文.md')
    result = reader().read(package({'资料/manifest.json': json.dumps(inner, ensure_ascii=False),
                                   '资料/正文.md': '![图](assets/p.png)', '资料/assets/p.png': PNG}))
    assert result['root_prefix'] == '资料/'
    assert result['manifest_archive_path'] == '资料/manifest.json'
    assert result['documents'][0]['path'] == '正文.md'
    assert result['documents'][0]['primary_archive_path'] == '资料/正文.md'
    assert result['documents'][0]['assets'][0]['data'] == PNG


def test_legacy_single_entry_is_accepted_but_multiple_entries_are_rejected():
    result = reader().read(package({'a.md': '# A'}, {'documents': [{'path': 'a.md', 'title': 'A'}]}))
    assert result['documents'][0]['title'] == 'A'
    with pytest.raises(ValueError, match='(?i)(split|拆|single|一份)'):
        reader().read(package({'a.md': '# A', 'b.md': '# B'}, {'documents': ['a.md', 'b.md']}))


@pytest.mark.parametrize('manifest', [
    {'schema_version': 99, 'document': {'entry': 'a.md'}},
    v2(entry='a.md', pages=['assets/1.png']),
    v2(pages=[]), v2(pages=['../outside.png']), v2(entry='../a.md'),
    v2(pages=['assets/a.png', 'assets/a.png']),
    v2(entry='a.md', metadata=[]), v2(entry='a.md', title=''),
    {'schema_version': 2, 'documents': ['a.md']},
])
def test_rejects_unknown_or_malformed_manifests(manifest):
    with pytest.raises(ValueError):
        reader().read(package({'a.md': '# A'}, manifest))


def test_ordered_image_pages_have_stable_page_provenance_and_original_bytes():
    data = package({'assets/02.png': PNG, 'assets/01.png': PNG},
                   v2(title='截图', pages=['assets/02.png', 'assets/01.png']))
    document = reader().read(data)['documents'][0]
    assert len(document['assets']) == 2
    assert [asset['resolved_path'] for asset in document['assets']] == ['assets/02.png', 'assets/01.png']
    assert [asset['provenance']['page_no'] for asset in document['assets']] == [1, 2]
    assert all(asset['data'] == PNG for asset in document['assets'])
    assert document['provenance']['media_type'] == 'image_pages'
    assert document['text'] == ''
    assert document['parser_metadata']['page_count'] == 2
    assert len(document['parsed']['images']) == 2


@pytest.mark.parametrize('name', ['page.png', 'page#01.png', 'page?01.png', 'page%2301.png'])
def test_ordered_pages_preserve_literal_url_punctuation_in_archive_paths(name):
    path = f'assets/{name}'
    document = reader().read(package({path: PNG}, v2(pages=[path])))['documents'][0]

    asset = document['assets'][0]
    assert asset['status'] == 'available'
    assert asset['resolved_path'] == path
    assert asset['original_path'] == path
    assert asset['data'] == PNG
    assert asset['provenance']['page_no'] == 1
    assert document['parsed']['images'][0]['path'] == path
    assert document['warnings'] == []


def test_markdown_attachment_decodes_path_after_removing_actual_query_and_fragment():
    document = reader().read(package({
        'rule.md': '![figure](assets/page%2301%3Ffinal.png?download=1#view)',
        'assets/page#01?final.png': PNG,
    }))['documents'][0]

    asset = document['assets'][0]
    assert asset['status'] == 'available'
    assert asset['resolved_path'] == 'assets/page#01?final.png'
    assert asset['data'] == PNG


def test_non_markdown_primary_uses_injected_parser_and_normalizes_generated_media():
    calls = []
    class Parser:
        def parse(self, path, original_name, **kwargs):
            calls.append((Path(path), original_name, Path(path).read_bytes(), kwargs))
            return ParsedDocument('![figure](assets/figure.png)\n正文', {'document_parser': 'fake-pdf'},
                                  [{'path': 'assets/figure.png', 'data': PNG, 'mime_type': 'image/png',
                                    'provenance': {'page_no': 7}}], {'items': [{'page_no': 7}]})

    document = reader(parser=Parser()).read(package({'folder/rule.pdf': b'%PDF-original'},
                                                   v2(entry='folder/rule.pdf')))['documents'][0]

    assert calls[0][1:3] == ('rule.pdf', b'%PDF-original')
    assert not calls[0][0].exists()
    assert document['assets'][0]['status'] == 'available'
    assert document['assets'][0]['block_id']
    assert document['assets'][0]['provenance']['page_no'] == 7
    assert document['primary_data'] == b'%PDF-original'
    assert document['primary_filename'] == 'rule.pdf'
    assert document['parser_metadata']['document_parser'] == 'fake-pdf'


def test_html_local_media_are_frozen_and_external_urls_stay_external():
    data = package({'article.html': '<p>正文</p><img src="assets/p.png"><img src="https://example.invalid/a.png">',
                    'assets/p.png': PNG}, v2(entry='article.html'))
    document = reader().read(data)['documents'][0]
    assert document['assets'][0]['data'] == PNG
    assert document['assets'][1]['status'] == 'external_unfrozen'
    assert '正文' in document['parsed']['blocks'][0]['normalized_text']


@pytest.mark.parametrize('path', ['../bad.md', '/bad.md', 'C:/bad.md'])
def test_rejects_unsafe_zip_member_even_when_unreferenced(path):
    with pytest.raises(ValueError, match='(?i)unsafe'):
        reader().read(package({'rule.md': '# Rule', path: 'unsafe'}))


def test_rejects_symlinks_and_expansion_limits_before_parsing():
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, 'w') as archive:
        info = zipfile.ZipInfo('rule.md')
        info.external_attr = (stat.S_IFLNK | 0o777) << 16
        archive.writestr(info, 'target')
    with pytest.raises(ValueError, match='(?i)symlink'):
        reader().read(stream.getvalue())
    with pytest.raises(ValueError, match='(?i)limit'):
        reader(max_files=1).read(package({'rule.md': '# Rule', 'assets/unread': b'x'}))
    with pytest.raises(ValueError, match='(?i)limit'):
        reader(max_uncompressed_bytes=1).read(package({'rule.md': '# Rule'}))


def test_rejects_mismatched_referenced_image_bytes_and_preserves_missing_asset_warning():
    with pytest.raises(ValueError):
        reader().read(package({'rule.md': '![bad](assets/bad.png)', 'assets/bad.png': b'not image'}))
    document = reader().read(package({'rule.md': '![missing](assets/missing.png)'}))['documents'][0]
    assert document['assets'][0]['status'] == 'missing'
    assert document['warnings'][0]['code'] == 'source_asset_missing'


def test_media_only_main_keeps_empty_body_and_parser_asset():
    class Parser:
        def parse(self, path, original_name, **kwargs):
            return ParsedDocument('', {'document_parser': 'builtin-image'},
                                  [{'original_path': original_name, 'data': PNG, 'mime_type': 'image/png',
                                    'kind': 'local', 'status': 'available', 'provenance': {'media_type': 'image'}}],
                                  {'media_type': 'image'})
    document = reader(parser=Parser()).read(package({'image.png': PNG}))['documents'][0]
    assert document['text'] == ''
    assert document['assets'][0]['data'] == PNG
    assert len(document['parsed']['images']) == 1
    assert document['assets'][0]['block_id']
    assert document['provenance']['media_type'] == 'image'


def test_ordered_pages_reject_multiframe_png():
    from PIL import Image
    stream = io.BytesIO()
    frames = [Image.new('RGB', (2, 2), color=color) for color in ('red', 'blue')]
    frames[0].save(stream, format='PNG', save_all=True, append_images=frames[1:], duration=100)
    with pytest.raises(ValueError, match='(?i)(frame|animation|动画|多帧)'):
        reader().read(package({'assets/page.png': stream.getvalue()}, v2(pages=['assets/page.png'])))
