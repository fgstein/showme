"""Minimal MCP server (JSON-RPC over Streamable HTTP, stateless, JSON responses)."""
from __future__ import annotations

import json

from . import __version__, imaging
from .state import FLAG_COLORS, Store, UserError

PROTOCOLS = ("2025-11-25", "2025-06-18", "2025-03-26", "2024-11-05")

INSTRUCTIONS = (
    "showme displays images to the user in a browser viewer organized in tabs. "
    "Images are shown raw (no processing); 8/12/16-bit and float are supported. "
    "Use open_tab to show images (files, directories or globs; up to 100k). "
    "Flag images that need the user's attention (colored border, optional note). "
    "Tab ids are returned by open_tab/list_tabs; omitting `tab` means the active tab. "
    "Notes on images start a thread the user can reply to; replies are appended to any tool result "
    "(\"user_replies\") or fetched/awaited with get_replies."
)

_IMG = {
    "type": "array",
    "description": "Paths, directories or globs; or objects {path, label?, flag?: color, note?: text}.",
    "items": {"anyOf": [{"type": "string"}, {"type": "object", "properties": {
        "path": {"type": "string"}, "label": {"type": "string"},
        "flag": {"type": "string"}, "note": {"type": "string"}}, "required": ["path"]}]},
}
_TAB = {"type": "string", "description": "Tab id (default: active tab)."}
_TARGETS = {"type": "array", "items": {"type": ["integer", "string"]},
            "description": "Item indices (negative ok), paths, basenames or globs."}
_DISPLAY = {"type": "string", "enum": list(imaging.MODES),
            "description": "native: bit-depth range (default); full: dtype range; minmax: per-image min/max; manual: lo..hi."}
_NUM = {"type": "number"}


def _obj(props: dict, required: list[str] | None = None) -> dict:
    return {"type": "object", "properties": props, "required": required or []}


TOOLS = [
    ("open_tab", "Open a new tab showing images (grid if several). Returns {tab, title, url, count, flagged, missing?}.",
     _obj({"images": _IMG, "title": {"type": "string", "description": "Default: the agent session name."}, "note": {"type": "string", "description": "Shown above the images."},
           "display": _DISPLAY, "lo": _NUM, "hi": _NUM, "recursive": {"type": "boolean"},
           "view": {"type": "string", "enum": ["grid", "single"]},
           "background": {"type": "boolean", "description": "Do not switch the UI to it."}}, ["images"]),
     lambda s, a: s.open_tab(a["images"], a.get("title"), a.get("note"), a.get("display"), a.get("lo"), a.get("hi"),
                             bool(a.get("recursive")), bool(a.get("background")), a.get("view"), a.get("_session"))),
    ("add_images", "Append images to a tab.",
     _obj({"tab": _TAB, "images": _IMG, "recursive": {"type": "boolean"}}, ["images"]),
     lambda s, a: s.add_images(a.get("tab"), a["images"], bool(a.get("recursive")))),
    ("flag", f"Flag images needing attention (colored border, optional note) or unflag them. "
             f"Colors: {', '.join(FLAG_COLORS)} or #hex.",
     _obj({"tab": _TAB, "targets": _TARGETS, "color": {"type": "string"}, "note": {"type": "string"},
           "unflag": {"type": "boolean"}}, ["targets"]),
     lambda s, a: s.flag(a.get("tab"), a["targets"], a.get("color"), a.get("note"), bool(a.get("unflag")))),
    ("note", "Attach a note to images (shown next to them). The user can reply.",
     _obj({"tab": _TAB, "targets": _TARGETS, "text": {"type": "string"}}, ["targets", "text"]),
     lambda s, a: s.note(a.get("tab"), a["targets"], a["text"])),
    ("get_replies", "Get the user's new notes/replies (each with tab, i, path, text, reply_to). "
                    "wait: seconds to block until one arrives (max 600).",
     _obj({"wait": {"type": "number"}}),
     None),  # async, handled below
    ("get_link", "URL of a tab, or of one image in it, to send to the user.",
     _obj({"tab": _TAB, "target": {"type": ["integer", "string"], "description": "Index or path (optional)."}}),
     lambda s, a: s.link(a.get("tab"), a.get("target"))),
    ("focus", "Bring a tab to front; with target, open that image in single view.",
     _obj({"tab": _TAB, "target": {"type": ["integer", "string"], "description": "Index or path."},
           "view": {"type": "string", "enum": ["grid", "single"]}}),
     lambda s, a: s.set_focus(a.get("tab"), a.get("target"), a.get("view"))),
    ("update_tab", "Change a tab's title, note or display mode.",
     _obj({"tab": _TAB, "title": {"type": "string"}, "note": {"type": "string"}, "display": _DISPLAY,
           "lo": _NUM, "hi": _NUM}),
     lambda s, a: s.update_tab(a.get("tab"), a.get("title"), a.get("note"), a.get("display"), a.get("lo"), a.get("hi"))),
    ("close_tabs", "Close tabs by id, or all.",
     _obj({"tabs": {"anyOf": [{"type": "array", "items": {"type": "string"}}, {"const": "all"}]}}, ["tabs"]),
     lambda s, a: s.close(a["tabs"])),
    ("list_tabs", "List open tabs and the active one.", _obj({}), lambda s, a: s.snapshot()),
    ("get_items", "List a tab's items (paginated) with flags and note threads. Flags set by the user have by='user'.",
     _obj({"tab": _TAB, "offset": {"type": "integer"}, "limit": {"type": "integer", "description": "Default 100."},
           "flagged_only": {"type": "boolean"}, "noted_only": {"type": "boolean"}}),
     lambda s, a: s.items(a.get("tab"), int(a.get("offset") or 0), int(a.get("limit") or 100),
                          bool(a.get("flagged_only")), bool(a.get("noted_only")))),
    ("get_view", "What the user is currently looking at (tab, view, current item).", _obj({}),
     lambda s, a: s.get_view()),
    ("image_info", "Read image files: size, channels, dtype, bit depth, min/max/mean.",
     _obj({"paths": {"type": "array", "items": {"type": "string"}}}, ["paths"]),
     None),  # handled in a worker thread by the server
]
TOOL_MAP = {name: (desc, schema, fn) for name, desc, schema, fn in TOOLS}


