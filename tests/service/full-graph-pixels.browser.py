"""Standalone software WebGL pixel regression; uses the existing llm_model environment.

Run: D:/anaconda/envs/llm_model/python.exe -X utf8 tests/service/full-graph-pixels.browser.py
Options --source and --output support an ignored pre-change renderer snapshot.
This deliberately runs outside pytest and does not access the app or database.
"""
import argparse
import json
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[2]
WEB = ROOT / 'knowledge_service/web'
FLAGS = ['--disable-gpu', '--disable-accelerated-2d-canvas', '--use-gl=angle',
         '--use-angle=swiftshader', '--enable-unsafe-swiftshader']

PROBE = r"""() => {
  const host=document.querySelector('#host'),r=window.renderer=new FullGraph.Renderer(host);
  const d=new FullGraph.GraphDataset();d.addNodes([{id:'a',type:'Thing',text:'A'}]);
  r.setDataset(d);r.setSessionManifest({id:'pixel-node',entity_count:1,relation_count:0});r.sync();r.showLabels=false;
  const gl=r.gl,ext=gl.getExtension('WEBGL_debug_renderer_info');
  const renderer=ext?gl.getParameter(ext.UNMASKED_RENDERER_WEBGL):gl.getParameter(gl.RENDERER);
  const rgb=hex=>[1,3,5].map(i=>parseInt(hex.slice(i,i+2),16)),tokens=GraphAppearance.tokens;
  const bg=rgb(tokens.background),outline=rgb(tokens.outline),selected=rgb(tokens.selected),edge=rgb(tokens.edge);
  const different=(a,b,tolerance=2)=>a.some((v,i)=>Math.abs(v-b[i])>tolerance);
  const read=()=>{r.draw();gl.finish();const pixels=new Uint8Array(r.canvas.width*r.canvas.height*4);gl.readPixels(0,0,r.canvas.width,r.canvas.height,gl.RGBA,gl.UNSIGNED_BYTE,pixels);return pixels;};
  const color=(pixels,x,y)=>{const i=((r.canvas.height-1-y)*r.canvas.width+x)*4;return [pixels[i],pixels[i+1],pixels[i+2]];};
  r.camera.x=-.37/r.camera.unit(r.width,r.height);r.camera.y=.23/r.camera.unit(r.width,r.height);
  const center=r.camera.worldToScreen(r.renderPositions[0],r.renderPositions[1],r.width,r.height).map(v=>v*r.dpr);
  function circle(chooseRing){
    r.selected=chooseRing?0:-1;const pixels=read(),radius=(r.nodeScreenSize(0)+(chooseRing?8:0))/2*r.dpr;
    // White outlines disappear against the white canvas. Inspect the whole
    // node footprint against its composited centre color, so missing nodes or
    // hard-edged discs cannot pass as antialiased colored boundaries.
    const solid=chooseRing?selected:color(pixels,Math.floor(center[0]),Math.floor(center[1]));
    let partial=0,painted=0;
    for(let y=Math.floor(center[1]-radius-2);y<=Math.ceil(center[1]+radius+2);y++)for(let x=Math.floor(center[0]-radius-2);x<=Math.ceil(center[0]+radius+2);x++){
      const distance=Math.hypot(x+.5-center[0],y+.5-center[1]);if(chooseRing&&Math.abs(distance-radius)>1.1)continue;
      const actual=color(pixels,x,y);if(different(actual,bg,1))painted++;
      if(different(actual,bg,1)&&different(actual,solid,1))partial++;
    }
    return {partial,painted};
  }
  const node=circle(false),ring=circle(true),lines=new FullGraph.GraphDataset();
  lines.addNodes([{id:'left',type:'Thing'},{id:'right',type:'Thing'}]);lines.addEdges([{id:'diagonal',subject_id:'left',object_id:'right'}]);
  r.setDataset(lines);r.setSessionManifest({id:'pixel-line',entity_count:2,relation_count:1});r.sync();
  const pixels=read(),positions=r.renderPositions,endpoints=[0,1].map(i=>r.camera.worldToScreen(positions[i*2],positions[i*2+1],r.width,r.height).map(v=>v*r.dpr));
  const [[ax,ay],[bx,by]]=endpoints,vx=bx-ax,vy=by-ay,length=Math.hypot(vx,vy),solid=edge.map((v,i)=>Math.round(v*.5+bg[i]*.5));
  let partial=0,painted=0;
  for(let y=0;y<r.canvas.height;y++)for(let x=0;x<r.canvas.width;x++){
    const along=((x+.5-ax)*vx+(y+.5-ay)*vy)/length;if(along<30*r.dpr||along>length-30*r.dpr)continue;
    const distance=Math.abs((x+.5-ax)*vy-(y+.5-ay)*vx)/length;if(distance>2*r.dpr)continue;
    const actual=color(pixels,x,y);if(different(actual,bg))painted++;
    if(different(actual,bg)&&different(actual,solid))partial++;
  }
  const result={dpr:devicePixelRatio,backing:[r.canvas.width,r.canvas.height],css:[r.width,r.height],renderer,node,ring,line:{partial,painted},gl_error:gl.getError()};
  r.destroy();return result;
}"""


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, default=WEB / 'full-graph.js')
    parser.add_argument('--output', type=Path, default=ROOT / '.benchmarks/graph-sharpness/after.json')
    parser.add_argument('--record-only', action='store_true')
    args = parser.parse_args()
    reports = []
    with sync_playwright() as runtime:
        browser = runtime.chromium.launch(headless=True, executable_path=r'C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe', args=FLAGS)
        for dpr in (1, 1.25, 2):
            context = browser.new_context(viewport={'width': 600, 'height': 360}, device_scale_factor=dpr)
            page = context.new_page()
            page.set_content('<div id="host" style="width:480px;height:240px;position:relative"></div>')
            page.add_style_tag(path=str(WEB / 'full-graph.css'))
            for source in (WEB / 'graph-palette.js', WEB / 'graph-appearance.js', args.source):
                page.add_script_tag(path=str(source))
            reports.append(page.evaluate(PROBE))
            context.close()
        browser.close()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(reports, indent=2), encoding='utf-8')
    print(json.dumps(reports))
    for report in reports:
        assert 'SwiftShader' in report['renderer'], report
        assert report['gl_error'] == 0, report
        assert report['backing'] == [round(n * report['dpr']) for n in report['css']], report
        if not args.record_only:
            for kind in ('node', 'ring', 'line'):
                assert report[kind]['partial'] >= 8, f"DPR {report['dpr']} {kind} has no smooth edge coverage: {report[kind]}"


if __name__ == '__main__':
    main()
