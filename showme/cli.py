"""Command line: `showme serve [IMAGES...] [OPTIONS]` runs the web UI and the MCP endpoint."""
from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request

from . import __version__
from .server import DEFAULT_PORT


def _post(port: int, path: str, data: dict) -> dict:
    r = urllib.request.Request(f"http://127.0.0.1:{port}{path}", json.dumps(data).encode(), method="POST",
                               headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(r, timeout=60) as resp:
        return json.loads(resp.read())


def _running(port: int) -> bool:
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/health", timeout=1) as resp:
            return json.loads(resp.read()).get("app") == "showme"
    except (urllib.error.URLError, OSError, ValueError):
        return False


def main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser(
        prog="showme",
        description="Raw image viewer driven by agents. `showme serve` starts the web UI "
                    "and the MCP endpoint (http://HOST:PORT/mcp).")
    p.add_argument("-V", "--version", action="version", version=__version__)
    sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("serve", help="start the server (web UI + MCP)")
    s.add_argument("images", nargs="*", help="images, directories or globs to open in a first tab")
    s.add_argument("-t", "--title", help="title of that tab")
    s.add_argument("-r", "--recursive", action="store_true", help="recurse into directories")
    s.add_argument("--host", default="127.0.0.1", help="bind address (default 127.0.0.1)")
    s.add_argument("--port", type=int, default=DEFAULT_PORT, help=f"port (default {DEFAULT_PORT}, env SHOWME_PORT)")
    s.add_argument("--public-url", help="base URL used in links given to agents (default http://localhost:PORT/)")
    s.add_argument("--no-browser", action="store_true", help="never open a browser automatically")
    s.add_argument("--no-persist", action="store_true", help="do not save/restore tabs")
    a = p.parse_args(sys.argv[1:] if argv is None else argv)

    images = [x if any(c in x for c in "*?[") else os.path.abspath(os.path.expanduser(x)) for x in a.images]
    if _running(a.port):  # already serving: just open the images there
        if not images:
            sys.exit(f"showme: already running on port {a.port}")
        try:
            r = _post(a.port, "/api/tabs", {"images": images, "title": a.title, "recursive": a.recursive})
        except urllib.error.HTTPError as e:
            sys.exit(f"showme: {json.loads(e.read()).get('error', e)}")
        print(r["url"])
        return

    from .server import serve

    serve(a.host, a.port, not a.no_browser, not a.no_persist, a.public_url,
          initial={"images": images, "title": a.title, "recursive": a.recursive} if images else None)
