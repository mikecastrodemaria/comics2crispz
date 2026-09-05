# crispz family CLI protocol (v1)

> 2026-08-28 — validated and implemented in crispz-studio (`cz_protocol.py` +
> `czp.bat`, hidden `cli_caps`/`cli_gen` endpoints), ported to every fork.
> Reference client: `c2c_engines.py` (this repo). One contract so that any
> interface (comics2crispz, the Gradio accordions, an agent, a night batch
> script) can talk to any family tool — crispz (upscale), crispz-studio
> (Z-Image), crispz-qwen-edit (Qwen-Image-Edit), crispz-krea/krea2 — without
> knowing which one generates.

## 1. Principles

1. **The CLI is the contract, not the process.** The unit of exchange is a
   JSON spec in and a JSON line out. A CLI call NEVER reloads a model when an
   instance is already running: it **routes** to it (direct HTTP on the
   Gradio endpoints, same mechanics as the family SPAs) and the app's queue
   serializes the GPU. Direct execution (cold path) only when no instance
   answers — night batches, CI.
2. **One GPU, one queue.** Two interfaces never talk to the GPU in parallel:
   everything goes through the running instance, or through ONE batch
   process.
3. **Announced degradation, never silent** (house rule). A tool that cannot
   do something (no LoRA, no refs) says so in `caps` and the caller decides;
   it never truncates silently.
4. **The protocol lives in crispz-studio** (the upstream) and flows to the
   forks by patch/merge. The crispz upscaler adopts it at its own pace on its
   own scope (upscale only).

## 2. Commands

`czp.bat` (each repo ships one; always JSON on stdout, one line).

| Command | Role | Tools exposing it |
|---|---|---|
| `caps` | announce the tool's capabilities | all |
| `gen`  | txt2img / omni multi-reference from a spec | studio, qwen-edit, krea, krea2 |
| `edit` | image + instruction → image (`input`, `prompt`) | qwen-edit; studio when an omni model is configured; krea/krea2 answer exit 3 (`supports.edit` false) |
| `upscale` | upscale/refine an image (`input`, `factor`, `denoise`); **factor 1 = pure img2img** (variation, no ESRGAN stage) | all |
| `inpaint` | redraw the WHITE area of a mask (`input`, `mask`, `prompt`, `denoise`); the rest stays pixel-exact | all |

## 3. The spec (input)

Aligned with the output of `cz_comic.resolve_panel` — already the internal
contract.

```json
{
  "protocol": 1,
  "op": "gen",
  "prompt": "…",
  "negative": "…",
  "width": 1024, "height": 1344,
  "seed": -1,
  "steps": null,
  "refs": ["C:/abs/path/lea.png"],
  "loras": ["style-ink.safetensors:0.8"],
  "model": null,
  "out_dir": "out/",
  "count": 1
}
```

Rules:
- `protocol` is mandatory. Unknown version → clean error, exit 3.
- `null`/absent fields = the tool's current settings (config/UI). The spec
  only states what it imposes.
- An unknown field is **ignored with a warning** (`warnings[]`), never an
  error: a spec written for qwen-edit must go through studio.
- `count` other than 1 → warning + forced to 1 (the caller loops).
- `detail_faces` / `detail_hands` (bool, `gen` only): after the render, the
  tool's detailer re-draws faces/hands (comics2crispz sets `detail_faces`
  when the panel stages a casting character). Missing detector → warning,
  never an error.

### upscale / edit specific fields

| Field | Op | Meaning |
|---|---|---|
| `input` | upscale, edit | local absolute path of the source image (missing → exit 2) |
| `factor` | upscale | scale (`2.0` default). `1.0` = **no ESRGAN at all**, the refine pass alone (img2img variation) |
| `denoise` | upscale | refine strength 0–1 (tool default when absent). With `factor` 1 it is the variation strength |
| `model` | upscale | ESRGAN model file name (from `caps.models`); absent → the config `default_esrgan_model`, else the first model matching the factor's scale (never a 16x by accident) |
| `prompt` | upscale | LOCAL description guiding the refine — never the scene prompt on a crop |
| `prompt` | edit | the instruction ("add heavy rain, keep the ink style") |
| `mask` | inpaint | local absolute path of a PNG mask, same size as `input` (resized with a warning otherwise); WHITE = redraw. An all-black mask is an error (exit 2) |
| `denoise` | inpaint | strength 0–1 inside the mask (`default_inpaint_strength`, 0.9) |
| `prompt` | inpaint | what should appear IN the painted area — a LOCAL description, never the scene prompt; may be empty (coherent fill) |

`edit` keeps the input size (aligned to 32); `width`/`height` are ignored.

### loras — style / character consistency

- `loras` = `["file.safetensors:0.8", ...]`, hot-swapped per call by the
  tool (a character LoRA wins over a style LoRA on the same file: first
  weight wins).
