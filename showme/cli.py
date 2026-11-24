"""Command line: open images, run the server, stdio MCP bridge."""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

from . import __version__
from .server import DEFAULT_PORT, data_dirs

USAGE = """\
showme [open] PATH... [options]   open images in a new tab (starts the server if needed)
showme serve [options]            run the server in the foreground
showme mcp [--port N]             MCP over stdio (bridges to the server, starting it if needed)
showme ls | close ID...|all | stop

PATH: image file, directory or glob. Server: http://localhost:%d (MCP at /mcp).
""" % DEFAULT_PORT


def _req(port: int, method: str, path: str, data=None, timeout: float = 30):
    body = None if data is None else json.dumps(data).encode()
    r = urllib.request.Request(f"http://127.0.0.1:{port}{path}", body, method=method,
                               headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(r, timeout=timeout) as resp:
        raw = resp.read()
        return json.loads(raw) if raw else None


def _alive(port: int) -> dict | None:
    try:
        h = _req(port, "GET", "/api/health", timeout=1)
        return h if h and h.get("app") == "showme" else None
    except (urllib.error.URLError, OSError, ValueError):
        return None


def ensure_server(port: int, browser: bool = True) -> dict:
    h = _alive(port)
    if h:
        return h
    state_dir, _ = data_dirs()
    state_dir.mkdir(parents=True, exist_ok=True)
    cmd = [sys.executable, "-m", "showme", "serve", "--port", str(port)]
    if not browser:
        cmd.append("--no-browser")
    with open(state_dir / "server.log", "ab") as log:
        subprocess.Popen(cmd, stdin=subprocess.DEVNULL, stdout=log, stderr=log, start_new_session=True)
    for _ in range(100):
        time.sleep(0.1)
        h = _alive(port)
        if h:
            return h
    sys.exit(f"showme: server did not start (see {state_dir / 'server.log'})")


def _fail(e: urllib.error.HTTPError):
    try:
        msg = json.loads(e.read()).get("error")
    except Exception:  # noqa: BLE001
        msg = str(e)
    sys.exit(f"showme: {msg}")


def cmd_open(argv: list[str]) -> None:
    p = argparse.ArgumentParser(prog="showme", usage=USAGE)
    p.add_argument("paths", nargs="+")
    p.add_argument("-t", "--title")
    p.add_argument("-n", "--note")
    p.add_argument("-r", "--recursive", action="store_true")
    p.add_argument("-d", "--display", choices=["native", "full", "minmax", "manual"])
    p.add_argument("--lo", type=float)
    p.add_argument("--hi", type=float)
    p.add_argument("-f", "--flag", action="append", default=[], metavar="PATH",
                   help="flag this image (repeatable)")
    p.add_argument("--tab", help="append to this tab instead of opening a new one")
    p.add_argument("--background", action="store_true")
    p.add_argument("--port", type=int, default=DEFAULT_PORT)
    a = p.parse_args(argv)
    ensure_server(a.port)
    paths = [os.path.abspath(os.path.expanduser(x)) if not any(c in x for c in "*?[") else x for x in a.paths]
    try:
        if a.tab:
            r = _req(a.port, "POST", f"/api/tabs/{a.tab}/items", {"images": paths, "recursive": a.recursive})
        else:
            r = _req(a.port, "POST", "/api/tabs", {
                "images": paths, "title": a.title, "note": a.note, "display": a.display, "lo": a.lo, "hi": a.hi,
                "recursive": a.recursive, "background": a.background})
        if a.flag:
            f = _req(a.port, "POST", f"/api/tabs/{r['tab']}/flags", {"targets": a.flag})
            r["flagged"] = f["flagged"]
    except urllib.error.HTTPError as e:
        _fail(e)
    print(json.dumps(r))


def cmd_serve(argv: list[str]) -> None:
    p = argparse.ArgumentParser(prog="showme serve")
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=DEFAULT_PORT)
    p.add_argument("--no-browser", action="store_true", help="never open a browser automatically")
    p.add_argument("--no-persist", action="store_true", help="do not save/restore tabs")
    p.add_argument("--public-url", help="base URL used in links given to agents (default http://localhost:PORT/)")
    a = p.parse_args(argv)
    from .server import serve

    serve(a.host, a.port, not a.no_browser, not a.no_persist, a.public_url)


