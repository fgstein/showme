# showme

A raw image viewer for humans, driven by agents. A local web app with tabs, a virtualized grid (100k+ images), a zoomable single-image viewer with pixel inspection, and an MCP endpoint, so an agent can open images for you and flag the ones that need your attention.

- Pixels are shown **raw**. The only display step is a linear window (`lo..hi → 0..255`), and you can see it in the status bar.
- Supports PNG, JPEG, TIFF, BMP, GIF, WebP, JPEG 2000, JPEG XL, AVIF, PNM, TGA, EXR, QOI and `.npy`, at 8/12/16-bit and float.
- **Flags** mark images that need attention: a colored border, with jump-to-next-flagged.
- **Notes** are threads attached to images. The agent writes a note, the user replies in the UI, and the reply goes back to the agent with the image reference.

## Install

```bash
pip install -e .          # or: pipx install .
```

## CLI

```bash
showme img.png dir/ "data/**/*.tif"   # open a tab (starts the server in the background if needed)
showme dir/ -r -t "Run 42" -n "Check edges" -f bad_017.png -d minmax
showme serve [--host H] [--port 8765] [--public-url URL] [--no-browser] [--no-persist]   # foreground server
showme mcp [--session NAME]            # MCP over stdio (bridges to the server)
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
| `flag` | Flag or unflag images (color, optional note). Targets are indices, paths, basenames or globs |
| `note` | Attach a note to images. The user can reply |
| `get_replies` | Get the user's new notes and replies (`wait` blocks for up to N seconds) |
| `focus` | Bring a tab to the front, optionally opening one image |
| `update_tab` | Change the title, note or display window |
| `close_tabs` | Close tabs by id, or `"all"` |
| `list_tabs` / `get_items` | Read tabs and items with their note threads (paginated, `flagged_only`, `noted_only`); flags you set show as `by: "user"` |
| `get_link` | Return the URL of a tab, or of one image in it, to send to the user |
| `get_view` | Return what the user is looking at |
| `image_info` | Return size, dtype, bit depth and min/max/mean for files |

Every user reply is delivered to the agent once. Each reply looks like `{tab, i, path, text, reply_to}`, where `reply_to` is the agent note being answered. The agent receives replies in two ways:

- **Automatically:** new replies are appended, as `user_replies`, to the result of the agent's next tool call, whatever that tool is.
- **On demand:** the agent can call `get_replies(wait=120)` to wait for the user's answer.

**Tab names.** The agent can name a tab with `title`. Otherwise the tab is named after the agent session, with " 2", " 3"… added to repeats. The stdio bridge finds the session name in this order:

1. `--session NAME` or `SHOWME_SESSION`.
2. The Claude Code session title (best effort: the custom title, else the AI-generated title).
3. The name of the working folder.

Over HTTP, the server reads the name from the `X-Showme-Session` header or the `?session=` parameter of the MCP URL.

**Links.** `open_tab`, `focus`, `get_view` and `list_tabs` return a `url`, and `get_link` builds one for any tab or image:

- `http://localhost:8765/#/<tab>` opens the tab's grid.
- `http://localhost:8765/#/<tab>/<index>` opens one image.

The browser's address bar follows your navigation, so you can copy links too. If the UI is reached through another address, for example over SSH or another host, set it with `--public-url` or `SHOWME_URL`.

The tool descriptions are kept short, so a client loads about 1k tokens.

## Display windows

| Mode | Window |
|---|---|
| `native` (default) | 0..2^bits−1. Bits come from the TIFF tag, else are inferred from the max (10/12/14/16). Float in [0,1] uses 0..1 |
| `full` | Full range of the dtype |
| `minmax` | Per-image min..max (normalization) |
| `manual` | `lo..hi` |

## UI keys

`A`/`←` and `Z`/`→`: previous/next · `↑`/`↓`: row (grid) or zoom (image) · `Enter`/`Esc`: image/grid · `F`/`Shift+F`: next/previous flagged · `X`: toggle your own flag · `C`: write a note or reply · `T`/`Shift+T`: next/previous image with notes · `0`/`1`/`+`/`-`: fit/1:1/zoom · `N`: toggle min/max · `I`: info panel and histogram · `/`: filter · `[`/`]`: switch tab · `W`: close tab · `?`: help.

Mouse: wheel zooms, drag pans, double-click toggles fit and 1:1. At high zoom, pixel values are drawn on the pixels.

## Develop

```bash
pip install -e ".[test]" && pytest -q
```
