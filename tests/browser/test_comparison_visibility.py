"""Browser integration tests: python -m unittest discover -s tests/browser.

Install requirements-dev.txt and run `python -m playwright install chromium` first.
"""
import json
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import plotly.graph_objects as go
import plotly.io as pio
from playwright.sync_api import sync_playwright


SCRIPT = (Path(__file__).resolve().parents[2] / 'app/static/js/comparison.js').read_bytes()


class Handler(BaseHTTPRequestHandler):
    completed = False
    dimension = 2
    delay_lda = False
    lda_gate = threading.Event()

    def log_message(self, *_args):
        pass

    def do_GET(self):
        if self.path.startswith('/api/'):
            items = []
            for name in ('PCA', 'LDA'):
                ready = name == 'PCA' or Handler.completed
                items.append({'algorithm': name, 'status': 'completed' if ready else 'running',
                              'plot_url': f'/{name}.html' if ready else None,
                              'csv_url': None, 'metrics': {}, 'error': None})
            content = json.dumps({'status': 'completed' if Handler.completed else 'running',
                                  'dataset_name': 'test', 'attributes': [], 'algorithms': items}).encode()
            kind = 'application/json'
        elif self.path == '/comparison.js':
            content, kind = SCRIPT, 'text/javascript'
        elif self.path in ('/PCA.html', '/LDA.html'):
            if self.path == '/LDA.html' and Handler.delay_lda:
                Handler.lda_gate.wait(timeout=15)
            names = ['A', 'B', 'C'] if self.path == '/PCA.html' else ['C', 'A', 'B']
            trace = go.Scatter3d if Handler.dimension == 3 else go.Scatter
            data = [trace(x=[1, 2], y=[1, 2], name=name, mode='markers',
                          **({'z': [1, 2]} if Handler.dimension == 3 else {})) for name in names]
            content = pio.to_html(go.Figure(data), full_html=True, include_plotlyjs=True).encode()
            kind = 'text/html'
        else:
            job = self.path.strip('/') or 'first'
            config = json.dumps({'statusUrl': f'/api/{job}', 'labels': {'progress': '{done}/{total}'}})
            cards = ''.join(f'<section data-algorithm="{name}"><span class="algorithm-status"></span>'
                            '<span class="algorithm-error"></span><div class="algorithm-output"></div></section>'
                            for name in ('PCA', 'LDA'))
            content = (f'<style>iframe {{ width: 700px; height: 500px; }}</style>'
                       f'<div id="comparison-progress"></div><div id="comparison-connection-error"></div>'
                       '<label><input type="checkbox" id="comparison-sync" checked>Synchronize legends</label>'
                       f'<div class="comparison-grid">{cards}</div>'
                       f'<script id="comparison-config" type="application/json">{config}</script>'
                       '<script src="/comparison.js" defer></script>').encode()
            kind = 'text/html'
        self.send_response(200)
        self.send_header('Content-Type', kind)
        self.send_header('Content-Length', str(len(content)))
        self.end_headers()
        try:
            self.wfile.write(content)
        except (BrokenPipeError, ConnectionResetError):
            pass


class VisibilityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.playwright = sync_playwright().start()
        cls.browser = cls.playwright.chromium.launch(headless=True, args=[
            '--use-gl=angle', '--use-angle=swiftshader', '--enable-unsafe-swiftshader',
        ])
        cls.base = f'http://127.0.0.1:{cls.server.server_port}'

    @classmethod
    def tearDownClass(cls):
        cls.browser.close()
        cls.playwright.stop()
        cls.server.shutdown()
        cls.server.server_close()

    def setUp(self):
        Handler.completed = False
        Handler.delay_lda = False
        self.page = self.browser.new_page()
        self.errors = []
        self.page.on('pageerror', lambda error: self.errors.append(str(error)))

    def tearDown(self):
        self.page.close()
        self.assertEqual(self.errors, [])

    def frame(self, name):
        selector = f'[data-algorithm="{name}"] iframe'
        self.page.wait_for_selector(selector)
        frame = self.page.frame_locator(selector)
        frame.locator('.legendtoggle').first.wait_for()
        return frame

    def state(self, name):
        frame = self.frame(name)
        return frame.locator('.plotly-graph-div').evaluate(
            "g => Object.fromEntries(g.data.map(t => [t.name, t.visible !== false && t.visible !== 'legendonly']))")

    def wait_state(self, name, expected):
        frame = self.frame(name)
        frame.locator('.plotly-graph-div').wait_for()
        self.page.wait_for_function("""([name, expected]) => {
            const f = document.querySelector(`[data-algorithm="${name}"] iframe`);
            const g = f && f.contentDocument.querySelector('.plotly-graph-div');
            return g && g.data && Object.entries(expected).every(([n, v]) => {
                const t = g.data.find(t => t.name === n);
                return t && (t.visible !== false && t.visible !== 'legendonly') === v;
            });
        }""", arg=[name, expected], timeout=15000)

    def check_progressive_sync(self, dimension):
        Handler.dimension, Handler.completed = dimension, False
        self.page.goto(self.base + f'/job-{dimension}')
        pca = self.frame('PCA')
        pca.locator('.legendtoggle').nth(2).click()
        self.wait_state('PCA', {'A': True, 'B': True, 'C': False})
        Handler.completed = True
        self.wait_state('LDA', {'A': True, 'B': True, 'C': False})
        self.frame('LDA').locator('.legendtoggle').nth(1).dblclick()
        self.wait_state('PCA', {'A': True, 'B': False, 'C': False})
        self.page.reload()
        self.wait_state('PCA', {'A': True, 'B': False, 'C': False})
        self.wait_state('LDA', {'A': True, 'B': False, 'C': False})
        self.page.goto(self.base + f'/other-{dimension}')
        self.wait_state('PCA', {'A': True, 'B': True, 'C': True})

    def test_progressive_sync_2d(self):
        self.check_progressive_sync(2)

    def test_progressive_sync_3d(self):
        self.page.goto(self.base + '/webgl-check')
        if not self.page.evaluate("() => !!document.createElement('canvas').getContext('webgl')"):
            self.skipTest('WebGL is unavailable in this browser environment')
        self.check_progressive_sync(3)

    def test_sync_without_session_storage(self):
        Handler.dimension, Handler.completed = 2, True
        self.page.add_init_script("Object.defineProperty(window, 'sessionStorage', {get() { throw new Error('blocked'); }});")
        self.page.goto(self.base + '/blocked')
        self.frame('LDA')
        self.frame('PCA').locator('.legendtoggle').nth(0).click()
        self.wait_state('LDA', {'A': False, 'B': True, 'C': True})
        self.page.locator('#comparison-sync').uncheck()
        self.frame('LDA').locator('.legendtoggle').nth(2).click()
        self.wait_state('LDA', {'A': False, 'B': False, 'C': True})
        self.wait_state('PCA', {'A': False, 'B': True, 'C': True})

    def test_latest_selection_reaches_delayed_iframe(self):
        Handler.dimension, Handler.completed, Handler.delay_lda = 2, True, True
        Handler.lda_gate.clear()
        try:
            self.page.goto(self.base + '/delayed', wait_until='domcontentloaded')
            self.page.wait_for_selector('[data-algorithm="LDA"] iframe')
            pca = self.frame('PCA')
            pca.locator('.legendtoggle').nth(2).click()
            self.wait_state('PCA', {'C': False})
            pca.locator('.legendtoggle').nth(1).click()
            self.wait_state('PCA', {'B': False, 'C': False})
            Handler.lda_gate.set()
            self.wait_state('LDA', {'A': True, 'B': False, 'C': False})
        finally:
            Handler.lda_gate.set()

    def test_optional_sync_and_waiting_survive_reload(self):
        Handler.dimension, Handler.completed = 2, False
        self.page.goto(self.base + '/optional')
        sync = self.page.locator('#comparison-sync')
        self.assertTrue(sync.is_checked())
        sync.uncheck()
        self.frame('PCA').locator('.legendtoggle').nth(2).click()
        self.wait_state('PCA', {'C': False})
        Handler.completed = True
        self.wait_state('LDA', {'A': True, 'B': True, 'C': True})
        self.frame('LDA').locator('.legendtoggle').nth(2).click()
        self.wait_state('LDA', {'B': False})
        self.wait_state('PCA', {'A': True, 'B': True, 'C': False})
        self.page.reload()
        self.assertFalse(sync.is_checked())
        self.wait_state('PCA', {'A': True, 'B': True, 'C': False})
        self.wait_state('LDA', {'A': True, 'B': False, 'C': True})
        sync.check()
        self.wait_state('PCA', {'A': True, 'B': True, 'C': False})
        self.wait_state('LDA', {'A': True, 'B': False, 'C': True})
        self.page.reload()
        self.assertTrue(sync.is_checked())
        self.wait_state('LDA', {'A': True, 'B': False, 'C': True})
        self.frame('PCA').locator('.legendtoggle').nth(0).click()
        self.wait_state('PCA', {'A': False, 'B': True, 'C': False})
        self.wait_state('LDA', {'A': False, 'B': True, 'C': False})
        self.page.goto(self.base + '/different-comparison')
        self.assertTrue(sync.is_checked())
        self.wait_state('PCA', {'A': True, 'B': True, 'C': True})

    def test_new_frame_waits_after_sync_is_reenabled(self):
        Handler.dimension, Handler.completed = 2, False
        self.page.goto(self.base + '/waiting')
        sync = self.page.locator('#comparison-sync')
        sync.uncheck()
        self.frame('PCA').locator('.legendtoggle').nth(2).click()
        self.wait_state('PCA', {'C': False})
        sync.check()
        Handler.completed = True
        self.wait_state('LDA', {'A': True, 'B': True, 'C': True})
        self.frame('LDA').locator('.legendtoggle').nth(2).click()
        self.wait_state('PCA', {'A': True, 'B': False, 'C': True})

    def test_disable_during_pending_plot_update(self):
        Handler.dimension, Handler.completed = 2, True
        self.page.goto(self.base + '/pending')
        lda = self.frame('LDA')
        self.frame('PCA')
        lda.locator('.plotly-graph-div').evaluate("""g => {
            const original = window.Plotly.restyle;
            window.Plotly.restyle = (...args) => {
                window.Plotly.restyle = original;
                window.__started = true;
                const result = original(...args);
                return Promise.all([result, new Promise(resolve => { window.__finish = resolve; })]);
            };
        }""")
        self.frame('PCA').locator('.legendtoggle').nth(0).click()
        self.page.wait_for_function("""() => {
            const f = document.querySelector('[data-algorithm="LDA"] iframe');
            return f && f.contentWindow.__started;
        }""")
        self.page.locator('#comparison-sync').uncheck()
        lda.locator('.legendtoggle').nth(2).click()
        self.wait_state('LDA', {'A': False, 'B': False, 'C': True})
        lda.locator('.plotly-graph-div').evaluate('() => window.__finish()')
        self.wait_state('PCA', {'A': False, 'B': True, 'C': True})
        self.page.reload()
        self.wait_state('LDA', {'A': False, 'B': False, 'C': True})


if __name__ == '__main__':
    unittest.main()
