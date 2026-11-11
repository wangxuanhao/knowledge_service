"""Local-only RapidOCR adapter. Validation completes before engine construction."""
import hashlib
import io
import json
import os
import threading
import math
from copy import deepcopy
from time import monotonic
from importlib.metadata import version
from pathlib import Path
from pydantic import BaseModel, Field, field_validator, model_validator
from .image_ocr_tiles import (LONG_IMAGE_RATIO, SPARSE_INK_MAX_AREA_RATIO, SPARSE_INK_PADDING,
                              SPARSE_INK_THRESHOLD, merge_tile_blocks, plan_tiles, sparse_ink_crop)

MANIFEST = Path(__file__).parents[1] / 'resources/ocr/pp-ocr-v6-small.json'
_OCR_SEMAPHORE = threading.BoundedSemaphore(1)
_AVAILABILITY_LOCK_TIMEOUT = 1.0
_AVAILABILITY_LOCK = threading.Lock()
_AVAILABILITY_CACHE = None


def _file_identity(path):
    """Cheap identity for invalidation; model contents are verified separately."""
    path = Path(path).resolve()
    try:
        stat = path.stat()
        return (str(path), stat.st_dev, stat.st_ino, stat.st_mode, stat.st_size,
                stat.st_mtime_ns, stat.st_ctime_ns)
    except OSError as exc:
        return (str(path), type(exc).__name__, exc.errno)


class OcrBlock(BaseModel):
    text: str
    normalized_text: str
    confidence: float = Field(ge=0,le=1,allow_inf_nan=False)
    x: float = Field(ge=0,le=1,allow_inf_nan=False)
    y: float = Field(ge=0,le=1,allow_inf_nan=False)
    width: float = Field(gt=0,le=1,allow_inf_nan=False)
    height: float = Field(gt=0,le=1,allow_inf_nan=False)
    page_or_frame: int = Field(default=1,ge=1)

    @field_validator('text','normalized_text')
    @classmethod
    def nonempty_text(cls,value):
        if not value.strip(): raise ValueError('OCR block text is empty')
        return value

    @model_validator(mode='after')
    def within_image(self):
        if self.x+self.width>1 or self.y+self.height>1:
            raise ValueError('OCR box extends beyond image')
        return self


class OcrResult(BaseModel):
    blocks: list[OcrBlock]
    width: int = Field(gt=0)
    height: int = Field(gt=0)
    engine: str
    engine_version: str
    normalized_text: str
    average_confidence: float = Field(ge=0,le=1,allow_inf_nan=False)
    languages: list[str]
    configuration_fingerprint: str
    preprocess_fingerprint: str
    status: str = 'completed'
    tiles: list[dict] = Field(default_factory=list)
    tiles_completed: int = Field(default=0, ge=0)
    tiles_total: int = Field(default=0, ge=0)


class OCRError(RuntimeError):
    def __init__(self, code, message, *, partial_result=None):
        self.code = code
        self.partial_result = partial_result
        super().__init__(message)


def verify_models(root, manifest_path=MANIFEST):
    manifest = json.loads(Path(manifest_path).read_text(encoding='utf-8'))
    root = Path(root).resolve()
    for entry in manifest['files']:
        path = (root / entry['path']).resolve()
        if not path.is_relative_to(root) or not path.is_file():
            raise OCRError('ocr_engine_unavailable', 'OCR model is missing: '+entry['path'])
        if path.stat().st_size != entry['size_bytes'] or hashlib.sha256(path.read_bytes()).hexdigest() != entry['sha256']:
            raise OCRError('ocr_engine_unavailable', 'OCR model checksum mismatch: '+entry['path'])
    from packaging.specifiers import SpecifierSet
    for package, requirement in manifest['runtime'].items():
        if version(package) not in SpecifierSet(requirement):
            raise OCRError('ocr_engine_unavailable', 'Incompatible OCR runtime: '+package)
    return manifest


