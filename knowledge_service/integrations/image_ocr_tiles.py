"""Bounded OCR crops and evidence merging in canonical image coordinates."""
from difflib import SequenceMatcher


LONG_IMAGE_RATIO = 3
SPARSE_INK_PADDING = 32
SPARSE_INK_THRESHOLD = 235
SPARSE_INK_MAX_AREA_RATIO = .6


def sparse_ink_crop(image):
    """Find a small nonblank region worth rechecking after empty detection."""
    with image.convert('L') as gray:
        with gray.point([255 if value < SPARSE_INK_THRESHOLD else 0 for value in range(256)]) as mask:
            bounds = mask.getbbox()
    if bounds is None:
        return None
    left, top, right, bottom = bounds
    # A sliver cut through a neighboring line is protected by the overlap; it
    # cannot become readable by enlarging blank margins around those pixels.
    if right - left < 4 or bottom - top < 4:
        return None
    region = (max(0, left - SPARSE_INK_PADDING), max(0, top - SPARSE_INK_PADDING),
              min(image.width, right + SPARSE_INK_PADDING), min(image.height, bottom + SPARSE_INK_PADDING))
    if (region[2] - region[0]) * (region[3] - region[1]) >= image.width * image.height * SPARSE_INK_MAX_AREA_RATIO:
        return None
    return region


def _axis_spans(image, axis, limit, overlap, max_tiles):
    """Prefer a nearby blank cut without enlarging the inference crop."""
    length = image.size[axis]
    spans = []
    start = 0
    while start < length:
        if len(spans) >= max_tiles:
            raise ValueError('OCR tile limit exceeded')
        end = min(start + limit, length)
        if end < length:
            # Only inspect a narrow band, never another full source bitmap.
            window = min(overlap // 2, limit // 8, limit - overlap)
            for candidate in range(end, end - window, -1):
                stripe = (candidate - 1, 0, candidate, image.height) if axis == 0 else (0, candidate - 1, image.width, candidate)
                if all(low >= 245 for low, _ in image.crop(stripe).getextrema()):
                    end = candidate
                    break
        spans.append((start, end))
        if end == length:
            break
        start = end - overlap
    return spans


def plan_tiles(image, max_side, overlap, max_tiles):
    """Ordinary images keep thumbnail behavior; elongated images keep pixels."""
    width, height = image.size
    tiled = max(width, height) > max_side and max(width, height) >= LONG_IMAGE_RATIO * min(width, height)
    if not tiled:
        return [(0, 0, width, height)], False
    xs = _axis_spans(image, 0, max_side, overlap, max_tiles)
    ys = _axis_spans(image, 1, max_side, overlap, max_tiles)
    if len(xs) * len(ys) > max_tiles:
        raise ValueError('OCR tile limit exceeded')
    return [(left, top, right, bottom) for top, bottom in ys for left, right in xs], True


def _same_evidence(left, right):
    overlap_width = min(left['x'] + left['width'], right['x'] + right['width']) - max(left['x'], right['x'])
    overlap_height = min(left['y'] + left['height'], right['y'] + right['height']) - max(left['y'], right['y'])
    if overlap_width <= 0 or overlap_height <= 0:
        return False
    intersection = overlap_width * overlap_height
    left_area = left['width'] * left['height']
    right_area = right['width'] * right['height']
    if intersection / min(left_area, right_area) < .7 and intersection / (left_area + right_area - intersection) < .45:
        return False
    a = ''.join(left['normalized_text'].casefold().split())
    b = ''.join(right['normalized_text'].casefold().split())
    return a == b or SequenceMatcher(None, a, b, autojunk=False).ratio() >= .85


def merge_tile_blocks(tiles):
    """Remove overlapping crop copies, retaining repeated clauses elsewhere."""
    kept = []
    for tile in tiles:
        if tile['status'] != 'completed':
            continue
        for block in tile['blocks']:
            duplicate = next((index for index, existing in enumerate(kept) if _same_evidence(existing, block)), None)
            if duplicate is None:
                kept.append(dict(block))
            else:
                existing = kept[duplicate]
                # Prefer a whole line to its edge-clipped copy, then confidence.
                priority = lambda item: (item['width'] * item['height'], item['confidence'])
                if priority(block) > priority(existing):
                    kept[duplicate] = dict(block)
    # Group only physically overlapping line centers. A normalized-y rounding
    # bucket can collapse many different source rows on a 20000px screenshot.
    rows = []
    for block in sorted(kept, key=lambda item: (item['y'], item['x'])):
        center = block['y'] + block['height'] / 2
        if rows:
            row = rows[-1]
            anchor = row[0]
            anchor_center = anchor['y'] + anchor['height'] / 2
            same_row = abs(center - anchor_center) <= min(block['height'], anchor['height']) * .4
        else:
            same_row = False
        if same_row:
            row.append(block)
        else:
            rows.append([block])
    return [block for row in rows for block in sorted(row, key=lambda item: item['x'])]
