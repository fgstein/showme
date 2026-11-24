# showme

A raw image viewer for humans, driven by agents. It is a local web app with tabs, a virtualized grid that handles 100k+ images, and a zoomable single-image viewer with pixel inspection. It also exposes an [MCP](https://modelcontextprotocol.io) endpoint, so any MCP-capable agent can show you images, flag the ones that need your attention and leave notes on them.

- Pixels are shown **raw**. The only display step is a linear window (`lo..hi → 0..255`), and the status bar shows it.
- Formats: PNG, JPEG, TIFF, BMP, GIF, WebP, JPEG 2000, JPEG XL, AVIF, PNM, TGA, EXR, QOI and `.npy`. Bit depths: 8, 12 and 16-bit, and float.
- **Flags** mark images that need attention with a colored border. You can jump to the next flagged image.
- **Notes** are attached to images and can come from the agent or from you. Your notes are sent back to the agent with the image they belong to.

## Install

Packaging is handled by [uv](https://docs.astral.sh/uv/).

```bash
uv tool install git+https://github.com/fgstein/showme    # installs the `showme` command
```

To run from a clone:

```bash
uv run showme serve
```

## Run

One command starts everything: the web UI, the HTTP API and the MCP endpoint.

```bash
showme serve [IMAGES...] [OPTIONS]
```

| Option | |
|---|---|
| `IMAGES...` | Files, directories or globs to open in a first tab |
| `-t, --title` | Title of that tab |
| `-r, --recursive` | Recurse into directories |
| `--host` | Bind address (default `127.0.0.1`) |
| `--port` | Port (default `8765`, or the `SHOWME_PORT` env variable) |
| `--public-url` | Base URL put in links given to agents, for example when the UI is reached through a tunnel (env `SHOWME_URL`) |
| `--no-browser` | Never open a browser automatically. By default, one opens when a tab is opened and no browser is viewing the UI |
| `--no-persist` | Do not save and restore tabs. By default they are saved in `~/.local/state/showme` |

The UI is served at `http://localhost:8765/` and the MCP endpoint at `http://localhost:8765/mcp`. If a server is already running on the port, `showme serve IMAGES...` opens the images there and prints the tab's link.

## Connect an agent

Add an MCP server of type **Streamable HTTP** that points to `http://localhost:8765/mcp`. Most clients accept a config like:

```json
{ "mcpServers": { "showme": { "url": "http://localhost:8765/mcp" } } }
```

For a client that only supports stdio, use a generic HTTP-to-stdio bridge, such as `npx mcp-remote http://localhost:8765/mcp`.

### Tools

| Tool | Purpose |
|---|---|
| `open_tab` | Show images (paths, dirs, globs, or `{path,label,flag,note}`) in a new tab, with an optional `title` |
| `add_images` | Append images to a tab |
| `flag` | Flag or unflag images (color, optional note). Targets are indices, paths, basenames or globs |
| `note` | Attach a note to images |
| `get_replies` | Get the user's new notes (`wait` blocks for up to N seconds) |
| `focus` | Bring a tab to the front, optionally opening one image |
| `get_link` | URL of a tab or of one image, to send to the user |
| `update_tab` | Change the title, note or display window |
| `close_tabs` | Close tabs by id, or `"all"` |
| `list_tabs` / `get_items` | Read tabs and items with their notes (paginated, `flagged_only`, `noted_only`) |
| `get_view` | What the user is looking at |
| `image_info` | Size, dtype, bit depth and min/max/mean of files |

**Notes from the user.** Each note you write is delivered to the agent once, as `{tab, i, path, text, reply_to}`, where `reply_to` is the agent note being answered. The agent receives it in two ways:

- **Automatically:** it is appended as `user_replies` to the result of the agent's next tool call, whatever that tool is.
- **On demand:** the agent can call `get_replies(wait=…)` to wait for it.

**Links.** Results include a `url`:

- `http://localhost:8765/#/<tab>` opens a tab's grid.
- `http://localhost:8765/#/<tab>/<index>` opens one image.

The address bar follows your navigation, so you can copy links too.

## Display windows

| Mode | Window |
|---|---|
| `native` (default) | 0..2^bits−1. Bits come from the TIFF tag, else are inferred from the max (10/12/14/16). Float in [0,1] uses 0..1 |
| `full` | Full range of the dtype |
| `minmax` | Per-image min..max (normalization) |
| `manual` | `lo..hi` |

## Keyboard

`A`/`←` and `Z`/`→`: previous/next · `↑`/`↓`: row (grid) or zoom (image) · `Enter`/`Esc`: image/grid · `F`/`Shift+F`: next/previous flagged · `X`: toggle your flag · `C`: add a note · `T`/`Shift+T`: next/previous image with notes · `0`/`1`/`+`/`-`: fit/1:1/zoom · `N`: toggle min/max · `I`: info panel · `/`: filter · `[`/`]`: switch tab · `W`: close tab · `?`: help.

Mouse: wheel zooms, drag pans, double-click toggles fit and 1:1. At high zoom, pixel values are drawn on the pixels.

## Develop

```bash
uv sync
uv run pytest -q
uv build
```
