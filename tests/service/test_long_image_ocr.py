"""Long-image OCR preserves source geometry and resumable coverage.

The scene engine reads visible marker pixels, so losing tiny source details or
forgetting a crop offset changes the evidence returned by the real adapter.
Actual text recognition is validated separately with the installed OCR models.
"""
import io
from copy import deepcopy
from types import SimpleNamespace

import numpy as np
import pytest
from PIL import Image, ImageDraw

from knowledge_service.integrations.image_ocr import OCRError, RapidOCRAdapter


def scene(size, markers, *, orientation=None):
    image = Image.new('RGB', size, 'white')
    draw = ImageDraw.Draw(image)
    for shade, _, (left, top, right, bottom) in markers:
        draw.rectangle((left, top, right - 1, bottom - 1), fill=(shade,) * 3)
    output = io.BytesIO()
    if orientation is None:
        image.save(output, format='PNG')
    else:
        exif = Image.Exif()
        exif[274] = orientation
        image.save(output, format='PNG', exif=exif)
    return output.getvalue()


class SceneEngine:
    def __init__(self, markers, *, fail_calls=()):
        self.labels = {shade: text for shade, text, _ in markers}
        self.fail_calls = set(fail_calls)
        self.sizes = []

    def __call__(self, pixels):
        self.sizes.append((pixels.shape[1], pixels.shape[0]))
        if len(self.sizes) in self.fail_calls:
            raise RuntimeError('local tile inference failed')
        boxes, texts, scores = [], [], []
        for shade, text in self.labels.items():
            ys, xs = np.where(np.all(pixels == shade, axis=2))
            # A 12px source line shrunk to one pixel is deliberately unreadable.
            if not len(xs) or ys.max() - ys.min() + 1 < 6:
                continue
            left, right = int(xs.min()), int(xs.max()) + 1
            top, bottom = int(ys.min()), int(ys.max()) + 1
            boxes.append([[left, top], [right, top], [right, bottom], [left, bottom]])
            texts.append(text)
            scores.append(.95)
        return SimpleNamespace(boxes=boxes or None, txts=texts or None, scores=scores or None)


def adapter_for(monkeypatch, engine, **options):
    monkeypatch.setattr(RapidOCRAdapter, 'configuration_fingerprint', property(lambda self: 'test-config'))
    adapter = RapidOCRAdapter(**options)
    adapter._engine = engine
    return adapter


def assert_box(block, region, size):
    left, top, right, bottom = region
    width, height = size
    assert block['x'] == pytest.approx(left / width, abs=1 / width)
    assert block['y'] == pytest.approx(top / height, abs=1 / height)
    assert block['width'] == pytest.approx((right - left) / width, abs=1 / width)
    assert block['height'] == pytest.approx((bottom - top) / height, abs=1 / height)
    assert block['x'] + block['width'] <= 1
    assert block['y'] + block['height'] <= 1


def assert_complete_coverage(tiles, size):
    width, height = size
    assert tiles and all(tile['status'] == 'completed' for tile in tiles)
    # Evaluate each rectangle boundary interval, including the last source row
    # and column, so a tiny uncovered seam cannot hide between samples.
    xs = sorted({0, width, *(x for tile in tiles for x in tile['region'][::2])})
    ys = sorted({0, height, *(y for tile in tiles for y in tile['region'][1::2])})
    for x in xs[:-1]:
        for y in ys[:-1]:
            assert any(left <= x < right and top <= y < bottom
                       for left, top, right, bottom in (tile['region'] for tile in tiles))


@pytest.mark.parametrize('size,regions', [
    ((1080, 20000), [(90, 80, 250, 92), (90, 9950, 250, 9962), (90, 19900, 250, 19912)]),
    ((20000, 1080), [(80, 90, 240, 102), (9950, 90, 10110, 102), (19800, 90, 19960, 102)]),
])
def test_long_image_preserves_narrow_text_and_covers_original(monkeypatch, size, regions):
    markers = [(30 + index * 40, text, region)
               for index, (text, region) in enumerate(zip(('top', 'middle', 'bottom'), regions))]
    engine = SceneEngine(markers)
    result = adapter_for(monkeypatch, engine).recognize(scene(size, markers))
    assert {block['text'] for block in result['blocks']} == {'top', 'middle', 'bottom'}
    assert (result['width'], result['height']) == size
    for block in result['blocks']:
        assert_box(block, dict((text, region) for _, text, region in markers)[block['text']], size)
    assert len(engine.sizes) > 1
    assert all(max(tile_size) <= 2400 for tile_size in engine.sizes)
    assert result['tiles_completed'] == result['tiles_total'] == len(engine.sizes)
    assert_complete_coverage(result['tiles'], size)