- The **prompt itself** may carry `<lora:file[:weight]>` tags (the
  A1111/Civitai habit): they are extracted at validation time, merged into
  `loras` (explicit entries win on duplicates) and stripped before the text
  encoder ever sees them. So a LoRA can travel inside a comics2crispz panel
  text: `@Lea runs in the rain <lora:ink-style.safetensors:0.8>`.
- A prompt that contains ONLY lora tags = error, exit 2 (describe the image
  too).

### refs (v2) — character consistency

- `refs` = **local absolute file paths** (the protocol is machine-local).
  The caller resolves project-relative paths before sending.
- When the tool has an Omni/edit model available (`supports.refs` true),
  generation goes through the multi-reference pipeline (`generate_omni`).
- When not (`supports.refs` false — no model configured, or the model family
  has no such pipeline at all, e.g. Krea): refs are **dropped with a
  warning**, generation continues as plain txt2img.
- A ref missing on disk = **error, exit 2** — never a silently degraded
  image.
- At most `supports.max_refs` (4) refs; extras are cut with a warning.

## 4. The output

```json
{
  "ok": true, "protocol": 1,
  "tool": "crispz-studio", "version": "1.16.0",
  "route": "remote",
  "images": ["…/out/2026-08-28/….png"],
  "seed_used": 42, "refs_used": 2, "loras": ["style-ink.safetensors:0.8"],
  "timings": {"total_s": 14.2, "txt2img": 13.9},
  "warnings": []
}
```

Error: `{"ok": false, "error": "…"}` + exit code.
Exit codes: `0` ok · `1` run error · `2` invalid spec · `3` unsupported
op/protocol · `4` no route.

`route`: `"remote"` (through the instance) or `"local"` (loaded in this
process) — essential diagnostics when timings blow up. `seed_used` is always
the concrete seed (a `-1` is resolved BEFORE generating, same rule as the
UI, so every image is replayable).

## 5. `caps`

```json
{
  "ok": true, "protocol": 1, "tool": "crispz-qwen-edit", "version": "1.16.0",
  "ops": ["caps", "gen", "upscale", "edit", "inpaint"],
  "models": ["4x-UltraSharp.pth", "…"], "loras": ["…"], "model_loaded": true,
  "supports": {"loras": true, "refs": true, "max_refs": 4, "seed": true,
               "negative": true, "arbitrary_size": true, "faces": true,
               "detail_faces": true, "detail_hands": false, "edit": true,
               "inpaint": true, "img2img": true},
  "instance": {"running": true, "url": "http://127.0.0.1:7860",
               "tool": "crispz-qwen-edit", "version": "1.16.0"}
}
```

`supports.inpaint` / `supports.img2img` are per MODEL FAMILY (static, no
pipeline import): Krea 2 has neither (diffusers ships no Krea2Inpaint /
Krea2Img2Img pipeline), so `inpaint` and a variation (`upscale` with factor
1) answer exit 3 there instead of pretending.

`caps` needs neither GPU nor a loaded model: config read + instance probe,
~1.5 s. `supports.refs` is honest per model family: hard `false` on
crispz-krea/krea2 (no instruction-edit pipeline exists — config can never
turn it on), default `true` on crispz-qwen-edit (ships Qwen-Image-Edit),
config-driven on crispz-studio (`zimage_omni_model`).

## 6. Routing

1. `--remote <url>`: force this instance (exit 4 when unreachable).
2. `--local`: force execution in this process (assumed night batch).
3. Default: probe `cli_protocol.instance_url` (default `127.0.0.1:7860`);
   instance found → `cli_gen` endpoint through its queue; otherwise local.
4. The shared GPU is solved by construction: the remote route waits in the
   instance's queue, behind the user's own renders.

## 7. Recorded decisions (v1)

- **One image per call**, the caller loops.
- **No per-tool port**: every tool reads `cli_protocol.instance_url` and the
  reply's `tool`/`version` identify WHO answered — the reply, not the port.
- Progress: v1 = blocking call. A `--progress-file` may come with the
  comics2crispz Production dashboard.
- Auth: out of scope for v1 (everything is localhost).
- On the **remote** route, `spec.model` is **refused with a warning**: never
  swap the model of the user's running instance under their feet.

## 8. Implementation notes

- The protocol lives in a dedicated module **`cz_protocol.py`** + `czp.bat`
  (always-JSON entry, light imports — no torch before the local path)
  instead of flags grafted onto `cz_cli.py`: `python app.py` imports the
  whole stack up front, a sub-2s `caps` was impossible there. `cz_cli` is
  untouched, full compat.
- The remote route speaks to the Gradio endpoints in **plain HTTP (urllib)**
  — no gradio_client dependency.
- Spec files may carry a UTF-8 BOM (PowerShell `-Encoding utf8`): accepted.
