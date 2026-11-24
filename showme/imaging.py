"""Image decoding, statistics, display ranges and thumbnails.

Pixels are never altered for display in the viewer: the raw array is sent to the
browser, which only applies a linear window (lo..hi -> 0..255). Thumbnails use
the exact same window, then a downscale.
"""
from __future__ import annotations

import hashlib
import io
import os
import re
import threading
from collections import OrderedDict
from pathlib import Path

import numpy as np
from PIL import Image

try:
    import tifffile
except ImportError:  # pragma: no cover
    tifffile = None
try:
    import imagecodecs
except ImportError:  # pragma: no cover
    imagecodecs = None

IMAGE_EXTS = {
    ".png", ".jpg", ".jpeg", ".jpe", ".jfif", ".tif", ".tiff", ".btf", ".bmp", ".gif",
    ".webp", ".jp2", ".j2k", ".jpx", ".jxl", ".avif", ".heic", ".pgm", ".ppm", ".pbm",
    ".pnm", ".tga", ".qoi", ".exr", ".npy", ".dds", ".pcx",
}
TIFF_EXTS = {".tif", ".tiff", ".btf"}
JPEG_EXTS = {".jpg", ".jpeg", ".jpe", ".jfif"}
MODES = ("native", "full", "minmax", "manual")


def is_image(path: str | Path) -> bool:
    return Path(path).suffix.lower() in IMAGE_EXTS


# --------------------------------------------------------------------------- decoding

def _to_hwc(a: np.ndarray) -> np.ndarray:
    """Normalize any array to (H, W) or (H, W, C) with C <= 4 (first plane of stacks)."""
    a = np.asarray(a)
    while a.ndim > 3:
        a = a[0]
    if a.ndim == 3:
        if a.shape[-1] <= 4:
            pass
        elif a.shape[0] <= 4:  # planar CHW
            a = np.moveaxis(a, 0, -1)
        else:  # stack of planes
            a = a[0]
    if a.ndim == 3 and a.shape[-1] == 1:
        a = a[..., 0]
    if a.ndim < 2:
        a = a.reshape(1, -1)
    return a


def decode(path: str | Path) -> tuple[np.ndarray, int | None]:
    """Return (array HxW[xC], declared bits per sample or None)."""
    path = str(path)
    ext = Path(path).suffix.lower()
    bits = None
    if ext == ".npy":
        return _to_hwc(np.load(path, mmap_mode="r", allow_pickle=False)), None
    if ext in TIFF_EXTS and tifffile is not None:
        with tifffile.TiffFile(path) as tf:
            page = tf.pages[0]
            arr = page.asarray()
            b = getattr(page, "bitspersample", None)
            bits = int(b) if isinstance(b, (int, np.integer)) else None
        return _to_hwc(arr), bits
    arr = None
    if imagecodecs is not None:
        try:
            arr = imagecodecs.imread(path)
        except Exception:
            arr = None
    if arr is None:
        with Image.open(path) as im:
            if im.mode == "P":
                im = im.convert("RGBA" if "transparency" in im.info else "RGB")
            elif im.mode == "1":
                im = im.convert("L")
            arr = np.asarray(im)
    return _to_hwc(arr), bits


def transport_dtype(a: np.ndarray) -> np.ndarray:
    """Convert to a dtype the browser handles: u8, u16 or f32 (values preserved)."""
    if a.dtype == np.bool_:
        return a.astype(np.uint8)
    if a.dtype in (np.uint8, np.uint16, np.float32):
        return np.ascontiguousarray(a)
    return np.ascontiguousarray(a.astype(np.float32))


def stats(a: np.ndarray) -> dict:
    if a.dtype.kind == "f":
        finite = a[np.isfinite(a)]
        if finite.size == 0:
            return {"min": 0.0, "max": 0.0, "mean": 0.0}
        a = finite
    return {"min": float(a.min()), "max": float(a.max()), "mean": float(a.mean())}


