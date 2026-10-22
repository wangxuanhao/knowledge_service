"""Local-only RapidOCR adapter. Validation completes before engine construction."""
import hashlib
import io
import json
import os
import threading
import math
from importlib.metadata import version
from pathlib import Path
from pydantic import BaseModel, Field, field_validator, model_validator

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


class OCRError(RuntimeError):
    def __init__(self, code, message):
        self.code = code
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
    preprocess_version = 'exif-white-rgb-scale-v1'

    def __init__(self, model_root=None, config_path=None, max_pixels=40_000_000, max_side=2400):
        self.model_root = Path(model_root or os.getenv('KG_IMAGE_OCR_MODEL_ROOT', 'data/model/ocr/pp-ocr-v6-small')).resolve()
        self.config_path = config_path or os.getenv('KG_IMAGE_OCR_CONFIG')
        self.max_pixels, self.max_side = max_pixels, max_side
        self.languages = os.getenv('KG_IMAGE_OCR_LANGUAGES', 'ch,en')
        self._engine = None
        self._init_lock = threading.Lock()

    @property
    def preprocess_fingerprint(self):
        payload=dict(version=self.preprocess_version,max_side=self.max_side,max_pixels=self.max_pixels,exif='transpose',alpha='white_composite',colors='RGB_to_BGR',scale='aspect_preserving_thumbnail')
        return hashlib.sha256(json.dumps(payload,sort_keys=True).encode()).hexdigest()

    @property
    def configuration_fingerprint(self):
        payload = dict(manifest=json.loads(MANIFEST.read_text()),preprocess=self.preprocess_version,max_side=self.max_side,max_pixels=self.max_pixels,languages=self.languages,device='cpu')
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
                self.preprocess_version, self.max_side, self.max_pixels, self.languages,
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
        from PIL import Image, ImageOps
        import numpy as np
        try:
            fingerprint=self.configuration_fingerprint
            with Image.open(io.BytesIO(data)) as source:
                if source.format not in {'PNG','JPEG','GIF','BMP','TIFF','WEBP'}:
                    raise OCRError('ocr_failed','Unsupported source image format')
                if source.width * source.height > self.max_pixels: raise OCRError('ocr_failed','Image pixel limit exceeded')
                image=ImageOps.exif_transpose(source).convert('RGBA')
                original_width,original_height=image.size
                white=Image.new('RGBA',image.size,'white'); white.alpha_composite(image); image=white.convert('RGB')
                image.thumbnail((self.max_side,self.max_side))
                width,height=image.size
                # RapidOCR ndarray convention is BGR.
                pixels=np.asarray(image)[:,:,::-1].copy()
            with _OCR_SEMAPHORE:
                self._initialize()
                result=self._engine(pixels)
            blocks=[]
            if result.boxes is not None:
                if result.txts is None or result.scores is None or not (len(result.boxes)==len(result.txts)==len(result.scores)):
                    raise OCRError('ocr_failed','OCR output contains inconsistent block counts')
                for box,text,confidence in zip(result.boxes,result.txts,result.scores):
                    if len(box)!=4 or any(len(point)!=2 for point in box):
                        raise OCRError('ocr_failed','OCR output contains an invalid polygon')
                    xs=[float(p[0]) for p in box]; ys=[float(p[1]) for p in box]
                    if any(not math.isfinite(point) for point in xs+ys) or min(xs)<0 or min(ys)<0 or max(xs)>width or max(ys)>height:
                        raise OCRError('ocr_failed','OCR polygon is outside image bounds')
                    x=min(xs)/width; y=min(ys)/height
                    block=OcrBlock(text=text,normalized_text=' '.join(text.split()),confidence=float(confidence),x=x,y=y,width=max(xs)/width-x,height=max(ys)/height-y)
                    blocks.append(block.model_dump())
            blocks.sort(key=lambda b:(round(b['y'],2),b['x']))
            result=OcrResult(blocks=blocks,width=original_width,height=original_height,engine=self.engine,engine_version=version('rapidocr'),normalized_text='\n'.join(b['normalized_text'] for b in blocks),average_confidence=sum(b['confidence'] for b in blocks)/len(blocks) if blocks else 0,languages=self.languages.split(','),configuration_fingerprint=fingerprint,preprocess_fingerprint=self.preprocess_fingerprint)
            return result.model_dump()
        except OCRError: raise
        except Exception as exc: raise OCRError('ocr_failed','Image recognition failed') from exc
