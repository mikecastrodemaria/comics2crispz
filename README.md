# comics2crispz

Local, **engine-agnostic comic book workshop**: the app generates nothing
itself — it drives the crispz family tools (crispz-studio, crispz-qwen-edit,
crispz-krea…) through the [family CLI protocol v1](docs/CLI_PROTOCOL.md):
JSON spec in, JSON out, routed to the running instance (warm model, one GPU
queue).

A project is a folder with a **crispz-family-compatible `project.json`**
(the documentary engine `cz_comic.py` is vendored from crispz-studio): the
🎞 Comic accordion of crispz-studio, its 🎬 Comic Studio, the `--comic` CLI
and comics2crispz all work on the same file, each of them stateless.

*Version française : [README.fr.md](README.fr.md).*

## Install / run

```bat
start.bat
```

That's it: first run installs everything (venv + Pillow + config + an example
book), then your **most recently edited book opens in the browser**. Inside
the app, the **📚 menu** switches between books and **➕ New book** creates
one (cover + a first 4-panel page, ready to fill); the very first launch
shows a welcome screen. Linux/macOS: `./start.sh`. Power users:

```bat
run.bat books\exemple             REM serve a specific book, no browser
start.bat books\autre --port 8771 REM a second book side by side
```

Dependencies: Python 3.10+, Pillow. The server is pure stdlib
(http.server), no framework. Copy `config-sample.json` to `config.json`
(local, gitignored) and point the `engines` entries to your crispz installs.

## The UI

Four modes on a permanent chapter/page navigator:

| Mode | Status | Content |
|---|---|---|
| **Flatplan** | ✅ step 1 | facing-page spreads (the right-hand page carries the beat), role badges, folios, progress colors (empty / partial / rendered / composed), **drag & drop** to reorder or change chapter (drop on a page = insert before; on a chapter header = append), Compose page/book |
| **Page** | preview + generation | the composed page + panel rects + balloons, **🎨 Generate missing**: panels without an image go to the configured engine through the CLI protocol (prompt resolved by the @Name casting, exact panel ratio, `project.json` saved after each panel, empty panel texts skipped with a warning — an empty prompt is never sent to an engine), then the page recomposes; fine editing (balloon drag, dialogues) = next step — meanwhile crispz-studio's Comic Studio edits the same project |
| **Script** | step 2 | the whole book as text (`.czs` format) |
| **Production** | step 3 | counter-filters, batch through the CLI protocol |

House rules inherited from the family: nothing is lost silently (an
impossible move is an explained error, never a quiet clamp), **page ids are
stable** (the `panels/` and `pages/` paths never move when reordering — the
folio is what readers see), atomic `project.json` writes.

## Tutorial — a full book, step by step

This walkthrough builds *The Making of comics2crispz*, a short comic telling
how this app came to be, and generates it end-to-end through a family
engine. Every screenshot below comes from this exact flow.

### 1. Install and configure

```bat
install.bat
```

Edit `config.json`: each engine has the `url` of its running app (preferred
route — warm model, shared GPU queue) and the `czp` path of its CLI (cold
fallback). Start ONE family app (crispz-studio, krea2… whichever model you
want to draw with) and check the wiring:

```bat
D:\Github\crispz-krea2\czp.bat caps
```

`"instance": {"running": true, …}` = ready.

### 2. Write the book as a script

A book is chapters → pages (with a **role**: `cover`, `title`, `story`,
`back`) → panels. Each panel has a visual description (the prompt, with
`@Name` casting references) and dialogue lines. The tutorial book ships as a
builder script — read it, it is the whole authoring API in one file:

```bat
.venv\Scripts\python.exe tools\make_making_of.py
```

