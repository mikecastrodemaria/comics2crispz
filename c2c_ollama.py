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


def _resolve_model(cfg):
    """(url, model|None, err|None) - modele de la config ou premier installe."""
    cfg = cfg or {}
    url = (cfg.get("url") or DEFAULT_URL).rstrip("/")
    model = (cfg.get("model") or "").strip()
    if model:
        return url, model, None
    m = models(cfg, timeout=5)
    if not m.get("ok"):
        return url, None, m
    if not m["models"]:
        return url, None, {"ok": False,
                           "error": "Ollama runs but has no model installed "
                                    "- e.g. run: ollama pull llama3.2"}
    return url, m["models"][0], None


def fun_book(title, pages, layout_counts, cfg, concept="", language="",
             timeout=600):
    """✨ Mode fun: un TITRE (+ concept/pitch, + langue des dialogues) ->
    l'outline complet d'un petit livre (synopsis, casting, planches avec
    gabarits, prompts de cases @Name, dialogues). Ollama est force en JSON
    (format: 'json'). Renvoie {ok, outline, model} ou {ok: False, error}.
    La VALIDATION/reparation de l'outline est faite par
    c2c_state.build_from_outline (pur, testable)."""
    title = (title or "").strip()
    if not title:
        return {"ok": False, "error": "give the book a title first"}
    url, model, err = _resolve_model(cfg)
    if err:
        return err
    lays = ", ".join(f"{n}={c}" for n, c in sorted(layout_counts.items()))
    concept = (concept or "").strip()
    language = (language or "").strip()
    lang_rule = (f"dialogue text in {language}" if language
                 else "dialogue text in the LANGUAGE OF THE TITLE")
    prompt = (
        f"You invent a short, fun comic book from its title: \"{title}\".\n"
        + (f"Concept / pitch given by the author (follow it closely - "
           f"story, mood, setting, characters):\n{concept}\n" if concept
           else "")
        + f"Answer with ONE JSON object EXACTLY shaped like this:\n"
        f'{{"synopsis": "...",\n'
        f' "mood": "visual mood of the whole book in English: palette, '
        f'light, era, weather (one line)",\n'
        f' "casting": [{{"name": "Lea", "kind": "character" or "setting", '
        f'"desc": "concrete visual description, in English"}}],\n'
        f' "pages": [{{"layout": "4-grid", "panels": [{{"text": "...", '
        f'"dialogue": [{{"speaker": "Lea", "kind": "speech", '
        f'"text": "..."}}]}}]}}]}}\n'
        f"Rules:\n"
        f"- exactly {int(pages)} pages; allowed layouts with their panel "
        f"counts: {lays}; each page's panels array has EXACTLY as many items "
        f"as its layout's count\n"
        f"- panel text = a concrete VISUAL image-generation prompt, ALWAYS "
        f"in English whatever the language of the title, concept or "
        f"dialogue (image models read English): subject, action, framing, "
        f"lighting, naming characters as @Name matching the casting names "
        f"(2 to 5 casting entries; casting descriptions in English too)\n"
        f"- dialogue kind is one of speech, thought, caption, sfx; speaker "
        f"empty for caption/sfx; {lang_rule}; "
        f"1-3 short lines per panel, not every panel needs dialogue\n"
        f"- no text or lettering described inside the image prompts")
    try:
        req = urllib.request.Request(
            url + "/api/generate",
            data=json.dumps({"model": model, "stream": False,
                             "format": "json",
                             "keep_alive": (cfg or {}).get("keep_alive", 0),
                             "prompt": prompt}).encode("utf-8"),
            headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            res = json.loads(r.read().decode("utf-8"))
        outline = json.loads(res.get("response") or "{}")
    except Exception as e:
        return {"ok": False,
                "error": f"fun-mode call failed ({model} at {url}): {e}"}
    if not isinstance(outline, dict) or not outline.get("pages"):
        return {"ok": False,
                "error": f"{model} answered without any pages - try again "
                         f"or pick another model in ⚙"}
    return {"ok": True, "outline": outline, "model": model}


HELP_INSTRUCTION = (
    "You are the built-in helper of comics2crispz, a local comic-book "
    "creation app (books are chapters > pages > panels; panels have a "
    "visual prompt in English with @Name casting references and dialogue "
    "lines; images are generated by a local engine; a Story Bible keeps the "
    "narrative coherent, the style/casting keep the visuals coherent). "
    "Answer the user's question BRIEFLY (3 to 6 sentences), concretely, "
    "with one small example when useful. Answer in the LANGUAGE OF THE "
    "QUESTION. No markdown headings, no lists longer than 3 items.")


def ask(question, context, cfg, timeout=180):
    """Aide contextuelle '?' : une question libre + le contexte du champ ou
    l'utilisateur se trouve. Meme hygiene que improve: keep_alive 0 (GPU
    partage), thinking purge, echec en langage clair."""
    question = (question or "").strip()
    if not question:
        return {"ok": False, "error": "type a question first"}
    url, model, err = _resolve_model(cfg)
    if err:
        return err
    prompt = (HELP_INSTRUCTION + "\n\nWhere the user is: "
              + (context or "somewhere in the app").strip()
              + "\n\nQuestion: " + question)
    try:
        req = urllib.request.Request(
            url + "/api/generate",
            data=json.dumps({"model": model, "stream": False,
                             "keep_alive": (cfg or {}).get("keep_alive", 0),
                             "prompt": prompt}).encode("utf-8"),
            headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            res = json.loads(r.read().decode("utf-8"))
    except Exception as e:
        return {"ok": False,
                "error": f"Ollama call failed ({model} at {url}): {e}"}
    raw = res.get("response") or ""
    if "</think>" in raw:
        raw = raw.rsplit("</think>", 1)[1]
    raw = re.sub(r"<think>[\s\S]*?(?:</think>|$)", "", raw,
                 flags=re.IGNORECASE)
    answer = raw.strip()
    if not answer:
        return {"ok": False, "error": f"empty answer from {model}"}
    return {"ok": True, "answer": answer, "model": model}


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
