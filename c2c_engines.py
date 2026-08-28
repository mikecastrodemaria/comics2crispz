"""comics2crispz - client du protocole CLI famille crispz (v1).

comics2crispz ne genere RIEN lui-meme : il parle aux outils de la famille
(crispz-studio, crispz-qwen-edit, crispz-krea*) via le contrat JSON documente
dans docs/CLI_PROTOCOL.md. Deux routes par moteur, essayees dans l'ordre :

  1. l'INSTANCE qui tourne (endpoints Gradio cli_caps/cli_gen a `url`) -
     modele chaud, queue partagee, jamais deux processus sur le GPU ;
  2. le CLI de l'outil (`czp.bat gen --spec ...`) - chemin froid, qui refait
     lui-meme cette detection d'instance (double filet, mais un seul contrat).

Config (config.json, voir config-sample.json) :
  "engines": {"studio": {"url": "http://127.0.0.1:7860",
                         "czp": "D:/Github/crispz-studio/czp.bat"}, ...}
  "engine": "studio"        <- moteur par defaut

Tout est stdlib : urllib + subprocess. Une erreur d'un moteur remonte en
{ok: false, error} - jamais d'exception qui traverse le serveur.
"""

import os
import json
import re
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
    """caps de l'instance a `url`, None si rien ne repond."""
    try:
        caps = _gradio_call(url, "cli_caps", [], post_timeout=timeout,
                            get_timeout=max(timeout, 8))
        return caps if isinstance(caps, dict) and caps.get("ok") else None
    except Exception:
        return None


def _run_czp(czp_path, args, spec=None, timeout=3600):
    """Invoque le czp d'un outil ; le spec passe par stdin ('-'), la reponse
    est la DERNIERE ligne JSON de stdout (contrat : une ligne JSON)."""
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
    """Un moteur de la famille : une URL d'instance et/ou un czp."""

    def __init__(self, name, url=None, czp=None):
        self.name = name
        self.url = (url or "").strip() or None
        self.czp = (czp or "").strip() or None

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
        """Genere UNE image. spec = dict du protocole (protocol/prompt/...).
        Prefere l'instance (modele chaud) ; sinon czp (qui re-essaie lui-meme
        l'instance avant de charger un pipeline)."""
        spec = dict(spec)
        spec.setdefault("protocol", PROTOCOL)
        spec.setdefault("op", "gen")
        if self.url and probe(self.url):
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
    """{nom: Engine} depuis la config. Jamais d'erreur : un moteur mal
    configure existe quand meme et echouera proprement a l'appel."""
    out = {}
    for name, e in (config.get("engines") or {}).items():
        if isinstance(e, dict):
            out[name] = Engine(name, url=e.get("url"), czp=e.get("czp"))
    return out