def ranges(a: np.ndarray, declared_bits: int | None, st: dict) -> dict:
    """Display windows for each mode (manual excluded). Returns also inferred bit depth."""
    dt = a.dtype
    mn, mx = st["min"], st["max"]
    minmax = [mn, mx if mx > mn else mn + 1]
    bits = None
    if dt == np.bool_:
        bits, native, full = 1, [0, 1], [0, 1]
    elif dt == np.uint8:
        bits = declared_bits if declared_bits and declared_bits < 8 else 8
        native, full = [0, 2 ** bits - 1], [0, 255]
    elif dt == np.uint16:
        if declared_bits and 8 < declared_bits < 16:
            bits = declared_bits
        else:
            bits = next(b for b in (10, 12, 14, 16) if mx < 2 ** b)
        native, full = [0, 2 ** bits - 1], [0, 65535]
    elif dt.kind in "iu":
        info = np.iinfo(dt)
        bits = dt.itemsize * 8
        native, full = minmax, [float(info.min), float(info.max)]
    else:  # float
        native = [0.0, 1.0] if mn >= 0 and mx <= 1 else minmax
        full = native
    return {"bits": bits, "native": native, "full": full, "minmax": minmax}


def window_for(rng: dict, mode: str, lo=None, hi=None) -> tuple[float, float]:
    if mode == "manual" and lo is not None and hi is not None:
        return float(lo), float(hi) if hi != lo else float(lo) + 1
    return tuple(rng.get(mode if mode in ("native", "full", "minmax") else "native"))


def load_full(path: str) -> tuple[np.ndarray, dict]:
    """Decode + metadata used by the viewer and image_info."""
    a, declared = decode(path)
    st = stats(a)
    rng = ranges(a, declared, st)
    h, w = a.shape[:2]
    c = 1 if a.ndim == 2 else a.shape[2]
    meta = {
        "width": int(w), "height": int(h), "channels": int(c),
        "dtype": str(a.dtype), **{k: round(v, 6) for k, v in st.items()}, **rng,
    }
    return a, meta


def info(path: str) -> dict:
    p = Path(path)
    if not p.is_file():
        return {"path": str(path), "error": "not found"}
    try:
        _, meta = load_full(str(p))
    except Exception as e:  # noqa: BLE001
        return {"path": str(path), "error": f"decode failed: {e}"}
    meta = {k: meta[k] for k in ("width", "height", "channels", "dtype", "bits", "min", "max", "mean")}
    return {"path": str(p), **meta, "bytes": p.stat().st_size}


# --------------------------------------------------------------------------- thumbnails

def to_u8(a: np.ndarray, lo: float, hi: float) -> np.ndarray:
    if a.dtype == np.uint8 and lo == 0 and hi == 255:
        return np.ascontiguousarray(a)
    scale = 255.0 / (hi - lo)
    if a.dtype in (np.uint8, np.uint16, np.bool_):
        n = 256 if a.dtype != np.uint16 else 65536
        lut = np.clip(np.round((np.arange(n, dtype=np.float64) - lo) * scale), 0, 255).astype(np.uint8)
        return lut[a.astype(np.intp) if a.dtype == np.bool_ else a]
    f = (a.astype(np.float32) - lo) * scale
    f = np.nan_to_num(f, nan=0.0, posinf=255.0, neginf=0.0)
    return np.clip(np.round(f), 0, 255).astype(np.uint8)


def _encode(img: Image.Image) -> tuple[bytes, str]:
    buf = io.BytesIO()
    if img.mode in ("RGBA", "LA"):
        img.save(buf, "PNG", compress_level=1)
        return buf.getvalue(), "image/png"
    img.save(buf, "JPEG", quality=90)
    return buf.getvalue(), "image/jpeg"


