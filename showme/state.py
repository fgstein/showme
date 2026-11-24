"""Tabs, items, flags, view state, persistence and change notification."""
from __future__ import annotations

import asyncio
import fnmatch
import json
import os
import re
import secrets
import time
from pathlib import Path

from . import imaging

# Flags are stored by name so the UI theme picks the actual color; #hex is kept as is.
FLAG_COLORS = ("default", "red", "orange", "yellow", "green", "blue", "purple", "pink", "cyan")
DEFAULT_FLAG = "default"
USER_FLAG = "user"
_HEX = re.compile(r"^#[0-9a-fA-F]{3}([0-9a-fA-F]{3})?$")


class UserError(ValueError):
    """Bad input from a client; message is returned verbatim."""


def color_of(c: str | None) -> str:
    c = (c or DEFAULT_FLAG).strip().lower()
    if c in FLAG_COLORS or c == USER_FLAG:
        return c
    if _HEX.match(c):
        return c
    raise UserError(f"invalid color {c!r}: use a name ({', '.join(FLAG_COLORS)}) or #hex")


class Store:
    def __init__(self, state_file: Path | None = None):
        self.tabs: dict[str, dict] = {}
        self.order: list[str] = []
        self.active: str | None = None
        self.view: dict = {}  # what the user is looking at (reported by the UI)
        self.focus_seq = 0
        self.focus: dict | None = None
        self.state_file = state_file
        self._listeners: set[asyncio.Queue] = set()
        self._save_handle = None
        self.on_open = None  # callback when a tab is opened (browser auto-open)
        self.base_url = "http://localhost:8765/"  # set by the server; used for links
        self.inbox: list[dict] = []  # user notes not yet delivered to the agent
        self._inbox_event: asyncio.Event | None = None
        self._load()

    # ------------------------------------------------------------------ persistence
    def _load(self) -> None:
        if not self.state_file or not self.state_file.is_file():
            return
        try:
            data = json.loads(self.state_file.read_text())
            for t in data.get("tabs", []):
                for it in t["items"]:  # legacy single note -> thread
                    if "note" in it:
                        it.setdefault("notes", []).insert(0, {"by": "agent", "text": it.pop("note"), "ts": 0})
                self.tabs[t["id"]] = t
                self.order.append(t["id"])
            self.inbox = data.get("inbox", [])
            self.active = data.get("active") if data.get("active") in self.tabs else (self.order[-1] if self.order else None)
        except Exception:  # noqa: BLE001 - corrupt state is not fatal
            self.tabs, self.order = {}, []

    def _save_now(self) -> None:
        self._save_handle = None
        if not self.state_file:
            return
        data = {"tabs": [self.tabs[i] for i in self.order], "active": self.active, "inbox": self.inbox}
        tmp = self.state_file.with_suffix(".tmp")
        try:
            self.state_file.parent.mkdir(parents=True, exist_ok=True)
            tmp.write_text(json.dumps(data, separators=(",", ":")))
            os.replace(tmp, self.state_file)
        except OSError:
            pass

    def _changed(self, focus: dict | None = None) -> None:
        if focus is not None:
            self.focus_seq += 1
            self.focus = {**focus, "seq": self.focus_seq}
        msg = self.snapshot(with_focus=focus is not None)
        for q in list(self._listeners):
            q.put_nowait(msg)
        try:
            loop = asyncio.get_running_loop()
            if self._save_handle is None:
                self._save_handle = loop.call_later(1.0, self._save_now)
        except RuntimeError:
            self._save_now()

    def flush(self) -> None:
        if self._save_handle:
            self._save_handle.cancel()
        self._save_now()

    # ------------------------------------------------------------------ events
    def subscribe(self) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue()
        self._listeners.add(q)
        q.put_nowait(self.snapshot(with_focus=True))
        return q

    def unsubscribe(self, q: asyncio.Queue) -> None:
        self._listeners.discard(q)

    @property
    def clients(self) -> int:
        return len(self._listeners)

    # ------------------------------------------------------------------ helpers
    def url(self, tab_id: str, index: int | None = None) -> str:
        return f"{self.base_url}#/{tab_id}" + ("" if index is None else f"/{index}")

    def link(self, tab_id, target=None) -> dict:
        t = self.tab(tab_id)
        i = None if target is None else self._resolve(t, [target])[0]
        r = {"url": self.url(t["id"], i), "tab": t["id"]}
        if i is not None:
            r["index"] = i
        return r

    def summary(self, t: dict) -> dict:
        return {
            "id": t["id"], "url": self.url(t["id"]), "title": t["title"], "note": t.get("note", ""), "count": len(t["items"]),
            "flagged": sum(1 for it in t["items"] if it.get("flag")),
            "noted": sum(1 for it in t["items"] if it.get("notes")), "display": t["display"],
            "version": t["version"],
        }

    def snapshot(self, with_focus: bool = False) -> dict:
        d = {"tabs": [self.summary(self.tabs[i]) for i in self.order], "active": self.active,
             "pending_replies": len(self.inbox)}
        if with_focus and self.focus:
            d["focus"] = self.focus
        return d

    def tab(self, tab_id: str | None) -> dict:
        if not tab_id:
            if self.active:
                return self.tabs[self.active]
            raise UserError("no tab open")
        if tab_id not in self.tabs:
            raise UserError(f"unknown tab {tab_id!r}; open tabs: {', '.join(self.order) or 'none'}")
        return self.tabs[tab_id]

    @staticmethod
    def _items(images, recursive: bool, flag_default: str | None = None) -> list[dict]:
        if isinstance(images, (str, dict)):
            images = [images]
        if not images:
            raise UserError("images is empty")
        out: list[dict] = []
        for spec in images:
            if isinstance(spec, str):
                spec = {"path": spec}
            if not isinstance(spec, dict) or not spec.get("path"):
                raise UserError(f"bad image spec {spec!r}")
            paths = imaging.expand(str(spec["path"]), recursive or bool(spec.get("recursive")))
            for p in paths:
                it = {"path": p}
                if spec.get("label") and len(paths) == 1:
                    it["label"] = str(spec["label"])
                flag = spec.get("flag")
                if flag:
                    it["flag"] = color_of(flag if isinstance(flag, str) else None)
                    it["by"] = "agent"
                if spec.get("note"):
                    it["notes"] = [{"by": "agent", "text": str(spec["note"]), "ts": time.time()}]
                out.append(it)
        return out

    def _resolve(self, t: dict, targets) -> list[int]:
        """Targets: indices, paths, basenames or glob patterns on paths."""
        if targets is None:
            raise UserError("targets is required")
        if isinstance(targets, (str, int)):
            targets = [targets]
        n = len(t["items"])
        out: list[int] = []
        index_by_path = None
        for x in targets:
            if isinstance(x, bool):
                raise UserError(f"bad target {x!r}")
            if isinstance(x, int) or (isinstance(x, str) and x.lstrip("-").isdigit()):
                i = int(x)
                i = i + n if i < 0 else i
                if not 0 <= i < n:
                    raise UserError(f"index {x} out of range (0..{n - 1})")
                out.append(i)
                continue
            if index_by_path is None:
                index_by_path = {it["path"]: i for i, it in enumerate(t["items"])}
            ap = os.path.abspath(os.path.expanduser(x))
            if ap in index_by_path:
                out.append(index_by_path[ap])
                continue
            pat = x if any(c in x for c in "*?[") else None
            hits = [i for i, it in enumerate(t["items"])
                    if (fnmatch.fnmatch(it["path"], pat) or fnmatch.fnmatch(os.path.basename(it["path"]), pat)
                        if pat else os.path.basename(it["path"]) == x)]
            if not hits:
                raise UserError(f"no item matches {x!r}")
            out.extend(hits)
        return out

    # ------------------------------------------------------------------ operations
    def open_tab(self, images, title: str | None = None, note: str | None = None, display: str | None = None,
                 lo=None, hi=None, recursive: bool = False, background: bool = False, view: str | None = None,
                 session: str | None = None) -> dict:
        items = self._items(images, recursive)
        tid = secrets.token_hex(3)
        while tid in self.tabs:
            tid = secrets.token_hex(3)
        if not title:
            title = session or (os.path.basename(items[0]["path"]) if len(items) == 1 else f"{len(items)} images")
            taken = {self.tabs[i]["title"] for i in self.order}
            base, n = title, 2
            while title in taken:  # "session", "session 2", ...
                title, n = f"{base} {n}", n + 1
        t = {"id": tid, "title": str(title), "note": note or "", "items": items, "version": 1,
             "display": self._display(display, lo, hi), "created": time.time()}
        self.tabs[tid] = t
        self.order.append(tid)
        focus = None
        if not background:
            self.active = tid
            focus = {"tab": tid, "view": view or ("single" if len(items) == 1 else "grid")}
            if view == "single":
                focus["index"] = 0
        self._changed(focus)
        if self.on_open:
            self.on_open()
        return self._result(t)

    def _result(self, t: dict) -> dict:
        missing = sum(1 for it in t["items"] if not os.path.isfile(it["path"]))
        r = {"tab": t["id"], "title": t["title"], "url": self.url(t["id"]), "count": len(t["items"]),
             "flagged": sum(1 for it in t["items"] if it.get("flag"))}
        if missing:
            r["missing"] = missing
        return r

    @staticmethod
    def _display(mode, lo=None, hi=None) -> dict:
        mode = mode or "native"
        if mode not in imaging.MODES:
            raise UserError(f"display must be one of {', '.join(imaging.MODES)}")
        if mode == "manual" and (lo is None or hi is None):
            raise UserError("manual display needs lo and hi")
        d = {"mode": mode}
        if mode == "manual":
            d.update(lo=float(lo), hi=float(hi))
        return d

    def add_images(self, tab_id, images, recursive: bool = False) -> dict:
        t = self.tab(tab_id)
        t["items"].extend(self._items(images, recursive))
        t["version"] += 1
        self._changed()
        return self._result(t)

    def update_tab(self, tab_id, title=None, note=None, display=None, lo=None, hi=None) -> dict:
        t = self.tab(tab_id)
        if title is not None:
            t["title"] = str(title)
        if note is not None:
            t["note"] = str(note)
        if display is not None:
            t["display"] = self._display(display, lo, hi)
        self._changed()
        return self.summary(t)

    def flag(self, tab_id, targets, color=None, note=None, unflag: bool = False, by: str = "agent") -> dict:
        t = self.tab(tab_id)
        idx = self._resolve(t, targets)
        c = None if unflag else (USER_FLAG if by == "user" and not color else color_of(color))
        for i in idx:
            it = t["items"][i]
            if unflag:
                it.pop("flag", None)
                it.pop("by", None)
            else:
                it["flag"], it["by"] = c, by
                if note:
                    self._append_note(t, i, note, by)
        t["version"] += 1
        self._changed()
        return {"tab": t["id"], "changed": len(idx), "flagged": sum(1 for it in t["items"] if it.get("flag"))}

    # ------------------------------------------------------------------ notes
    def _append_note(self, t: dict, i: int, text: str, by: str) -> None:
        it = t["items"][i]
        thread = it.setdefault("notes", [])
        thread.append({"by": by, "text": str(text), "ts": time.time()})
        if by == "user":
            prev = next((n["text"] for n in reversed(thread[:-1]) if n["by"] == "agent"), None)
            msg = {"tab": t["id"], "i": i, "path": it["path"], "text": str(text)}
            if prev:
                msg["reply_to"] = prev if len(prev) <= 200 else prev[:200] + "…"
            self.inbox.append(msg)
            if self._inbox_event:
                self._inbox_event.set()

    def note(self, tab_id, targets, text, by: str = "agent") -> dict:
        if not text or not str(text).strip():
            raise UserError("text is empty")
        if by not in ("agent", "user"):
            raise UserError("by must be agent or user")
        t = self.tab(tab_id)
        idx = self._resolve(t, targets)
        for i in idx:
            self._append_note(t, i, str(text).strip(), by)
        t["version"] += 1
        self._changed()
        return {"tab": t["id"], "noted": len(idx)}

    def take_replies(self) -> list[dict]:
        out, self.inbox = self.inbox, []
        if out:
            self._changed()
        return out

    async def wait_replies(self, timeout: float) -> list[dict]:
        if not self.inbox and timeout > 0:
            if self._inbox_event is None:
                self._inbox_event = asyncio.Event()
            self._inbox_event.clear()
            try:
                await asyncio.wait_for(self._inbox_event.wait(), timeout)
            except asyncio.TimeoutError:
                pass
        return self.take_replies()

    def close(self, tabs) -> dict:
        if tabs == "all" or tabs == ["all"]:
            ids = list(self.order)
        else:
            ids = [tabs] if isinstance(tabs, str) else list(tabs or [])
            for i in ids:
                self.tab(i)
        for i in ids:
            self.tabs.pop(i, None)
            self.order.remove(i)
        if self.active not in self.tabs:
            self.active = self.order[-1] if self.order else None
        self._changed()
        return {"closed": ids, "open": len(self.order)}

    def set_focus(self, tab_id, target=None, view: str | None = None) -> dict:
        t = self.tab(tab_id)
        self.active = t["id"]
        f = {"tab": t["id"]}
        if target is not None:
            f["index"] = self._resolve(t, [target])[0]
            f["view"] = view or "single"
        elif view:
            f["view"] = view
        if f.get("view") not in (None, "grid", "single"):
            raise UserError("view must be grid or single")
        self._changed(f)
        return {**f, "url": self.url(t["id"], f.get("index"))}

    def items(self, tab_id, offset: int = 0, limit: int = 100, flagged_only: bool = False,
              noted_only: bool = False) -> dict:
        t = self.tab(tab_id)
        rows = [(i, it) for i, it in enumerate(t["items"])
                if (not flagged_only or it.get("flag")) and (not noted_only or it.get("notes"))]
        page = rows[offset: offset + limit]
        return {"tab": t["id"], "title": t["title"], "total": len(rows),
                "items": [{"i": i, **it} for i, it in page]}

    def set_view(self, view: dict) -> None:
        self.view = {k: view.get(k) for k in ("tab", "view", "index") if k in view}
        if view.get("tab") in self.tabs:
            self.active = view["tab"]

    def get_view(self) -> dict:
        v = dict(self.view)
        t = self.tabs.get(v.get("tab") or "")
        if not t:
            return {"tab": None, "ui_connected": self.clients > 0}
        i = v.get("index")
        if isinstance(i, int) and 0 <= i < len(t["items"]):
            v["item"] = {"i": i, **t["items"][i]}
        v["title"] = t["title"]
        v["url"] = self.url(t["id"], i if v.get("view") == "single" and isinstance(i, int) else None)
        v["ui_connected"] = self.clients > 0
        return v