def test_long_image_tiles_both_axes_when_short_side_exceeds_limit(monkeypatch):
    size = (300, 1200)
    markers = [(20, 'upper left', (5, 20, 35, 30)), (60, 'middle right', (265, 590, 295, 600)),
               (100, 'lower right', (265, 1180, 295, 1190))]
    engine = SceneEngine(markers)
    result = adapter_for(monkeypatch, engine, max_side=128, tile_overlap=16).recognize(scene(size, markers))
    assert {block['text'] for block in result['blocks']} == {text for _, text, _ in markers}
    assert all(max(tile_size) <= 128 for tile_size in engine.sizes)
    assert_complete_coverage(result['tiles'], size)
    for block in result['blocks']:
        assert_box(block, dict((text, region) for _, text, region in markers)[block['text']], size)


def test_overlap_deduplicates_evidence_but_keeps_repeated_text_at_other_positions(monkeypatch):
    size = (100, 700)
    markers = [(20, 'repeated clause', (10, 110, 60, 120)),
               (60, 'repeated clause', (10, 334, 60, 344)),
               (100, 'last clause', (10, 650, 60, 660))]
    engine = SceneEngine(markers)
    result = adapter_for(monkeypatch, engine, max_side=128, tile_overlap=32).recognize(scene(size, markers))
    repeated = [block for block in result['blocks'] if block['text'] == 'repeated clause']
    assert len(repeated) == 2
    assert sorted(block['y'] for block in repeated) == pytest.approx([110 / 700, 334 / 700])
    assert len(result['blocks']) == 3


def test_long_image_orders_nearby_rows_before_horizontal_position(monkeypatch):
    markers = [(20, 'first row', (70, 10000, 100, 10012)),
               (60, 'second row', (10, 10025, 50, 10037))]
    result = adapter_for(monkeypatch, SceneEngine(markers)).recognize(scene((1080, 20000), markers))
    assert result['normalized_text'] == 'first row\nsecond row'


def test_exif_rotation_uses_canonical_original_coordinates(monkeypatch):
    markers = [(20, 'rotated rule', (10, 20, 60, 30))]
    result = adapter_for(monkeypatch, SceneEngine(markers), max_side=128, tile_overlap=16).recognize(
        scene((100, 700), markers, orientation=6))
    assert (result['width'], result['height']) == (700, 100)
    assert len(result['blocks']) == 1
    assert_box(result['blocks'][0], (670, 10, 680, 60), (700, 100))


def test_blank_tiles_complete_without_discarding_text_elsewhere(monkeypatch):
    markers = [(20, 'bottom rule', (10, 650, 60, 660))]
    result = adapter_for(monkeypatch, SceneEngine(markers), max_side=128, tile_overlap=16).recognize(
        scene((100, 700), markers))
    assert result['normalized_text'] == 'bottom rule'
    assert result['tiles_completed'] == result['tiles_total']
    assert any(tile['status'] == 'completed' and not tile['blocks'] for tile in result['tiles'])


def test_failed_tile_returns_partial_progress_and_retry_reuses_only_matching_successes(monkeypatch):
    markers = [(20, 'top rule', (10, 20, 60, 30)), (60, 'bottom rule', (10, 650, 60, 660))]
    data = scene((100, 700), markers)
    engine = SceneEngine(markers, fail_calls={2})
    adapter = adapter_for(monkeypatch, engine, max_side=128, tile_overlap=16)
    progress = []
    with pytest.raises(OCRError) as error:
        adapter.recognize_with_progress(data, on_tile=lambda tile: progress.append(deepcopy(tile)))
    partial = error.value.partial_result
    assert partial['tiles_completed'] < partial['tiles_total']
    assert partial['blocks']
    assert sum(tile['status'] == 'failed' for tile in partial['tiles']) == 1
    assert len(progress) == partial['tiles_total']
    assert all(tile['tiles_total'] == partial['tiles_total'] for tile in progress)
    assert {tile['id'] for tile in progress} == {tile['id'] for tile in partial['tiles']}
    previous_calls = len(engine.sizes)
    resumed_progress = []
    complete = adapter.recognize_with_progress(data, resume_tiles=partial['tiles'], on_tile=resumed_progress.append)
    assert len(engine.sizes) - previous_calls == 1
    assert complete['tiles_completed'] == complete['tiles_total']
    assert len(resumed_progress) == complete['tiles_total']
    assert complete['normalized_text'] == 'top rule\nbottom rule'

    other_engine = SceneEngine(markers)
    changed = adapter_for(monkeypatch, other_engine, max_side=128, tile_overlap=24)
    changed.recognize_with_progress(data, resume_tiles=complete['tiles'])
    assert len(other_engine.sizes) > 1