def make_thumbnail(path: str, size: int, mode: str = "native", lo=None, hi=None) -> tuple[bytes, str]:
    ext = Path(path).suffix.lower()
    if ext in JPEG_EXTS and mode in ("native", "full"):
        with Image.open(path) as im:  # fast path: 8-bit JPEG, identity window
            if im.mode in ("L", "RGB"):
                im.draft(im.mode, (size, size))
                im = im.copy()
                im.thumbnail((size, size), Image.Resampling.LANCZOS)
                return _encode(im)
    a, declared = decode(path)
    st = stats(a)
    lo_, hi_ = window_for(ranges(a, declared, st), mode, lo, hi)
    h, w = a.shape[:2]
    k = max(1, min(h, w) // (size * 2), max(h, w) // (size * 4))
    if k > 1:
        a = a[::k, ::k]
    u = to_u8(np.asarray(a), lo_, hi_)
    c = 1 if u.ndim == 2 else u.shape[2]
    img = Image.fromarray(u, {1: "L", 2: "LA", 3: "RGB", 4: "RGBA"}[c])
    if max(img.size) > size:
        img.thumbnail((size, size), Image.Resampling.LANCZOS)
    return _encode(img)


class ThumbCache:
    """Memory LRU + on-disk cache keyed by (path, mtime, size, window)."""

    def __init__(self, directory: Path | None, mem_items: int = 3000, disk_bytes: int = 2 << 30):
        self.dir = directory
        self.mem: OrderedDict[str, tuple[bytes, str]] = OrderedDict()
        self.mem_items = mem_items
        self.lock = threading.Lock()
        if self.dir:
            self.dir.mkdir(parents=True, exist_ok=True)
            threading.Thread(target=self._trim, args=(disk_bytes,), daemon=True).start()

    def _trim(self, cap: int) -> None:
        try:
            files = [(f.stat().st_mtime, f.stat().st_size, f) for f in self.dir.glob("*/*") if f.is_file()]
        except OSError:
            return
        total = sum(s for _, s, _ in files)
        for _, s, f in sorted(files):
            if total <= cap:
                break
            try:
                f.unlink()
                total -= s
            except OSError:
                pass

    def get(self, path: str, size: int, mode: str, lo, hi) -> tuple[bytes, str]:
        st = os.stat(path)
        key = hashlib.sha1(f"{path}|{st.st_mtime_ns}|{st.st_size}|{size}|{mode}|{lo}|{hi}".encode()).hexdigest()
        with self.lock:
            if key in self.mem:
                self.mem.move_to_end(key)
                return self.mem[key]
        hit = None
        if self.dir:
            for ext, mime in ((".jpg", "image/jpeg"), (".png", "image/png")):
                f = self.dir / key[:2] / (key + ext)
                if f.is_file():
                    hit = (f.read_bytes(), mime)
                    break
        if hit is None:
            hit = make_thumbnail(path, size, mode, lo, hi)
            if self.dir:
                f = self.dir / key[:2] / (key + (".png" if hit[1] == "image/png" else ".jpg"))
                try:
                    f.parent.mkdir(exist_ok=True)
                    f.write_bytes(hit[0])
                except OSError:
                    pass
        with self.lock:
            self.mem[key] = hit
            while len(self.mem) > self.mem_items:
                self.mem.popitem(last=False)
        return hit


# --------------------------------------------------------------------------- path expansion

def natural_key(s: str):
    return [int(t) if t.isdigit() else t.lower() for t in re.split(r"(\d+)", s)]


def expand(spec: str, recursive: bool = False) -> list[str]:
    """Expand a file, directory or glob into a sorted list of image paths."""
    import glob as _glob

    spec = os.path.expanduser(spec)
    if any(ch in spec for ch in "*?["):
        out = [p for p in _glob.glob(spec, recursive=True) if os.path.isfile(p) and is_image(p)]
        return sorted((os.path.abspath(p) for p in out), key=natural_key)
    p = Path(spec)
    if p.is_dir():
        it = p.rglob("*") if recursive else p.iterdir()
        out = [str(f.absolute()) for f in it if f.is_file() and is_image(f)]
        return sorted(out, key=natural_key)
    return [str(p.absolute())]
