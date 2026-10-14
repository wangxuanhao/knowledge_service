"""Generate equivalent rule fixtures with tables, shared images and missing assets."""
import argparse
import io
import zipfile
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont


def generate(path,count=1000):
    image=Image.new('RGB',(900,180),'white')
    font_path=Path('C:/Windows/Fonts/msyh.ttc')
    font=ImageFont.truetype(str(font_path),32) if font_path.exists() else ImageFont.load_default()
    ImageDraw.Draw(image).text((25,50),'设备温度超过 80 度时，应停止运行并检查冷却系统。',font=font,fill='black')
    output=io.BytesIO(); image.save(output,format='PNG')
    with zipfile.ZipFile(path,'w',compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr('shared/rule.png',output.getvalue())
        for number in range(count):
            archive.writestr(f'rule-{number:04}.md',f'# 规则 {number}\n\n设备编号 {number} 的安全要求。\n\n| 参数 | 阈值 |\n|---|---|\n| 温度 | 80 |\n\n![安全规则](shared/rule.png)\n')
    return path

if __name__=='__main__':
    parser=argparse.ArgumentParser(); parser.add_argument('--output',default='data/ingest-fixture.zip'); parser.add_argument('--count',type=int,default=1000)
    args=parser.parse_args(); Path(args.output).parent.mkdir(parents=True,exist_ok=True); print(generate(args.output,args.count))
