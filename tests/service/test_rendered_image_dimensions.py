"""Rendering reserves image layout from stored dimensions, including legacy rows."""
from types import SimpleNamespace

from knowledge_service.services.source_documents import SourceDocumentService


def test_rendered_image_uses_only_positive_bounded_stored_dimensions():
    asset = dict(id='image', original_path='chart.png', alt_text='chart', kind='local',
                 source_object_id='object', width=960, height=320)
    version = dict(assets=[asset], blocks=[dict(payload={'images': [{'path': 'chart.png'}]})],
                   rendered_html='<p data-block-id="b"><span class="source-image" data-image-index="0">chart</span></p>')
    service = SimpleNamespace(get=lambda project, version_id: version)
    render = lambda: SourceDocumentService.rendered(service, 'p', 'v')['html']
    assert 'width="960" height="320"' in render()
    for width, height in [(None, None), (-1, 50), (50, 0), ('960', 320), (True, 320), (1_000_001, 320)]:
        asset.update(width=width, height=height)
        assert ' width=' not in render()
        assert ' height=' not in render()
