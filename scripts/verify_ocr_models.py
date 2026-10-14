"""Verify expected files, runtime compatibility, and a local inference smoke test."""
import argparse
import io
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from knowledge_service.integrations.image_ocr import verify_models,RapidOCRAdapter

if __name__=='__main__':
    p=argparse.ArgumentParser(); p.add_argument('--root',default='data/model/ocr/pp-ocr-v6-small'); p.add_argument('--image')
    args=p.parse_args(); verify_models(args.root)
    if args.image: data=Path(args.image).read_bytes()
    else:
        from PIL import Image,ImageDraw
        image=Image.new('RGB',(400,120),'white'); ImageDraw.Draw(image).text((20,40),'OCR model validation 123',fill='black',font_size=24)
        output=io.BytesIO(); image.save(output,format='PNG'); data=output.getvalue()
    result=RapidOCRAdapter(args.root).recognize(data)
    if not result['blocks']: raise SystemExit('OCR inference returned no text')
    print('Verified local OCR models and inference:',len(result['blocks']),'blocks')