def cmd_mcp(argv: list[str]) -> None:
    """Forward newline-delimited JSON-RPC from stdin to the HTTP MCP endpoint."""
    p = argparse.ArgumentParser(prog="showme mcp")
    p.add_argument("--port", type=int, default=DEFAULT_PORT)
    p.add_argument("--session", help="name used for tabs the agent does not title (default: agent session name)")
    a = p.parse_args(argv)
    started = False
    sess_cache = [0.0, ""]

    def session_name() -> str:
        if a.session or os.environ.get("SHOWME_SESSION"):
            return a.session or os.environ["SHOWME_SESSION"]
        if time.time() - sess_cache[0] > 30:  # the session may be renamed while running
            sess_cache[:] = [time.time(), claude_session_title() or os.path.basename(os.getcwd()) or "agent"]
        return sess_cache[1]

    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        if not started:
            ensure_server(a.port)
            started = True
        for attempt in range(2):
            try:
                req = urllib.request.Request(f"http://127.0.0.1:{a.port}/mcp", line.encode(), method="POST",
                                             headers={"Content-Type": "application/json",
                                                      "Accept": "application/json, text/event-stream",
                                                      "X-Showme-Session": urllib.parse.quote(session_name())})
                with urllib.request.urlopen(req, timeout=300) as resp:
                    out = resp.read().decode()
                break
            except urllib.error.HTTPError as e:
                out = e.read().decode()
                break
            except (urllib.error.URLError, OSError) as e:
                if attempt == 0:
                    ensure_server(a.port)
                    continue
                try:
                    mid = json.loads(line).get("id")
                except Exception:  # noqa: BLE001
                    mid = None
                out = json.dumps({"jsonrpc": "2.0", "id": mid, "error": {"code": -32000, "message": str(e)}})
        if out.strip():
            sys.stdout.write(out.strip() + "\n")
            sys.stdout.flush()


def claude_session_title() -> str | None:
    """Best effort: title of the Claude Code session that launched this bridge (custom title, else AI title)."""
    sid = os.environ.get("CLAUDE_CODE_SESSION_ID") or os.environ.get("CLAUDE_SESSION_ID")
    if not sid:
        return None
    import glob

    files = glob.glob(os.path.expanduser(f"~/.claude/projects/*/{glob.escape(sid)}.jsonl"))
    if not files:
        return None
    titles: dict[str, str] = {}
    try:
        with open(files[0], encoding="utf-8", errors="replace") as f:
            for line in f:
                if '"customTitle"' not in line and '"aiTitle"' not in line and '"summary"' not in line[:200]:
                    continue
                try:
                    d = json.loads(line)
                except ValueError:
                    continue
                for k in ("customTitle", "aiTitle", "summary"):
                    if isinstance(d.get(k), str) and d[k].strip():
                        titles[k] = d[k].strip()
    except OSError:
        return None
    return next((titles[k] for k in ("customTitle", "aiTitle", "summary") if k in titles), None)


def main(argv: list[str] | None = None) -> None:
    argv = sys.argv[1:] if argv is None else argv
    if not argv or argv[0] in ("-h", "--help"):
        print(USAGE)
        return
    cmd, rest = argv[0], argv[1:]
    if cmd in ("-V", "--version"):
        print(__version__)
    elif cmd == "serve":
        cmd_serve(rest)
    elif cmd == "mcp":
        cmd_mcp(rest)
    elif cmd in ("ls", "close", "stop"):
        port = int(os.environ.get("SHOWME_PORT", DEFAULT_PORT))
        if not _alive(port):
            sys.exit("showme: server not running")
        try:
            if cmd == "ls":
                for t in _req(port, "GET", "/api/tabs")["tabs"]:
                    print(f"{t['id']}  {t['count']:>7} img  {t['flagged']:>5} flagged  {t['title']}")
            elif cmd == "close":
                for tid in rest or sys.exit("showme close ID...|all"):
                    _req(port, "DELETE", f"/api/tabs/{tid}")
            else:
                _req(port, "POST", "/api/shutdown")
        except urllib.error.HTTPError as e:
            _fail(e)
    elif cmd == "open":
        cmd_open(rest)
    else:
        cmd_open(argv)
