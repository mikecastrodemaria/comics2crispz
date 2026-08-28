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

    def __init__(self, name, url=None, czp=None):
        self.name = name
        self.url = (url or "").strip() or None
        self.czp = (czp or "").strip() or None
        self._probe = (0.0, None)          # (timestamp, caps|None) cache

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
                return inst
        if self.czp and os.path.isfile(self.czp):
            res = _run_czp(self.czp, ["caps"], timeout=120)
            res.setdefault("route", "cli")
            return res
        return {"ok": False, "name": self.name,
                "error": "no running instance and no czp path configured"}

    def gen(self, spec):
        """Generate ONE image. spec = a protocol dict (protocol/prompt/...).
        Prefers the instance (warm model); falls back to czp (which retries
        the instance itself before loading a pipeline)."""
        spec = dict(spec)
        spec.setdefault("protocol", PROTOCOL)
        spec.setdefault("op", "gen")
        if self.url and self.alive():
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
            out[name] = Engine(name, url=e.get("url"), czp=e.get("czp"))
    return out