class RapidOCRAdapter:
    engine = 'rapidocr'
    preprocess_version = 'exif-white-rgb-overlap-tiles-v3'

    def __init__(self, model_root=None, config_path=None, max_pixels=40_000_000, max_side=2400,
                 tile_overlap=None, max_tiles=128, timeout_seconds=180):
        self.model_root = Path(model_root or os.getenv('KG_IMAGE_OCR_MODEL_ROOT', 'data/model/ocr/pp-ocr-v6-small')).resolve()
        self.config_path = config_path or os.getenv('KG_IMAGE_OCR_CONFIG')
        self.max_pixels, self.max_side = max_pixels, max_side
        self.tile_overlap = min(192, max_side // 4) if tile_overlap is None and isinstance(max_side, int) else tile_overlap
        self.max_tiles = max_tiles
        self.timeout_seconds = timeout_seconds
        self.languages = os.getenv('KG_IMAGE_OCR_LANGUAGES', 'ch,en')
        self._engine = None
        self._init_lock = threading.Lock()

    @property
    def preprocess_fingerprint(self):
        payload=dict(version=self.preprocess_version,max_side=self.max_side,max_pixels=self.max_pixels,exif='transpose',alpha='white_composite',colors='RGB_to_BGR',scale='ordinary_thumbnail_long_image_tiles',
                     tiling=dict(long_image_ratio=LONG_IMAGE_RATIO,overlap=self.tile_overlap,max_tiles=self.max_tiles,timeout_seconds=self.timeout_seconds,cuts='nearby_blank_stripe',merge='spatial_text_line_order_v1',
                                 sparse_ink_retry=dict(padding=SPARSE_INK_PADDING,threshold=SPARSE_INK_THRESHOLD,max_area_ratio=SPARSE_INK_MAX_AREA_RATIO,attempts=1)))
        return hashlib.sha256(json.dumps(payload,sort_keys=True).encode()).hexdigest()

    @property
    def configuration_fingerprint(self):
        payload = dict(manifest=json.loads(MANIFEST.read_text()),preprocess=self.preprocess_fingerprint,max_side=self.max_side,max_pixels=self.max_pixels,languages=self.languages,device='cpu')
        try:
            payload['runtime_versions']={name:version(name) for name in ('rapidocr','onnxruntime')}
        except Exception as exc:
            raise OCRError('ocr_engine_unavailable','OCR runtime is not installed') from exc
        if os.getenv('KG_IMAGE_OCR_DEVICE','cpu')!='cpu':
            raise OCRError('ocr_engine_unavailable','This OCR configuration supports CPU only')
        if os.getenv('KG_IMAGE_OCR_BACKEND','rapidocr')!='rapidocr':
            raise OCRError('ocr_engine_unavailable','Unsupported image OCR backend')
        if not isinstance(self.max_side,int) or not isinstance(self.max_pixels,int) or self.max_side<1 or self.max_pixels<1:
            raise OCRError('ocr_engine_unavailable','Invalid OCR image limits')
        if not isinstance(self.tile_overlap, int) or not 0 <= self.tile_overlap < self.max_side:
            raise OCRError('ocr_engine_unavailable', 'Invalid OCR tile overlap')
        if not isinstance(self.max_tiles, int) or self.max_tiles < 1 or not isinstance(self.timeout_seconds, (int, float)) or not math.isfinite(self.timeout_seconds) or self.timeout_seconds <= 0:
            raise OCRError('ocr_engine_unavailable', 'Invalid OCR task limits')
        if set(self.languages.split(',')) - {'ch','en'} or not self.languages:
            raise OCRError('ocr_engine_unavailable','Unsupported OCR language configuration')
        if self.config_path:
            path=Path(self.config_path)
            if not path.is_file(): raise OCRError('ocr_engine_unavailable','OCR configuration missing')
            payload['config_sha256']=hashlib.sha256(path.read_bytes()).hexdigest()
            import yaml
            try:
                config=yaml.safe_load(path.read_text(encoding='utf-8'))
            except Exception as exc:
                raise OCRError('ocr_engine_unavailable','Invalid OCR configuration') from exc
            if not isinstance(config,dict) or not isinstance(config.get('EngineConfig',{}),dict):
                raise OCRError('ocr_engine_unavailable','Invalid OCR configuration structure')
            runtime=config.get('EngineConfig',{}).get('onnxruntime',{})
            if not isinstance(runtime,dict) or any(not isinstance(config.get(section,{}),dict) for section in ('Det','Cls','Rec')):
                raise OCRError('ocr_engine_unavailable','Invalid OCR runtime configuration')
            if any(runtime.get('use_'+provider,False) for provider in ('cuda','dml','cann','coreml')):
                raise OCRError('ocr_engine_unavailable','OCR configuration enables a non-CPU provider')
            if any(config.get(section,{}).get('engine_type','onnxruntime')!='onnxruntime' for section in ('Det','Cls','Rec')):
                raise OCRError('ocr_engine_unavailable','OCR configuration uses unsupported inference engine')
        return hashlib.sha256(json.dumps(payload,sort_keys=True).encode()).hexdigest()

    def _availability_identity(self):
        root = Path(self.model_root).resolve()
        manifest_identity = _file_identity(MANIFEST)
        packages = {'rapidocr', 'onnxruntime'}
        try:
            manifest = json.loads(MANIFEST.read_text(encoding='utf-8'))
            files = tuple(_file_identity(root / entry['path']) for entry in manifest['files'])
            packages.update(manifest['runtime'])
        except Exception as exc:
            files = ('invalid_manifest', type(exc).__name__)
        runtimes = []
        for package in sorted(packages):
            try:
                runtimes.append((package, version(package)))
            except Exception as exc:
                runtimes.append((package, type(exc).__name__, str(exc)))
        return (str(root), manifest_identity, files, tuple(runtimes),
                _file_identity(self.config_path) if self.config_path else None,
                self.preprocess_fingerprint, self.max_side, self.max_pixels, self.languages,
                os.getenv('KG_IMAGE_OCR_DEVICE', 'cpu'),
                os.getenv('KG_IMAGE_OCR_BACKEND', 'rapidocr'))

    def available(self):
        global _AVAILABILITY_CACHE
        # Capability routes create adapters per request: share one bounded cache
        # entry across instances. A slow concurrent validation cannot
        # leave capability requests waiting indefinitely; contention fails closed.
        if not _AVAILABILITY_LOCK.acquire(timeout=_AVAILABILITY_LOCK_TIMEOUT):
            return False
        try:
            identity = self._availability_identity()
            if _AVAILABILITY_CACHE is not None and identity == _AVAILABILITY_CACHE[0]:
                return _AVAILABILITY_CACHE[1]
            try:
                verify_models(self.model_root)
                self.configuration_fingerprint
                result = True
            except Exception:
                result = False
            # A file/settings change during validation has not been validated as
            # a consistent identity. Retry on the next read instead of caching it.
            if self._availability_identity() != identity:
                _AVAILABILITY_CACHE = None
                return False
            _AVAILABILITY_CACHE = (identity, result)
            return result
        except Exception:
            return False
        finally:
            _AVAILABILITY_LOCK.release()

    def _initialize(self):
        with self._init_lock:
            if self._engine is not None: return
            try:
                manifest=verify_models(self.model_root)
                from rapidocr import RapidOCR
                params={f"{entry['role'].title()}.model_path":str(self.model_root / entry['path']) for entry in manifest['files']}
                params.update({'EngineConfig.onnxruntime.use_cuda':False,'Global.text_score':0.0,'EngineConfig.onnxruntime.intra_op_num_threads':2,'EngineConfig.onnxruntime.inter_op_num_threads':1})
                # Explicit local paths prevent RapidOCR's model auto-download branch.
                self._engine=RapidOCR(config_path=self.config_path,params=params)
            except OCRError: raise
            except Exception as exc: raise OCRError('ocr_engine_unavailable','Local OCR initialization failed') from exc

    def recognize(self, data):
        return self.recognize_with_progress(data)

    @staticmethod
    def _tile_blocks(result, region, input_size, original_size):
        """Validate local engine geometry before mapping to the canonical image."""
        width, height = input_size
        original_width, original_height = original_size
        left, top, right, bottom = region
        blocks = []
        if result.boxes is None:
            if result.txts is not None and len(result.txts) or result.scores is not None and len(result.scores):
                raise OCRError('ocr_failed', 'OCR output has text without geometry')
            return blocks
        if result.txts is None or result.scores is None or not (len(result.boxes) == len(result.txts) == len(result.scores)):
            raise OCRError('ocr_failed', 'OCR output contains inconsistent block counts')
        for box, text, confidence in zip(result.boxes, result.txts, result.scores):
            if len(box) != 4 or any(len(point) != 2 for point in box):
                raise OCRError('ocr_failed', 'OCR output contains an invalid polygon')
            xs = [float(point[0]) for point in box]
            ys = [float(point[1]) for point in box]
            if any(not math.isfinite(point) for point in xs + ys) or min(xs) < 0 or min(ys) < 0 or max(xs) > width or max(ys) > height:
                raise OCRError('ocr_failed', 'OCR polygon is outside image bounds')
            x = (left + min(xs) * (right - left) / width) / original_width
            y = (top + min(ys) * (bottom - top) / height) / original_height
            end_x = min(1.0, (left + max(xs) * (right - left) / width) / original_width)
            end_y = min(1.0, (top + max(ys) * (bottom - top) / height) / original_height)
            block = OcrBlock(text=text, normalized_text=' '.join(text.split()), confidence=float(confidence),
                             x=x, y=y, width=end_x - x, height=end_y - y)
            blocks.append(block.model_dump())
        return blocks

    @staticmethod
    def _resumable_blocks(previous, tile, original_size):
        if not isinstance(previous, dict) or previous.get('status') != 'completed':
            return None
        keys = ('id', 'region', 'image_sha256', 'configuration_fingerprint', 'preprocess_fingerprint')
        if any(previous.get(key) != tile[key] for key in keys):
            return None
        try:
            if not isinstance(previous.get('blocks'), list):
                return None
            blocks = [OcrBlock.model_validate(block).model_dump() for block in previous['blocks']]
            width, height = original_size
            left, top, right, bottom = tile['region']
            epsilon = 1e-7
            for block in blocks:
                if (block['x'] * width < left - epsilon or block['y'] * height < top - epsilon or
                        (block['x'] + block['width']) * width > right + epsilon or
                        (block['y'] + block['height']) * height > bottom + epsilon):
                    return None
            return blocks
        except Exception:
            return None

    def _result(self, tiles, original_size, fingerprint, tiled):
        if tiled:
            blocks = merge_tile_blocks(tiles)
        else:
            blocks = [block for tile in tiles if tile['status'] == 'completed' for block in tile['blocks']]
            blocks.sort(key=lambda block: (round(block['y'], 2), block['x']))
        completed = sum(tile['status'] == 'completed' for tile in tiles)
        return OcrResult(
            blocks=blocks, width=original_size[0], height=original_size[1], engine=self.engine,
            engine_version=version('rapidocr'), normalized_text='\n'.join(block['normalized_text'] for block in blocks),
            average_confidence=sum(block['confidence'] for block in blocks) / len(blocks) if blocks else 0,
            languages=self.languages.split(','), configuration_fingerprint=fingerprint,
            preprocess_fingerprint=self.preprocess_fingerprint,
            status='completed' if completed == len(tiles) else 'partial', tiles=tiles,
            tiles_completed=completed, tiles_total=len(tiles),
        ).model_dump()

    def _infer_crop(self, image, region, deadline):
        import numpy as np
        if monotonic() >= deadline:
            raise OCRError('ocr_failed', 'OCR time limit exceeded')
        with image.crop(region) as crop:
            crop.thumbnail((self.max_side, self.max_side))
            input_size = list(crop.size)
            # RapidOCR ndarray convention is BGR.
            pixels = np.asarray(crop)[:, :, ::-1].copy()
        if not _OCR_SEMAPHORE.acquire(timeout=max(0, deadline - monotonic())):
            raise OCRError('ocr_failed', 'OCR time limit exceeded while waiting for inference')
        try:
            self._initialize()
            if monotonic() >= deadline:
                raise OCRError('ocr_failed', 'OCR time limit exceeded')
            output = self._engine(pixels)
        finally:
            _OCR_SEMAPHORE.release()
        blocks = self._tile_blocks(output, region, input_size, image.size)
        return blocks, input_size

    def recognize_with_progress(self, data, *, resume_tiles=None, on_tile=None):
        """Recognize crops, reporting a detached current-tile dict after each one.

        Regions are [left, top, right, bottom] source pixels after EXIF transpose;
        right/bottom are exclusive. Blocks use that image's normalized geometry.
        Every callback includes tiles_total, including reused and blank tiles.
        An incomplete run raises OCRError with the full result in partial_result.
        """
        from PIL import Image, ImageOps
        try:
            fingerprint = self.configuration_fingerprint
            deadline = monotonic() + self.timeout_seconds
            with Image.open(io.BytesIO(data)) as source:
                if source.format not in {'PNG', 'JPEG', 'GIF', 'BMP', 'TIFF', 'WEBP'}:
                    raise OCRError('ocr_failed', 'Unsupported source image format')
                if source.width * source.height > self.max_pixels:
                    raise OCRError('ocr_failed', 'Image pixel limit exceeded')
                orientation = source.getexif().get(274, 1)
                canonical = ImageOps.exif_transpose(source)
                if 'A' in canonical.getbands() or 'transparency' in canonical.info:
                    rgba = canonical.convert('RGBA')
                    image = Image.new('RGB', rgba.size, 'white')
                    image.paste(rgba, mask=rgba.getchannel('A'))
                    rgba.close()
                else:
                    image = canonical.convert('RGB')
                canonical.close()
            with image:
                original_size = image.size
                try:
                    regions, tiled = plan_tiles(image, self.max_side, self.tile_overlap, self.max_tiles)
                except ValueError as exc:
                    raise OCRError('ocr_failed', str(exc)) from exc
                image_hash = hashlib.sha256(data).hexdigest()
                tiles = [dict(id=f'tile-{index + 1:06d}', ordinal=index, tiles_total=len(regions), region=list(region),
                              status='pending', blocks=[], image_sha256=image_hash,
                              configuration_fingerprint=fingerprint, preprocess_fingerprint=self.preprocess_fingerprint,
                              orientation=orientation, original_size=list(original_size))
                         for index, region in enumerate(regions)]
                previous_tiles = {tile.get('id'): tile for tile in (resume_tiles or []) if isinstance(tile, dict)}
                failures = []
                for tile in tiles:
                    reused = self._resumable_blocks(previous_tiles.get(tile['id']), tile, original_size)
                    if reused is not None:
                        tile.update(status='completed', blocks=reused)
                        previous = previous_tiles[tile['id']]
                        for key in ('input_size', 'scale', 'inference_region', 'sparse_ink_retry'):
                            if key in previous:
                                tile[key] = deepcopy(previous[key])
                    else:
                        try:
                            region = tile['region']
                            blocks, input_size = self._infer_crop(image, region, deadline)
                            if tiled and not blocks:
                                with image.crop(region) as crop:
                                    tighter = sparse_ink_crop(crop)
                                if tighter is not None:
                                    left, top = region[:2]
                                    region = [left + tighter[0], top + tighter[1], left + tighter[2], top + tighter[3]]
                                    blocks, input_size = self._infer_crop(image, region, deadline)
                                    tile['sparse_ink_retry'] = True
                            tile.update(input_size=input_size, inference_region=list(region),
                                        scale=[(region[2] - region[0]) / input_size[0], (region[3] - region[1]) / input_size[1]])
                            tile.update(status='completed', blocks=blocks)
                        except Exception as exc:
                            error = exc if isinstance(exc, OCRError) else OCRError('ocr_failed', 'Tile recognition failed')
                            tile.update(status='failed', error=dict(code=error.code, message=str(error)))
                            failures.append(error)
                    if on_tile is not None:
                        on_tile(deepcopy(tile))
                result = self._result(tiles, original_size, fingerprint, tiled)
                if failures:
                    raise OCRError(failures[0].code, f'Image recognition incomplete: {len(failures)} of {len(tiles)} tiles failed', partial_result=result)
                return result
        except OCRError:
            raise
        except Exception as exc:
            raise OCRError('ocr_failed', 'Image recognition failed') from exc
