# /// script
# requires-python = ">=3.10"
# dependencies = []
# ///
"""
Vendors the web UI JavaScript into beetsplug/beetstreamnext/web/static/vendor/

    uv run tools/vendor_js.py            # (re)generate the files
    uv run tools/vendor_js.py --check    # fail if the committed files differ from a fresh build (for CI)
"""
from __future__ import annotations

import argparse
import hashlib
import io
import sys
import tarfile
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Optional

##

ROOT = Path(__file__).resolve().parent.parent
OUT_DIR = ROOT / 'beetsplug' / 'beetstreamnext' / 'web' / 'static' / 'vendor'

##

htmx_repo = 'bigskysoftware/htmx'
htmx_url = f'https://raw.githubusercontent.com/{htmx_repo}'
htmx_pinned_commit = '4195bc0dc26b612ea5bea46f5914c6386eadeba3'

htmx_file = f'{htmx_url}/{htmx_pinned_commit}/dist/htmx.min.js'
htmx_SHA256 = 'e484d9171a9db30a39c8f16e3d709d4137f3211c659f8e6125816635033d593f'

hx_sse_file = f'{htmx_url}/{htmx_pinned_commit}/dist/ext/hx-sse.min.js'
hx_sse_SHA256 = '8a834680c4000a9034d79228872372a92e140c810a075cb6d4a76690dfc13085'

alpine_version = '3.17.4'
alpine_tarball = f'https://registry.npmjs.org/@alpinejs/csp/-/csp-{alpine_version}.tgz'
alpine_SHA256 = '4c4cd80495f4bbfac3cc026a191b84f341df885d175d757cba7675bcc7b87ec4'


##

def download(url: str, sha256: Optional[str] = None) -> io.BytesIO:

    with urllib.request.urlopen(urllib.parse.quote(url, safe=':/,'), timeout=120) as resp:
        data = resp.read()

    digest = hashlib.sha256(data).hexdigest()

    if sha256 and digest != sha256:
        sys.exit(f'Checksum mismatch for {url}:\n  expected {sha256}\n  got      {digest}')

    return io.BytesIO(data)


def build() -> dict[str, bytes]:

    files: dict[str, bytes] = {}

    files['htmx/htmx.min.js'] = download(htmx_file, htmx_SHA256).getvalue()
    files['htmx/hx-sse.min.js'] = download(hx_sse_file, hx_sse_SHA256).getvalue()

    with tarfile.open(fileobj=download(alpine_tarball, alpine_SHA256), mode='r:gz') as tar:
        files['alpine/alpine.csp.min.js'] = tar.extractfile('package/dist/cdn.min.js').read()

    return files


def main() -> None:

    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--check', action='store_true', help='just compare with the committed files instead of writing')
    args = parser.parse_args()

    files = build()

    if args.check:
        stale = [name for name, data in files.items() if not (OUT_DIR / name).is_file() or (OUT_DIR / name).read_bytes() != data]

        if stale:
            sys.exit(f'Out of date: {", ".join(sorted(stale))}. Run: uv run tools/vendor_js.py')
        print('JavaScript is up to date.')

        return

    for name, data in files.items():
        (OUT_DIR / name).parent.mkdir(parents=True, exist_ok=True)
        (OUT_DIR / name).write_bytes(data)
        print(f'  {name}: {len(data) / 1024:.1f} KB')


if __name__ == '__main__':
    main()
