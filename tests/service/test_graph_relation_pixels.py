"""A real WebGL pixel check, independent of the application and its database."""
from pathlib import Path
import unittest

from playwright.sync_api import sync_playwright


class RelationshipPixels(unittest.TestCase):
    def test_sparse_and_cached_relationships_darken_actual_canvas_pixels(self):
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(channel='msedge', headless=True,
                                                  args=['--enable-webgl', '--use-angle=swiftshader'])
            try:
                page = browser.new_page(viewport={'width': 900, 'height': 700})
                page.set_content('<div id="host" style="width:800px;height:600px"></div>')
                page.add_script_tag(path=str(Path(__file__).resolve().parents[2] /
                                             'knowledge_service/web/full-graph.js'))
                for edge_count in (1, 5000):
                    with self.subTest(edge_count=edge_count):
                        self.assert_relationship_pixels(page, edge_count)
            finally:
                browser.close()

    def assert_relationship_pixels(self, page, edge_count):
        result = page.evaluate('''edgeCount => {
          const d=new FullGraph.GraphDataset();
          d.addNodes([{id:'a'},{id:'b'}],new Float32Array([-.7,0,.7,0]));
          d.addEdges(Array.from({length:edgeCount},(_,i)=>({id:'r'+i,subject_id:'a',object_id:'b'})));
          const r=new FullGraph.Renderer(document.getElementById('host'));
          r.setDataset(d);
          const read=()=>{r.draw();const pixels=new Uint8Array(40*6*4);
            r.gl.readPixels(380,297,40,6,r.gl.RGBA,r.gl.UNSIGNED_BYTE,pixels);return Array.from(pixels);};
          r.showEdges=false;const background=read();r.showEdges=true;const lines=read();
          const darkening=background.reduce((max,value,i)=>i%4===1?Math.max(max,value-lines[i]):max,0);
          const result={darkening,gl_error:r.gl.getError(),edge_count:r.dataset.edgeCount};
          r.destroy();return result;
        }''', edge_count)
        self.assertEqual(result['gl_error'], 0)
        self.assertGreaterEqual(result['darkening'], 20,
                                f'A relationship must visibly darken a real canvas pixel: {result}')


if __name__ == '__main__':
    unittest.main()
