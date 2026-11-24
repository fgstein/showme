import asyncio
import gzip
import json

import numpy as np
import pytest
import tifffile
from PIL import Image

from showme import imaging, mcp
from showme.server import create_app
from showme.state import Store, UserError


@pytest.fixture
def imgs(tmp_path):
    y, x = np.mgrid[0:30, 0:40]
    tifffile.imwrite(tmp_path / "a12.tif", (x * 100).astype(np.uint16))
    Image.fromarray((x * 6).astype(np.uint8)).save(tmp_path / "b8.png")
    np.save(tmp_path / "c.npy", (x / 40).astype(np.float64))
    return tmp_path


def test_decode_and_ranges(imgs):
    a, meta = imaging.load_full(str(imgs / "a12.tif"))
    assert a.dtype == np.uint16 and meta["bits"] == 12 and meta["native"] == [0, 4095]
    assert meta["full"] == [0, 65535] and meta["minmax"] == [0, 3900]
    a, meta = imaging.load_full(str(imgs / "c.npy"))
    assert imaging.transport_dtype(a).dtype == np.float32 and meta["native"] == [0.0, 1.0]
    a, meta = imaging.load_full(str(imgs / "b8.png"))
    assert meta["native"] == [0, 255] and a[0, 10] == 60  # raw values untouched


def test_thumbnail(imgs):
    for f in ("a12.tif", "b8.png", "c.npy"):
        data, mime = imaging.make_thumbnail(str(imgs / f), 16, "minmax")
        assert mime == "image/jpeg" and len(data) > 100


def test_store_ops(imgs):
    s = Store()
    r = s.open_tab([str(imgs), {"path": str(imgs / "b8.png"), "flag": "red", "note": "look"}], title="t")
    assert r["count"] == 4 and r["flagged"] == 1
    tid = r["tab"]
    assert s.flag(tid, ["a12.tif", 0, "*.npy"])["changed"] == 3
    assert s.items(tid, flagged_only=True)["total"] == 3
    assert s.flag(tid, [-1], unflag=True)["flagged"] == 2
    s.add_images(tid, [str(imgs / "*.png")])
    assert s.summary(s.tab(tid))["count"] == 5
    assert s.set_focus(tid, "b8.png")["index"] == 1
    with pytest.raises(UserError):
        s.update_tab(tid, display="manual")
    s.update_tab(tid, display="manual", lo=0, hi=10)
    assert s.close("all")["open"] == 0


def test_mcp_protocol(imgs):
    s = Store()

    async def run_sync(fn, *a):
        return fn(*a)

    def call(method, params=None, id_=1):
        return asyncio.run(mcp.handle(s, {"jsonrpc": "2.0", "id": id_, "method": method, "params": params or {}}, run_sync))

    assert call("initialize", {"protocolVersion": "2025-06-18"})["result"]["protocolVersion"] == "2025-06-18"
    assert len(call("tools/list")["result"]["tools"]) == len(mcp.TOOLS)
    r = call("tools/call", {"name": "open_tab", "arguments": {"images": [str(imgs)]}})["result"]
    assert not r["isError"] and json.loads(r["content"][0]["text"])["count"] == 3
    r = call("tools/call", {"name": "image_info", "arguments": {"paths": [str(imgs / "a12.tif")]}})["result"]
    assert json.loads(r["content"][0]["text"])[0]["bits"] == 12
    r = call("tools/call", {"name": "focus", "arguments": {"tab": "zzz"}})["result"]
    assert r["isError"]
    assert asyncio.run(mcp.handle(s, {"jsonrpc": "2.0", "method": "notifications/initialized"}, run_sync)) is None


def test_http(imgs, tmp_path):
    from starlette.testclient import TestClient

    c = TestClient(create_app(Store(), tmp_path / "thumbs"))
    tid = c.post("/api/tabs", json={"images": [str(imgs)]}).json()["tab"]
    t = c.get(f"/api/tabs/{tid}").json()
    assert len(t["items"]) == 3
    r = c.get(f"/api/tabs/{tid}/raw/0")
    meta = json.loads(r.headers["x-meta"])
    assert meta["transport"] == "u16" and len(r.content) == 30 * 40 * 2
    assert c.get(f"/api/tabs/{tid}/thumb/1?s=64&d=minmax").headers["content-type"] == "image/jpeg"
    assert c.get(f"/api/tabs/{tid}/raw/9").status_code == 400
    r = c.post("/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                             "params": {"name": "list_tabs", "arguments": {}}})
    assert json.loads(r.json()["result"]["content"][0]["text"])["tabs"][0]["id"] == tid
    assert c.post("/mcp", json={"jsonrpc": "2.0", "method": "notifications/initialized"}).status_code == 202
    assert c.post("/api/tabs", json={"images": []}).status_code == 400
    assert c.post("/mcp", headers={"Origin": "http://evil.com"}, json={}).status_code == 403