(always use the repo's `.venv` — a bare `python` may lack Pillow.)

This creates `books/making-of/` with 6 pages / 17 panels, a casting
(`@Mika` the artist, `@Robi` the protocol robot, `@Atelier` the workshop
setting) and composes every page with placeholders.

### 3. Open the flatplan

```bat
run.bat books\making-of
```

![Flatplan with placeholder pages](docs/img/01-flatplan-before.png)

The book in publication order, as facing pages: cover alone, then
verso/recto spreads — you SEE where right-hand pages land. Colored dots =
progress. Drag a page onto another to reorder (folios recompute), drop on a
chapter header to move it there.

### 4. Open a page

Click any page:

![Page preview with panel rects](docs/img/02-page-before.png)

The layout rects are overlaid on the composed page; hover a panel to see its
id and prompt. Balloons (from the last compose) show as blue outlines.

### 5. Generate through the engine

Press **🎨 Generate missing**. Each panel without an image is resolved
(casting substituted, exact panel ratio) and sent to the engine — the
running instance renders it in its own queue, the image lands in
`panels/…`, `project.json` is saved after every panel, and the page
recomposes with lettering:

![Page after generation](docs/img/03-page-after.png)

The status bar reports the engine, the seeds (replayable) and any warnings
(unknown casting names, empty panels, dropped refs — never silent).

### 6. The whole book

Back to the flatplan (Esc), **🧩 Compose book**:

![Flatplan after generation](docs/img/04-flatplan-after.png)

### 7. Character consistency (refs v2)

Give a casting entry reference images (`refs`, project-relative paths —
crispz-studio's 🪪 *Generate reference sheet* creates them) and, when the
engine supports multi-reference generation (`supports.refs` in `czp caps`),
panels mentioning `@Name` are generated WITH those references. Engines that
cannot (e.g. Krea 2) say so upfront and fall back to plain txt2img with a
warning.

**LoRAs** work on every engine (`supports.loras`), three ways, all ending up
hot-swapped per panel: per character (the `loras` list of a casting entry —
wins over the style LoRA on the same file), per book (`style.loras` in
`project.json`), or **inline in a panel text** with the A1111/Civitai syntax:
`@Lea runs in the rain <lora:ink-style.safetensors:0.8>` — the tag is
extracted by the protocol and never reaches the text encoder.

### 8. Export

The project stays fully compatible with crispz-studio, so its CLI finishes
the job:

```bat
D:\Github\crispz-studio\.venv\Scripts\python.exe D:\Github\crispz-studio\app.py --comic D:\Github\comics2crispz\books\making-of --comic-export pdf
```

(absolute paths on purpose: `--comic` resolves relative to the shell's current
directory, and crispz-studio's venv is the one with the full pipeline.)

(or open the same folder in crispz-studio's 🎬 Comic Studio to drag
balloons, then export PDF/CBZ.)

**The finished result of this exact tutorial**, generated end-to-end by the
crispz family through the CLI protocol:
📖 **[The Making of comics2crispz — final PDF](docs/the-making-of.pdf)**
(6 pages, 17 panels, one engine, zero manual retouching).

## Architecture

```
c2c_server.py     stdlib server: SPA + JSON API (/api/index, /api/chapter,
                  /api/move_page, /api/generate, /api/compose…), cached page
                  thumbnails, path-traversal-safe file serving
c2c_state.py      pure layer: thin book index + per-chapter state (Asset
                  Browser manifest pattern), move_page, facing-page spreads,
                  composition + placements sidecar
c2c_engines.py    CLI-protocol client: caps/gen per engine, instance route
                  first (warm model), czp cold fallback
cz_comic.py       VENDORED verbatim from crispz-studio - geometry, @Name
                  casting, lettering; resync = copy the file from upstream
assets/studio.html  the SPA (vanilla HTML/JS, zero build)
docs/             CLI_PROTOCOL.md (family contract)
```

## Tests

```bat
.venv\Scripts\python.exe tools\run_tests.py
```

No GPU, no server needed: the `c2c_state` layer is pure, the API is tested
against an ephemeral server, the engines against a protocol mock.
