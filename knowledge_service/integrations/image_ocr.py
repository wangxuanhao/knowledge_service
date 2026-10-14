"""Local-only RapidOCR adapter. Validation completes before engine construction."""
import hashlib
import io
import json
import os
import threading
from importlib.metadata import version
from pathlib import Path

MANIFEST = Path(__file__).parents[1] / 'resources/ocr/pp-ocr-v6-small.json'
_OCR_SEMAPHORE = threading.BoundedSemaphore(1)


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
        if self.config_path:
            path=Path(self.config_path)
            if not path.is_file(): raise OCRError('ocr_engine_unavailable','OCR configuration missing')
            payload['config_sha256']=hashlib.sha256(path.read_bytes()).hexdigest()
        return hashlib.sha256(json.dumps(payload,sort_keys=True).encode()).hexdigest()

    def available(self):
        try:
            verify_models(self.model_root)
            self.configuration_fingerprint
            return True
        except Exception:
            return False

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
            with Image.open(io.BytesIO(data)) as source:
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
                for box,text,confidence in zip(result.boxes,result.txts,result.scores):
                    xs=[float(p[0]) for p in box]; ys=[float(p[1]) for p in box]
                    x=max(0,min(1,min(xs)/width)); y=max(0,min(1,min(ys)/height))
                    blocks.append(dict(text=text,normalized_text=' '.join(text.split()),confidence=float(confidence),x=x,y=y,width=max(0,min(1-x,(max(xs)-min(xs))/width)),height=max(0,min(1-y,(max(ys)-min(ys))/height)),page_or_frame=1))
            blocks.sort(key=lambda b:(round(b['y'],2),b['x']))
            return dict(blocks=blocks,width=original_width,height=original_height,engine=self.engine,engine_version=version('rapidocr'),configuration_fingerprint=self.configuration_fingerprint,preprocess_fingerprint=hashlib.sha256(self.preprocess_version.encode()).hexdigest())
        except OCRError: raise
        except Exception as exc: raise OCRError('ocr_failed','Image recognition failed') from exc
