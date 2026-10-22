"""Measure real WebGL draw/interaction. Does not claim database integration."""
from argparse import ArgumentParser
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import threading
import time

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = ArgumentParser()
    parser.add_argument('--browser', default='C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe')
    parser.add_argument('--output', default='docs/research/full-graph-capacity.json')
    parser.add_argument('--stability-seconds', type=int, default=0)
    parser.add_argument('--quick', action='store_true')
    args = parser.parse_args()
    handler = partial(SimpleHTTPRequestHandler, directory=str(ROOT))
    server = ThreadingHTTPServer(('127.0.0.1', 0), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    report = {'scope': 'browser renderer; PostgreSQL and Milvus checked separately',
              'targets': {'load_ms': 30000, 'frame_p95_ms': 33, 'pick_p95_ms': 200,
                          'js_heap_budget_bytes': 1500 * 1024**2, 'browser_budget_bytes': 3 * 1024**3},
              'cases': [], 'errors': []}
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(executable_path=args.browser, headless=True)
            page = browser.new_page(viewport={'width': 1280, 'height': 1000})
            page.on('pageerror', lambda e: report['errors'].append(str(e)))
            page.goto(f'http://127.0.0.1:{server.server_port}/tests/service/fixtures/full-graph-capacity.html')
            page.set_default_timeout(120000)
            cases = [(150000, 300000)] if args.quick else [(1000, 2000), (10000, 20000), (50000, 100000), (150000, 300000), (150000, 1000000)]
            for nodes, edges in cases:
                result = page.evaluate('([n,e])=>buildCapacityFixture(n,e)', [nodes, edges])
                interaction = page.evaluate('()=>measureCapacityInteraction(10000)')
                result.update(interaction)
                result['required_entities'] = nodes
                result['required_relations'] = edges
                result['complete'] = (result['loaded_entities'] == result['drawn_entities'] == nodes and result['loaded_relations'] == edges and result['gl_error'] == 0)
                report['cases'].append(result)
                print(json.dumps(result, ensure_ascii=False), flush=True)
            if args.stability_seconds:
                samples = []
                until = time.monotonic() + args.stability_seconds
                while time.monotonic() < until:
                    samples.append(page.evaluate('()=>measureCapacityInteraction(10000)'))
                    if len(samples) % 6 == 0:
                        print('stability seconds', len(samples)*10, flush=True)
                report['stability'] = {'requested_seconds': args.stability_seconds, 'samples': samples}
            page.screenshot(path=str(Path(args.output).with_suffix('.png')), full_page=True)
            browser.close()
    finally:
        server.shutdown()
        target = Path(args.output)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    assert not report['errors'], report['errors']
    assert all(row['complete'] for row in report['cases']), 'Entity/draw counts mismatch'


if __name__ == '__main__':
    main()
