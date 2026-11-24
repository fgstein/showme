"""HTTP server: web UI, REST API (UI + CLI), SSE events and the MCP endpoint."""
from __future__ import annotations

import asyncio
import contextlib
import gzip
import json
import os
import threading
import time
import webbrowser
from pathlib import Path
from urllib.parse import urlparse

import anyio
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import FileResponse, JSONResponse, Response, StreamingResponse
from starlette.routing import Mount, Route
from starlette.staticfiles import StaticFiles

from . import __version__, imaging, mcp
from .state import Store, UserError

STATIC = Path(__file__).parent / "static"
DEFAULT_PORT = int(os.environ.get("SHOWME_PORT", "8765"))


def data_dirs() -> tuple[Path, Path]:
    state = Path(os.environ.get("XDG_STATE_HOME", Path.home() / ".local/state")) / "showme"
    cache = Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache")) / "showme"
    return state, cache


def create_app(store: Store | None = None, thumb_dir: Path | None = None, open_browser: bool = False,
               url: str = "") -> Starlette:
    store = store or Store()
    if url:
        store.base_url = url
    thumbs = imaging.ThumbCache(thumb_dir)
    limiter = anyio.CapacityLimiter(max(2, os.cpu_count() or 2))
    thumb_limiter = anyio.CapacityLimiter(max(2, os.cpu_count() or 2))
    last_open = [0.0]

    def maybe_open_browser():
        if open_browser and store.clients == 0 and time.time() - last_open[0] > 15:
            last_open[0] = time.time()
            threading.Thread(target=webbrowser.open, args=(url,), daemon=True).start()

    store.on_open = maybe_open_browser

    async def run_sync(fn, *args):
        return await anyio.to_thread.run_sync(lambda: fn(*args), limiter=limiter)

    def jerr(msg: str, code: int = 400) -> JSONResponse:
        return JSONResponse({"error": msg}, status_code=code)

    def local_origin(request: Request) -> bool:
        origin = request.headers.get("origin")
        if not origin:
            return True
        host = urlparse(origin).hostname or ""
        return host in ("localhost", "127.0.0.1", "::1") or origin.rstrip("/") == f"{request.url.scheme}://{request.url.netloc}"

    async def body(request: Request) -> dict:
        try:
            d = await request.json()
        except Exception:  # noqa: BLE001
            raise UserError("invalid JSON body")
        if not isinstance(d, dict):
            raise UserError("JSON object expected")
        return d

    def api(fn):
        async def wrapper(request: Request):
            if request.method != "GET" and not local_origin(request):
                return jerr("forbidden origin", 403)
            try:
                return await fn(request)
            except UserError as e:
                return jerr(str(e))
        return wrapper

    def item(request: Request) -> dict:
        t = store.tab(request.path_params["tab"])
        i = int(request.path_params["i"])
        if not 0 <= i < len(t["items"]):
            raise UserError("index out of range")
        return t["items"][i]

    # ------------------------------------------------------------------ routes
    async def index(request: Request):
        return FileResponse(STATIC / "index.html", headers={"Cache-Control": "no-cache"})

    @api
    async def health(request: Request):
        return JSONResponse({"app": "showme", "version": __version__, "pid": os.getpid(), "clients": store.clients})

    @api
    async def tabs(request: Request):
        if request.method == "GET":
            return JSONResponse(store.snapshot())
        d = await body(request)
        return JSONResponse(store.open_tab(d.get("images"), d.get("title"), d.get("note"), d.get("display"),
                                           d.get("lo"), d.get("hi"), bool(d.get("recursive")),
                                           bool(d.get("background")), d.get("view")))

    @api
    async def tab(request: Request):
        tid = request.path_params["tab"]
        if request.method == "DELETE":
            return JSONResponse(store.close([tid]))
        if request.method == "PATCH":
            d = await body(request)
            return JSONResponse(store.update_tab(tid, d.get("title"), d.get("note"), d.get("display"),
                                                 d.get("lo"), d.get("hi")))
        t = store.tab(tid)
        payload = json.dumps({**store.summary(t), "items": t["items"]}, separators=(",", ":")).encode()
        headers = {"Cache-Control": "no-store"}
        if len(payload) > 4096 and "gzip" in request.headers.get("accept-encoding", ""):
            payload = gzip.compress(payload, 3)
            headers["Content-Encoding"] = "gzip"
        return Response(payload, media_type="application/json", headers=headers)

    @api
    async def tab_items(request: Request):
        d = await body(request)
        return JSONResponse(store.add_images(request.path_params["tab"], d.get("images"), bool(d.get("recursive"))))

    @api
    async def tab_flags(request: Request):
        d = await body(request)
        return JSONResponse(store.flag(request.path_params["tab"], d.get("targets"), d.get("color"), d.get("note"),
                                       bool(d.get("unflag")), d.get("by", "agent")))

    @api
    async def tab_notes(request: Request):
        d = await body(request)
        return JSONResponse(store.note(request.path_params["tab"], d.get("targets"), d.get("text"),
                                       d.get("by", "user")))

    @api
    async def focus(request: Request):
        d = await body(request)
        return JSONResponse(store.set_focus(d.get("tab"), d.get("target"), d.get("view")))

    @api
    async def view(request: Request):
        if request.method == "POST":
            store.set_view(await body(request))
            return JSONResponse({"ok": True})
        return JSONResponse(store.get_view())

    @api
    async def state(request: Request):  # polling fallback when SSE is blocked/buffered by a proxy
        return JSONResponse(store.snapshot(with_focus=True), headers={"Cache-Control": "no-store"})

    @api
    async def close_all(request: Request):
        return JSONResponse(store.close("all"))

    @api
    async def thumb(request: Request):
        it = item(request)
        q = request.query_params
        size = min(1024, max(32, int(q.get("s", 256))))
        mode = q.get("d", "native")
        lo = float(q["lo"]) if q.get("lo") else None
        hi = float(q["hi"]) if q.get("hi") else None
        try:
            async with thumb_limiter:  # skip work for requests the browser already dropped (fast scrolling)
                if await request.is_disconnected():
                    return Response(status_code=204)
                data, mime = await anyio.to_thread.run_sync(thumbs.get, it["path"], size, mode, lo, hi)
        except FileNotFoundError:
            return Response(status_code=404)
        except Exception as e:  # noqa: BLE001
            return Response(str(e), status_code=415)
        return Response(data, media_type=mime, headers={"Cache-Control": "private, max-age=600"})

    @api
    async def raw(request: Request):
        """Raw pixels, interleaved little-endian (u8/u16/f32). Metadata in X-Meta header."""
        it = item(request)
        gz = "gzip" in request.headers.get("accept-encoding", "")

        def work():
            a, meta = imaging.load_full(it["path"])
            t = imaging.transport_dtype(a)
            meta["transport"] = {"uint8": "u8", "uint16": "u16", "float32": "f32"}[str(t.dtype)]
            buf = t.astype(t.dtype.newbyteorder("<"), copy=False).tobytes()
            return meta, (gzip.compress(buf, 1) if gz else buf)

        try:
            meta, buf = await run_sync(work)
        except FileNotFoundError:
            return jerr("file not found", 404)
        except Exception as e:  # noqa: BLE001
            return jerr(f"cannot decode: {e}", 415)
        headers = {"X-Meta": json.dumps(meta), "Cache-Control": "no-store"}
        if gz:
            headers["Content-Encoding"] = "gzip"
        return Response(buf, media_type="application/octet-stream", headers=headers)

    async def events(request: Request):
        q = store.subscribe()

        async def gen():
            try:
                yield ":" + " " * 2048 + "\n\n"  # padding: defeats proxies that buffer small responses
                while True:
                    try:
                        msg = await asyncio.wait_for(q.get(), timeout=4)
                        yield f"data: {json.dumps(msg, separators=(',', ':'))}\n\n"
                    except asyncio.TimeoutError:
                        yield "event: ping\ndata: 1\n\n"
            finally:
                store.unsubscribe(q)

        return StreamingResponse(gen(), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

    async def mcp_endpoint(request: Request):
        if not local_origin(request):
            return jerr("forbidden origin", 403)
        if request.method == "GET":
            return Response(status_code=405, headers={"Allow": "POST"})
        if request.method == "DELETE":
            return Response(status_code=204)
        try:
            msg = await request.json()
        except Exception:  # noqa: BLE001
            return JSONResponse(mcp._err(None, -32700, "parse error"), status_code=400)
        if isinstance(msg, list):
            out = [r for r in [await mcp.handle(store, m, run_sync) for m in msg] if r]
        else:
            out = await mcp.handle(store, msg, run_sync)
        if not out:
            return Response(status_code=202)
        return JSONResponse(out)

    routes = [
        Route("/", index),
        Route("/api/health", health),
        Route("/api/tabs", tabs, methods=["GET", "POST"]),
        Route("/api/tabs/all", close_all, methods=["DELETE"]),
        Route("/api/tabs/{tab}", tab, methods=["GET", "PATCH", "DELETE"]),
        Route("/api/tabs/{tab}/items", tab_items, methods=["POST"]),
        Route("/api/tabs/{tab}/flags", tab_flags, methods=["POST"]),
        Route("/api/tabs/{tab}/notes", tab_notes, methods=["POST"]),
        Route("/api/tabs/{tab}/thumb/{i:int}", thumb),
        Route("/api/tabs/{tab}/raw/{i:int}", raw),
        Route("/api/focus", focus, methods=["POST"]),
        Route("/api/view", view, methods=["GET", "POST"]),
        Route("/api/events", events),
        Route("/api/state", state),
        Route("/mcp", mcp_endpoint, methods=["GET", "POST", "DELETE"]),
        Mount("/static", StaticFiles(directory=STATIC)),
    ]
    @contextlib.asynccontextmanager
    async def lifespan(app):
        yield
        store.flush()

    app = Starlette(routes=routes, lifespan=lifespan)
    app.state.store = store
    return app


def serve(host: str = "127.0.0.1", port: int = DEFAULT_PORT, open_browser: bool = True,
          persist: bool = True, public_url: str | None = None, initial: dict | None = None) -> None:
    import uvicorn

    state_dir, cache_dir = data_dirs()
    store = Store(state_dir / "state.json" if persist else None)
    url = public_url or os.environ.get("SHOWME_URL") or \
        f"http://{'localhost' if host in ('127.0.0.1', '0.0.0.0', '::') else host}:{port}/"
    url = url.rstrip("/") + "/"
    if initial:  # before create_app, so the browser is opened once, below
        store.base_url = url
        print(store.open_tab(initial["images"], initial.get("title"), recursive=bool(initial.get("recursive")))["url"])
    app = create_app(store, cache_dir / "thumbs", open_browser, url)
    print(f"showme {__version__}\n  UI   {url}\n  MCP  {url}mcp", flush=True)
    if open_browser and initial:
        threading.Timer(0.8, webbrowser.open, args=(url,)).start()
    uvicorn.run(app, host=host, port=port, log_level="warning", timeout_graceful_shutdown=1)
