"""comics2crispz - client of the crispz family CLI protocol (v1).

comics2crispz generates NOTHING itself: it talks to the family tools
(crispz-studio, crispz-qwen-edit, crispz-krea*) through the JSON contract
documented in docs/CLI_PROTOCOL.md. Two routes per engine, tried in order:

  1. the RUNNING instance (Gradio endpoints cli_caps/cli_gen at `url`) -
     warm model, shared queue, never two processes on the GPU;
  2. the tool's CLI (`czp.bat gen --spec ...`) - cold path, which redoes
     that instance detection itself (double safety net, single contract).

Config (config.json, see config-sample.json):
  "engines": {"studio": {"url": "http://127.0.0.1:7860",
                         "czp": "D:/Github/crispz-studio/czp.bat"}, ...}
  "engine": "studio"        <- default engine

Everything is stdlib: urllib + subprocess. An engine failure comes back as
{ok: false, error} - no exception ever crosses the server.
"""

import os
import io
import json
import re
import time
import base64
import subprocess
import urllib.request

PROTOCOL = 1


def _gradio_call(url, name, data, post_timeout=10, get_timeout=3600):
    req = urllib.request.Request(
        f"{url.rstrip('/')}/gradio_api/call/{name}",
        data=json.dumps({"data": data}).encode("utf-8"),
        headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=post_timeout) as r:
        j = json.loads(r.read().decode("utf-8"))
    eid = j.get("event_id") or j.get("hash")
    if not eid:
        raise ValueError("no event id (endpoint missing?)")
    with urllib.request.urlopen(
            f"{url.rstrip('/')}/gradio_api/call/{name}/{eid}",
            timeout=get_timeout) as r:
        text = r.read().decode("utf-8")
    m = re.search(r"data:\s*(\[[\s\S]*?\])\s*(?:\n|$)", text)
    if not m:
        raise ValueError("empty API response")
    out = json.loads(m.group(1))[0]
    return json.loads(out) if isinstance(out, str) else out


def probe(url, timeout=4):
    """caps of the instance at `url`, None when nothing answers."""
    try:
        caps = _gradio_call(url, "cli_caps", [], post_timeout=timeout,
                            get_timeout=max(timeout, 8))
        return caps if isinstance(caps, dict) and caps.get("ok") else None
    except Exception:
        return None


def _run_czp(czp_path, args, spec=None, timeout=3600):
    """Invoke a tool's czp; the spec goes through stdin ('-'), the reply is
    the LAST JSON line of stdout (contract: one JSON line)."""
    cmd = [czp_path] + args
    if czp_path.lower().endswith((".bat", ".cmd")):
        cmd = ["cmd", "/c", czp_path] + args
    p = subprocess.run(cmd, input=(json.dumps(spec) if spec else None),
                       capture_output=True, text=True, encoding="utf-8",
                       errors="replace", timeout=timeout)
    for line in reversed((p.stdout or "").strip().splitlines()):
        line = line.strip()
        if line.startswith("{"):
            try:
                return json.loads(line)
            except ValueError:
                continue
    return {"ok": False, "error": f"czp exited {p.returncode} without JSON: "
                                  f"{(p.stderr or p.stdout or '')[-400:]}"}


