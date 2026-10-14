"""Small reproducible CPU benchmark, without a database or LLM calls."""
import argparse
import io
import json
from difflib import SequenceMatcher
import platform
import sys
import tempfile
import time
import zipfile
from datetime import datetime,timezone
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from PIL import Image,ImageDraw,ImageFont
from knowledge_service.integrations.image_ocr import RapidOCRAdapter,OCRError
from knowledge_service.services.image_ocr import ImageOCRService
from generate_ingest_fixture import generate


def benchmark(model_root,output_path):
    with tempfile.TemporaryDirectory(prefix='knowledge-ocr-benchmark-') as directory:
        fixture=generate(Path(directory)/'fixture.zip',1)
        with zipfile.ZipFile(fixture) as archive: chinese=archive.read('shared/rule.png')
    font=ImageFont.truetype('C:/Windows/Fonts/arial.ttf',32)
    english=Image.new('RGB',(900,160),'white')
    ImageDraw.Draw(english).text((25,50),'Stop operation when temperature exceeds 80 degrees.',font=font,fill='black')
    def png(image):
        stream=io.BytesIO();image.save(stream,format='PNG');return stream.getvalue()
    samples=[('chinese_rule_fixture',chinese,'设备温度超过 80 度时，应停止运行并检查冷却系统。'),
             ('blank',png(Image.new('RGB',(900,180),'white')),''),
             ('english',png(english),'Stop operation when temperature exceeds 80 degrees.'),
             ('english_rotated_180',png(english.rotate(180)),'Stop operation when temperature exceeds 80 degrees.'),
             ('english_rotated_90',png(english.rotate(90,expand=True)),'Stop operation when temperature exceeds 80 degrees.')]
    adapter=RapidOCRAdapter(model_root)
    quality=ImageOCRService(None,None,adapter)
    rows=[]
    for ordinal,(name,data,expected) in enumerate(samples):
        started=time.perf_counter()
        result=adapter.recognize(data)
        seconds=time.perf_counter()-started
        try:
            accepted,average=quality.quality_check(result)
            status='passed'
        except OCRError as exc:
            status=exc.code
        normalized_expected=''.join(c for c in expected if c.isalnum())
        normalized_observed=''.join(c for c in result['normalized_text'] if c.isalnum())
        rows.append(dict(sample=name,cold_start=ordinal==0,elapsed_seconds=round(seconds,4),expected_text=expected,normalized_text=result['normalized_text'],normalized_character_similarity=round(SequenceMatcher(None,normalized_expected,normalized_observed).ratio(),4),average_confidence=result['average_confidence'],blocks=len(result['blocks']),quality_status=status,width=result['width'],height=result['height']))
    report=dict(recorded_at=datetime.now(timezone.utc).isoformat(),platform=platform.platform(),python=platform.python_version(),device='cpu',engine=adapter.engine,engine_version=result['engine_version'],languages=result['languages'],quality_policy=quality.quality_policy,configuration_fingerprint=result['configuration_fingerprint'],preprocess_fingerprint=result['preprocess_fingerprint'],notes=['First sample includes model initialization.','Synthetic rule samples validate local execution, not production accuracy.','No LLM or database calls.','Rotated English may be misrecognized; confidence gates reject these samples with current defaults.','Latency depends on concurrent local workloads.'],samples=rows)
    if rows[1]['quality_status']!='ocr_low_confidence': raise RuntimeError('Blank image unexpectedly passed OCR quality gate')
    if rows[0]['quality_status']!='passed' or '冷却系统' not in rows[0]['normalized_text']: raise RuntimeError('Chinese rule fixture did not satisfy benchmark contract')
    path=Path(output_path);path.parent.mkdir(parents=True,exist_ok=True);path.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(rows,ensure_ascii=False,indent=2))
    return report


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--model-root',default='data/model/ocr/pp-ocr-v6-small');parser.add_argument('--output',default='docs/research/2026-10-14-image-ocr-benchmark.json')
    args=parser.parse_args();benchmark(args.model_root,args.output)
