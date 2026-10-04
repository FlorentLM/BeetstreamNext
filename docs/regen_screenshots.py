"""
Regenerates the Web UI screenshots used in the docs

    uv run --with playwright playwright install chromium
    uv run --with playwright python docs/regen_screenshots.py

Options:
    --theme {light,dark,both}
    --only NAME [NAME ...]
    --out DIR
"""
from __future__ import annotations

import argparse
import os
import sys
import threading
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))


# The sandbox overrides $HOME so browser location pinned first
if 'PLAYWRIGHT_BROWSERS_PATH' not in os.environ:
    _real_cache = Path.home() / {'darwin': 'Library/Caches', 'win32': 'AppData/Local'}.get(sys.platform, '.cache')
    os.environ['PLAYWRIGHT_BROWSERS_PATH'] = str(_real_cache / 'ms-playwright')

from tests.demo import ADMIN_USER, ADMIN_PASSWORD, add_albums, build_app, seed_demo, use_sandbox, CORE_ALBUMS

SANDBOX = use_sandbox()     # before anything imports beetsplug

MAX_HEIGHT = 1400       # px: very tall tabs -> cropped to that
VIEWPORT = {'width': 1280, 'height': 900}
SCALE = 2

BEETS_CONFIG = '''directory: /music
library: /config/library.db

import:
  copy: yes
  write: yes
  incremental: yes

paths:
  default: $albumartist/$album%aunique{}/$track $title

plugins: fetchart embedart lastgenre
'''

SHOTS = {
    'home': ('/', None, None),
    'users': ('/admin/', 'users', '#tab-users'),
    'server': ('/admin/', 'server', '#tab-server'),
    'library': ('/admin/', 'library', '#tab-library'),
    'audio': ('/admin/', 'audio', '#tab-audio'),
    'security': ('/admin/', 'security', '#tab-security'),
    'radios': ('/admin/', 'radios', '#tab-radios'),
    'podcasts': ('/admin/', 'podcasts', '#tab-podcasts'),
    'chat': ('/admin/', 'chat', '#tab-chat'),
    'beets': ('/admin/', 'beets', '#tab-beets'),
}


def serve(app):
    from werkzeug.serving import make_server

    server = make_server('127.0.0.1', 0, app, threaded=True)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, f'http://127.0.0.1:{server.server_port}'


def capture(browser, base, theme, names, out_dir, masks):
    ctx = browser.new_context(
        viewport=VIEWPORT,
        device_scale_factor=SCALE,
        color_scheme=theme,
        bypass_csp=True   # to inject the no-animation stylesheet
    )
    ctx.add_cookies([{'name': 'bsn-theme', 'value': theme, 'url': base}])
    page = ctx.new_page()

    page.goto(f'{base}/login')
    page.fill('input[name=username]', ADMIN_USER)
    page.fill('input[name=password]', ADMIN_PASSWORD)
    page.click('button[type=submit]')
    page.wait_for_url(f'{base}/admin/**')

    theme_dir = out_dir / theme
    theme_dir.mkdir(parents=True, exist_ok=True)

    for name in names:
        path, tab, selector = SHOTS[name]
        page.goto(f'{base}{path}' if tab is None else f'{base}{path}#{tab}')
        page.reload()

        page.wait_for_load_state('load')
        page.add_style_tag(content='*, *::before, *::after { animation: none !important; transition: none !important; caret-color: transparent !important; }')

        page.wait_for_timeout(800)     # let htmx/Alpine fill in

        # Hide values specific to this run (random port, sandbox paths)
        page.evaluate('''(masks) => {
            const swap = (str) => Object.entries(masks).reduce((acc, [k, v]) => acc.split(k).join(v), str);
            document.querySelectorAll('input[type=text], input:not([type])').forEach(el => {
                el.value = swap(el.value);
                if (el.placeholder) el.placeholder = swap(el.placeholder);
            });
            const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
            for (let n = walker.nextNode(); n; n = walker.nextNode()) n.nodeValue = swap(n.nodeValue);
        }''', masks)

        if name == 'beets':       # no beets config.yaml in the sandbox: show the dummy one
            page.evaluate('(text) => { const el = document.getElementById("beetsConfigEditor"); if (el && !el.value) el.value = text; }', BEETS_CONFIG)

        out = theme_dir / f'screenshot_{name}.png'
        if selector:
            box = page.locator(selector).bounding_box()
            page.screenshot(path=str(out), full_page=True, clip={
                'x': box['x'], 'y': box['y'], 'width': box['width'], 'height': min(box['height'], MAX_HEIGHT)})
        else:
            page.screenshot(path=str(out), full_page=True)
        print(f'  {out.relative_to(REPO_ROOT)}')

    ctx.close()


def main():

    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--theme', choices=['light', 'dark', 'both'], default='both')
    parser.add_argument('--only', nargs='+', choices=sorted(SHOTS), metavar='NAME')
    parser.add_argument('--out', type=Path, default=REPO_ROOT / 'docs' / 'src' / 'images')
    args = parser.parse_args()

    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        sys.exit('Playwright is missing. Run with: uv run --with playwright python docs/regen_screenshots.py')

    (SANDBOX / 'app').mkdir()
    app, lib = build_app(SANDBOX / 'app')
    add_albums(lib, CORE_ALBUMS)
    seed_demo(app, lib, import_root=SANDBOX / 'imports')
    server, base = serve(app)

    masks = {str(SANDBOX): '/srv', base: 'http://music.example.com:8080'}
    themes = ['light', 'dark'] if args.theme == 'both' else [args.theme]
    names = args.only or list(SHOTS)
    args.out.mkdir(parents=True, exist_ok=True)

    with sync_playwright() as p:
        browser = p.chromium.launch()
        for theme in themes:
            capture(browser, base, theme, names, args.out, masks)
        browser.close()

    server.shutdown()


if __name__ == '__main__':
    main()
