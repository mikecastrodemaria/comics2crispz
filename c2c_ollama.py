"""comics2crispz - optional Ollama helper: improve a panel description.

Same idea as crispz-studio's 'Improve prompt', standalone (stdlib urllib, no
cz_ollama import): comics2crispz talks to the LOCAL Ollama service directly.
Config (config.json): {"ollama": {"url": "http://127.0.0.1:11434",
"model": ""}} - empty model = first installed model.

Never raises: every function returns {ok: true, ...} or {ok: false, error}
with a plain-language message (the SPA shows it as-is to non-dev users).
"""

import json
import re
import urllib.request

DEFAULT_URL = "http://127.0.0.1:11434"

# Regle stricte @Name / <lora:>: l'amelioration ne doit JAMAIS perdre le
# casting ni les tags - et si le modele le fait quand meme, improve() le
# detecte et le signale (jamais silencieux).
INSTRUCTION = (
    "You improve comic panel descriptions used as image generation prompts. "
    "Rewrite the description below into ONE rich, concrete VISUAL prompt in "
    "English: subject, action, framing (close-up, wide shot...), lighting, "
    "mood. STRICT RULES: keep every token starting with '@' EXACTLY as "
    "written (they are cast references, e.g. @Sam); keep every <lora:...> "
    "tag exactly as written; describe no text or lettering in the image; "
    "answer with the prompt ONLY - one line, no quotes, no explanations.")

_AT = re.compile(r"@[A-Za-z0-9_\-]+")


def models(cfg, timeout=5):
    """Liste des modeles installes. Sert aussi de 'Test connection'."""
    url = ((cfg or {}).get("url") or DEFAULT_URL).rstrip("/")
    try:
        with urllib.request.urlopen(url + "/api/tags", timeout=timeout) as r:
            data = json.loads(r.read().decode("utf-8"))
        names = [m.get("name") for m in data.get("models") or [] if m.get("name")]
        return {"ok": True, "url": url, "models": names}
    except Exception as e:
        return {"ok": False,
                "error": f"Ollama does not answer at {url} ({e}). Is the "
                         f"Ollama app running?"}


def improve(text, cfg, timeout=180):
    """Ameliore une description de case. Renvoie {ok, improved, model,
    warnings?} - warning si le modele a perdu un @Name ou un tag <lora:>."""
    text = (text or "").strip()
    if not text:
        return {"ok": False,
                "error": "nothing to improve - write a first draft in the box"}
    cfg = cfg or {}
    url = (cfg.get("url") or DEFAULT_URL).rstrip("/")
    model = (cfg.get("model") or "").strip()
    if not model:
        m = models(cfg, timeout=5)
        if not m.get("ok"):
            return m
        if not m["models"]:
            return {"ok": False,
                    "error": "Ollama runs but has no model installed - e.g. "
                             "run: ollama pull llama3.2"}
        model = m["models"][0]
    try:
        req = urllib.request.Request(
            url + "/api/generate",
            # keep_alive 0 par defaut: le modele est DECHARGE de la VRAM des
            # la reponse. Le GPU est partage avec le moteur de dessin - un
            # LLM de 6 GB qui traine 5 min fait s'effondrer la generation
            # d'images (CUDA bascule en RAM systeme, ~10x plus lent). Config
            # ollama.keep_alive (ex. "5m") pour qui enchaine les Improve.
            data=json.dumps({"model": model, "stream": False,
                             "keep_alive": cfg.get("keep_alive", 0),
                             "prompt": INSTRUCTION + "\n\nDescription:\n"
                                       + text}).encode("utf-8"),
            headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            res = json.loads(r.read().decode("utf-8"))
    except Exception as e:
        return {"ok": False,
                "error": f"Ollama call failed ({model} at {url}): {e}"}
    raw = res.get("response") or ""
    # Modeles "thinking" (Qwen3+, DeepSeek-R1...): le raisonnement
    # <think>...</think> ne doit JAMAIS finir dans le prompt. On garde ce qui
    # suit le dernier </think>, puis on purge tout bloc residuel.
    if "</think>" in raw:
        raw = raw.rsplit("</think>", 1)[1]
    raw = re.sub(r"<think>[\s\S]*?(?:</think>|$)", "", raw,
                 flags=re.IGNORECASE)
    out = " ".join(raw.strip().strip('"').split())
    if not out:
        return {"ok": False,
                "error": f"empty answer from {model} (it may have spent the "
                         f"whole reply thinking - try another model in ⚙)"}
    reply = {"ok": True, "improved": out, "model": model}
    lost = sorted(set(_AT.findall(text)) - set(_AT.findall(out)))
    if "<lora:" in text.lower() and "<lora:" not in out.lower():
        lost.append("<lora:...> tag")
    if lost:
        reply["warnings"] = [f"the model dropped {', '.join(lost)} - put "
                             f"them back, or Improve again"]
    return reply