def test_resume_rejects_successes_from_different_image_bytes(monkeypatch):
    first_markers = [(20, 'old rule', (10, 20, 60, 30))]
    adapter = adapter_for(monkeypatch, SceneEngine(first_markers), max_side=128, tile_overlap=16)
    first = adapter.recognize_with_progress(scene((100, 700), first_markers))
    next_markers = [(60, 'new rule', (10, 20, 60, 30))]
    engine = SceneEngine(next_markers)
    adapter._engine = engine
    second = adapter.recognize_with_progress(scene((100, 700), next_markers), resume_tiles=first['tiles'])
    assert second['normalized_text'] == 'new rule'
    assert len(engine.sizes) == second['tiles_total']


def test_preprocess_fingerprint_versions_tiling_configuration():
    baseline = RapidOCRAdapter()
    assert baseline.preprocess_version != 'exif-white-rgb-scale-v1'
    assert baseline.preprocess_fingerprint != RapidOCRAdapter(tile_overlap=128).preprocess_fingerprint


def test_ordinary_image_keeps_existing_thumbnail_geometry(monkeypatch):
    markers = [(20, 'ordinary rule', (40, 20, 120, 60))]
    class GeometryEngine:
        sizes = []

        def __call__(self, pixels):
            self.sizes.append((pixels.shape[1], pixels.shape[0]))
            # Known detector coordinates in the 128x96 inference image. Pure
            # marker pixels shrink under LANCZOS, so they are no box oracle here.
            return SimpleNamespace(boxes=[[[12.8, 6.4], [38.4, 6.4], [38.4, 19.2], [12.8, 19.2]]],
                                   txts=['ordinary rule'], scores=[.95])

    engine = GeometryEngine()
    result = adapter_for(monkeypatch, engine, max_side=128).recognize(scene((400, 300), markers))
    assert engine.sizes == [(128, 96)]
    assert (result['width'], result['height']) == (400, 300)
    assert result['normalized_text'] == 'ordinary rule'
    assert_box(result['blocks'][0], markers[0][2], (400, 300))


def test_tile_limit_fails_before_engine_inference(monkeypatch):
    engine = SceneEngine([])
    adapter = adapter_for(monkeypatch, engine, max_side=128, tile_overlap=16, max_tiles=2)
    with pytest.raises(OCRError, match='tile limit'):
        adapter.recognize(scene((100, 700), []))
    assert not engine.sizes


def test_time_budget_stops_new_tiles_and_reports_incomplete_coverage(monkeypatch):
    from knowledge_service.integrations import image_ocr
    engine = SceneEngine([])
    monkeypatch.setattr(image_ocr, 'monotonic', lambda: len(engine.sizes) * 2)
    adapter = adapter_for(monkeypatch, engine, max_side=128, tile_overlap=16, timeout_seconds=1)
    with pytest.raises(OCRError) as error:
        adapter.recognize_with_progress(scene((100, 700), []))
    assert len(engine.sizes) == 1
    assert error.value.partial_result['tiles_completed'] == 1
    assert error.value.partial_result['tiles_total'] > 1
    assert error.value.partial_result['status'] == 'partial'


def test_empty_detection_with_sparse_ink_rechecks_margins_and_restores_coordinates(monkeypatch):
    class SparseSceneEngine(SceneEngine):
        def __call__(self, pixels):
            result = super().__call__(pixels)
            ys, _ = np.where(np.any(pixels < 230, axis=2))
            # Reproduce the real local detector's empty result for a small line
            # surrounded by a tall blank region, despite readable source pixels.
            if len(ys) and (ys.max() - ys.min() + 1) / pixels.shape[0] < .1:
                return SimpleNamespace(boxes=None, txts=None, scores=None)
            return result

    markers = [(20, 'sparse rule must remain searchable', (10, 340, 60, 350))]
    engine = SparseSceneEngine(markers)
    result = adapter_for(monkeypatch, engine, max_side=128, tile_overlap=16).recognize(scene((100, 700), markers))
    assert result['normalized_text'] == 'sparse rule must remain searchable'
    assert len(result['blocks']) == 1
    assert_box(result['blocks'][0], markers[0][2], (100, 700))