def tools_list() -> list[dict]:
    return [{"name": n, "description": d, "inputSchema": s} for n, d, s, _ in TOOLS]


def image_info(paths) -> list[dict]:
    if isinstance(paths, str):
        paths = [paths]
    out = []
    for spec in paths:
        for p in imaging.expand(spec)[:1000]:
            out.append(imaging.info(p))
    return out


def _ok(id_, result) -> dict:
    return {"jsonrpc": "2.0", "id": id_, "result": result}


def _err(id_, code: int, msg: str) -> dict:
    return {"jsonrpc": "2.0", "id": id_, "error": {"code": code, "message": msg}}


async def handle(store: Store, msg: dict, run_sync, session: str | None = None) -> dict | None:
    """Handle one JSON-RPC message. `run_sync(fn, *args)` runs blocking work off the loop."""
    if not isinstance(msg, dict) or msg.get("jsonrpc") != "2.0":
        return _err(None, -32600, "invalid request")
    method, id_, params = msg.get("method"), msg.get("id"), msg.get("params") or {}
    if method is None:
        return None  # a response from the client: ignore
    if id_ is None:
        return None  # notification
    if method == "initialize":
        v = params.get("protocolVersion")
        return _ok(id_, {
            "protocolVersion": v if v in PROTOCOLS else PROTOCOLS[0],
            "capabilities": {"tools": {"listChanged": False}},
            "serverInfo": {"name": "showme", "version": __version__},
            "instructions": INSTRUCTIONS,
        })
    if method == "ping":
        return _ok(id_, {})
    if method == "tools/list":
        return _ok(id_, {"tools": tools_list()})
    if method == "tools/call":
        name, args = params.get("name"), dict(params.get("arguments") or {})
        args.pop("_session", None)
        if session:
            args["_session"] = session
        if name not in TOOL_MAP:
            return _err(id_, -32602, f"unknown tool {name!r}")
        try:
            if name == "image_info":
                res = await run_sync(image_info, args.get("paths") or [])
            elif name == "get_replies":
                res = await store.wait_replies(min(600.0, max(0.0, float(args.get("wait") or 0))))
            else:
                res = TOOL_MAP[name][2](store, args)
            text, is_err = json.dumps(res, separators=(",", ":")), False
        except (UserError, KeyError, TypeError, ValueError) as e:
            text, is_err = f"error: {e}", True
        content = [{"type": "text", "text": text}]
        if store.inbox:  # piggyback user replies on any tool result
            replies = json.dumps({"user_replies": store.take_replies()}, separators=(",", ":"))
            content.append({"type": "text", "text": replies})
        return _ok(id_, {"content": content, "isError": is_err})
    if method in ("resources/list", "prompts/list"):
        return _ok(id_, {method.split("/")[0]: []})
    return _err(id_, -32601, f"method not found: {method}")