class Engine:
    """One family engine: an instance URL and/or a czp path."""

    def __init__(self, name, url=None, czp=None, tool=None):
        self.name = name
        self.url = (url or "").strip() or None
        self.czp = (czp or "").strip() or None
        self.tool = (tool or "").strip() or None   # identite attendue (config)
        self._probe = (0.0, None)          # (timestamp, caps|None) cache

    def _tool_matches(self, caps):
        """L'app qui repond sur le port est-elle BIEN cet outil ? Plusieurs
        entrees de config peuvent pointer le meme port (7860): sans ce
        controle, choisir 'krea2' genererait chez crispz-studio sans un mot.
        Heuristique nom config -> nom d'outil ('studio' ~ 'crispz-studio'),
        surchargable par config engines.<n>.tool."""
        t = str((caps or {}).get("tool") or "").lower()
        if not t:
            return True                     # vieux outil sans identite: on croit
        if self.tool:
            return t == self.tool.lower()
        n = self.name.lower()
        return t == n or t == "crispz-" + n or t.endswith("-" + n)

    def alive(self, ttl=20):
        """Cached instance probe: caps dict when the instance answers, else
        None. The cache keeps per-panel face-detection calls from paying two
        extra HTTP round-trips each."""
        ts, caps = self._probe
        if time.time() - ts > ttl:
            caps = probe(self.url) if self.url else None
            self._probe = (time.time(), caps)
        return caps

    def faces(self, pil_image, timeout=180):
        """Face detection through the running instance (cli_faces endpoint):
        [{'box': (x1,y1,x2,y2), 'mouth': (x,y)|None, 'embedding': [...]|None}]
        or None when the instance is down or has no detector - the caller
        letters with the fallback placement then."""
        caps = self.alive()
        if not caps or not (caps.get("supports") or {}).get("faces"):
            return None
        buf = io.BytesIO()
        pil_image.convert("RGB").save(buf, "JPEG", quality=92)
        b64 = base64.b64encode(buf.getvalue()).decode("ascii")
        try:
            res = _gradio_call(self.url, "cli_faces", [b64],
                               get_timeout=timeout)
            if not (isinstance(res, dict) and res.get("ok")):
                return None
            return [{"box": tuple(f["box"]),
                     "mouth": (tuple(f["mouth"]) if f.get("mouth") else None),
                     "embedding": f.get("embedding")}
                    for f in res.get("faces") or []]
        except Exception:
            return None

    def caps(self):
        if self.url:
            inst = probe(self.url)
            if inst:
                inst["route"] = "remote"
                if not self._tool_matches(inst):
                    inst["tool_mismatch"] = True
                return inst
        if self.czp and os.path.isfile(self.czp):
            res = _run_czp(self.czp, ["caps"], timeout=120)
            res.setdefault("route", "cli")
            return res
        return {"ok": False, "name": self.name,
                "error": "no running instance and no czp path configured"}

    def upscale(self, spec):
        """Upscale UNE image via l'op 'upscale' du protocole (ESRGAN +
        refine de l'outil) - la sortie print. Memes routes que gen()."""
        spec = dict(spec)
        spec.setdefault("protocol", PROTOCOL)
        spec.setdefault("op", "upscale")
        caps = self.alive() if self.url else None
        if caps and not self._tool_matches(caps):
            return {"ok": False,
                    "error": f"the app answering at {self.url} is "
                             f"'{caps.get('tool')}', not {self.name} - "
                             f"close it and start {self.name}'s app"}
        if caps:
            sup = caps.get("supports") or {}
            if float(spec.get("factor") or 2.0) <= 1.0 and sup.get("img2img") is False:
                return {"ok": False,
                        "error": f"{self.name} cannot do a variation: this "
                                 f"model family has no img2img pipeline "
                                 f"(Regenerate, or switch the book to studio "
                                 f"/ krea / qwen-edit for variations)"}
            try:
                return _gradio_call(self.url, "cli_upscale",
                                    [json.dumps(spec)])
            except Exception as e:
                return {"ok": False, "error": f"remote call failed: {e}"}
        if self.czp and os.path.isfile(self.czp):
            return _run_czp(self.czp, ["upscale", "--spec", "-"], spec=spec)
        return {"ok": False,
                "error": f"engine '{self.name}': no route (no instance at "
                         f"{self.url or '?'} and no czp)"}

    def edit(self, spec):
        """Image + instruction -> image via l'op 'edit' du protocole
        (Qwen-Image-Edit, Z-Image Omni). Memes routes/garde-fous que gen():
        un outil sans modele d'edition repond par un refus explicite."""
        spec = dict(spec)
        spec.setdefault("protocol", PROTOCOL)
        spec.setdefault("op", "edit")
        caps = self.alive() if self.url else None
        if caps and not self._tool_matches(caps):
            return {"ok": False,
                    "error": f"the app answering at {self.url} is "
                             f"'{caps.get('tool')}', not {self.name} - "
                             f"close it and start {self.name}'s app"}
        if caps:
            sup = caps.get("supports") or {}
            if "edit" not in sup:
                return {"ok": False,
                        "error": f"the running {self.name} app is older than "
                                 f"the edit feature - restart it with the "
                                 f"current code"}
            if not sup.get("edit"):
                return {"ok": False,
                        "error": f"{self.name} has no edit model "
                                 f"(supports.edit is false) - use qwen-edit, "
                                 f"or configure an omni model on this engine"}
            try:
                return _gradio_call(self.url, "cli_edit", [json.dumps(spec)])
            except Exception as e:
                return {"ok": False, "error": f"remote call failed: {e}"}
        if self.czp and os.path.isfile(self.czp):
            return _run_czp(self.czp, ["edit", "--spec", "-"], spec=spec)
        return {"ok": False,
                "error": f"engine '{self.name}': no route (no instance at "
                         f"{self.url or '?'} and no czp)"}

    def inpaint(self, spec):
        """Image + masque (blanc = a redessiner) + prompt LOCAL -> image via
        l'op 'inpaint' du protocole (pipeline inpaint de l'outil: tous les
        moteurs de la famille en ont un). Memes routes/garde-fous que gen()."""
        spec = dict(spec)
        spec.setdefault("protocol", PROTOCOL)
        spec.setdefault("op", "inpaint")
        caps = self.alive() if self.url else None
        if caps and not self._tool_matches(caps):
            return {"ok": False,
                    "error": f"the app answering at {self.url} is "
                             f"'{caps.get('tool')}', not {self.name} - "
                             f"close it and start {self.name}'s app"}
        if caps:
            sup = caps.get("supports") or {}
            if "inpaint" not in sup:
                return {"ok": False,
                        "error": f"the running {self.name} app is older than "
                                 f"the inpaint feature - restart it with the "
                                 f"current code"}
            if not sup.get("inpaint"):
                return {"ok": False,
                        "error": f"{self.name} cannot inpaint: this model "
                                 f"family has no inpaint pipeline (switch the "
                                 f"book to studio / krea / qwen-edit to inpaint, "
                                 f"or Regenerate the panel)"}
            try:
                return _gradio_call(self.url, "cli_inpaint",
                                    [json.dumps(spec)])
            except Exception as e:
                return {"ok": False, "error": f"remote call failed: {e}"}
        if self.czp and os.path.isfile(self.czp):
            return _run_czp(self.czp, ["inpaint", "--spec", "-"], spec=spec)
        return {"ok": False,
                "error": f"engine '{self.name}': no route (no instance at "
                         f"{self.url or '?'} and no czp)"}

    def gen(self, spec):
        """Generate ONE image. spec = a protocol dict (protocol/prompt/...).
        Prefers the instance (warm model); falls back to czp (which retries
        the instance itself before loading a pipeline)."""
        spec = dict(spec)
        spec.setdefault("protocol", PROTOCOL)
        spec.setdefault("op", "gen")
        caps = self.alive() if self.url else None
        if caps and not self._tool_matches(caps):
            # NE PAS generer chez le mauvais outil, et ne pas non plus
            # charger un pipeline froid pendant qu'une autre app tient le
            # GPU: refus explicite, l'utilisateur choisit.
            return {"ok": False,
                    "error": f"the app answering at {self.url} is "
                             f"'{caps.get('tool')}', not {self.name}. One "
                             f"engine at a time on this GPU: close the "
                             f"running app, start {self.name}'s own "
                             f"run.bat, then retry - or switch this book "
                             f"to the running engine (⚙)"}
        if caps:
            try:
                return _gradio_call(self.url, "cli_gen", [json.dumps(spec)])
            except Exception as e:
                return {"ok": False, "error": f"remote call failed: {e}"}
        if self.czp and os.path.isfile(self.czp):
            return _run_czp(self.czp, ["gen", "--spec", "-"], spec=spec)
        return {"ok": False,
                "error": f"engine '{self.name}': no route (no instance at "
                         f"{self.url or '?'} and no czp)"}


def load_engines(config):
    """{name: Engine} from the config. Never raises: a misconfigured engine
    still exists and will fail cleanly when called."""
    out = {}
    for name, e in (config.get("engines") or {}).items():
        if isinstance(e, dict):
            out[name] = Engine(name, url=e.get("url"), czp=e.get("czp"),
                               tool=e.get("tool"))
    return out
