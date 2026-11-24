# showme

A raw image viewer for humans, driven by agents. A local web app with tabs, a virtualized grid (100k+ images), a zoomable single-image viewer with pixel inspection, and an MCP endpoint, so an agent can open images for you and flag the ones that need your attention.

- Pixels are shown **raw**. The only display step is a linear window (`lo..hi → 0..255`), and you can see it in the status bar.
- Supports PNG, JPEG, TIFF, BMP, GIF, WebP, JPEG 2000, JPEG XL, AVIF, PNM, TGA, EXR, QOI and `.npy`, at 8/12/16-bit and float.
- **Flags** mark images that need attention: a colored border and an optional note, with jump-to-next-flagged.

## Install

```bash
pip install -e .          # or: pipx install .
```

## CLI

```bash
showme img.png dir/ "data/**/*.tif"   # open a tab (starts the server in the background if needed)
showme dir/ -r -t "Run 42" -n "Check edges" -f bad_017.png -d minmax
showme serve [--host H] [--port 8765] [--no-browser] [--no-persist]   # foreground server
showme mcp                             # MCP over stdio (bridges to the server)
showme ls | showme close ID|all | showme stop
```

The UI is at `http://localhost:8765`. Tabs persist across restarts (`~/.local/state/showme`). Thumbnails are cached in `~/.cache/showme`. Set `SHOWME_PORT` to change the default port. When an agent opens a tab and no browser is viewing the UI, a browser window opens automatically (`--no-browser` turns this off).

## MCP

Use HTTP (`http://localhost:8765/mcp`, stateless Streamable HTTP) or stdio (`showme mcp`, which starts the server if needed).

```bash
claude mcp add showme -- showme mcp                                   # stdio
claude mcp add --transport http showme http://localhost:8765/mcp     # http (server must run)
```

```json
{ "mcpServers": { "showme": { "command": "showme", "args": ["mcp"] } } }
```

| Tool | Purpose |
|---|---|
| `open_tab` | Show images (paths, dirs, globs, or `{path,label,flag,note}`) in a new tab |
| `add_images` | Append images to a tab |
| `flag` | Flag or unflag images (color + note). Targets are indices, paths, basenames or globs |
| `focus` | Bring a tab to the front, optionally opening one image |
| `update_tab` | Change the title, note or display window |
| `close_tabs` | Close tabs by id, or `"all"` |
| `list_tabs` / `get_items` | Read tabs and items (paginated, `flagged_only`); flags you set show as `by: "user"` |
| `get_view` | Return what the user is looking at |
| `image_info` | Return size, dtype, bit depth and min/max/mean for files |

The tool descriptions are kept short, so a client loads about 1k tokens.

## Display windows

| Mode | Window |
|---|---|
| `native` (default) | 0..2^bits−1. Bits come from the TIFF tag, else are inferred from the max (10/12/14/16). Float in [0,1] uses 0..1 |
| `full` | Full range of the dtype |
| `minmax` | Per-image min..max (normalization) |
| `manual` | `lo..hi` |

## UI keys

`A`/`←` and `Z`/`→`: previous/next · `↑`/`↓`: row (grid) or zoom (image) · `Enter`/`Esc`: image/grid · `F`/`Shift+F`: next/previous flagged · `X`: toggle your own flag · `0`/`1`/`+`/`-`: fit/1:1/zoom · `N`: toggle min/max · `I`: info panel and histogram · `/`: filter · `[`/`]`: switch tab · `W`: close tab · `?`: help.

Mouse: wheel zooms, drag pans, double-click toggles fit and 1:1. At high zoom, pixel values are drawn on the pixels.

## Develop

```bash
pip install -e ".[test]" && pytest -q
```
