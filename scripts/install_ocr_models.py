"""Install verified local OCR weights; downloading requires explicit --download-url."""
import argparse
import hashlib
import json
import shutil
import sys
from pathlib import Path
from urllib.request import urlopen
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from knowledge_service.integrations.image_ocr import MANIFEST, verify_models


def install(source, target, download_url=None):
    manifest=json.loads(MANIFEST.read_text())
    root=Path(target).resolve(); root.mkdir(parents=True,exist_ok=True)
    for entry in manifest['files']:
        destination=root / entry['path']; destination.parent.mkdir(parents=True,exist_ok=True)
        if download_url:
            with urlopen(download_url.rstrip('/')+'/'+entry['path'],timeout=120) as response:
                data=response.read(entry['size_bytes']+1)
        else:
            if not source: raise ValueError('--source or explicit --download-url is required')
            candidate=Path(source)/entry['path']
            if not candidate.is_file(): candidate=Path(source)/Path(entry['path']).name
            data=candidate.read_bytes()
        if len(data)!=entry['size_bytes'] or hashlib.sha256(data).hexdigest()!=entry['sha256']: raise ValueError('Model checksum mismatch: '+entry['path'])
        staging=destination.with_suffix('.tmp'); staging.write_bytes(data); staging.replace(destination)
    # Copy RapidOCR complete config then set all local model paths (never implicit download).
    import rapidocr, yaml
    config=yaml.safe_load((Path(rapidocr.__file__).parent/'config.yaml').read_text())
    for entry in manifest['files']: config[entry['role'].title()]['model_path']=str(root/entry['path'])
    config['EngineConfig']['onnxruntime']['use_cuda']=False
    (root/'rapidocr.yaml').write_text(yaml.safe_dump(config,sort_keys=False),encoding='utf-8')
    (root/'manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2),encoding='utf-8')
    verify_models(root)
    return root

if __name__=='__main__':
    p=argparse.ArgumentParser(); p.add_argument('--source'); p.add_argument('--target',default='data/model/ocr/pp-ocr-v6-small'); p.add_argument('--download-url')
    args=p.parse_args(); print(install(args.source,args.target,args.download_url))
