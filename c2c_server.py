"""comics2crispz - local server (stdlib): SPA + JSON API for a comic project.

    python c2c_server.py <project_dir> [--port 8770]

Zero server dependency (http.server); Pillow only for thumbnails and page
composition. A project stays ONE crispz-family-compatible project.json
(vendored cz_comic): the crispz-studio accordion, its Comic Studio and the
CLI can all work on the same folder - everything is stateless, every
operation re-reads/re-writes the file.

Routes:
  GET  /                    -> assets/studio.html
  GET  /assets/<f>          -> SPA files
  GET  /file/<rel>          -> PROJECT files (pages/, panels/, refs/)
  GET  /thumb/<cid>/<pid>   -> cached JPEG thumbnail of the composed page
  POST /api/<op>            -> JSON in / JSON out:
       index                  the thin index (navigator + flatplan)
       chapter {cid}          the full state of one chapter
       move_page {cid,pid,to_cid,to_index}   the flatplan drag
       set_role {cid,pid,role}
       compose {cid,pid} | compose_book {}
       generate {cid,pid,pnid?,engine?,force?}  panels via a family engine
       engines {}             caps of the configured engines (CLI protocol)

Every API reply is {ok: true, ...} or {ok: false, error} - the SPA has a
single error path. A process lock serializes project.json writes (two quick
drags cannot lose each other).
"""

import os
import re
import sys
import json
import time
import shutil
import base64
import argparse
import threading
import mimetypes
import posixpath
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import cz_comic
import c2c_state
import c2c_engines
import c2c_ollama
import c2c_script
import c2c_bundle

HERE = os.path.dirname(os.path.abspath(__file__))
ASSETS = os.path.join(HERE, "assets")
DEFAULT_PORT = 8770
# Racine des livres (le selecteur de l'UI ne montre QUE ce dossier).
# Variable de module pour que les tests la deportent dans un tmp.
BOOKS_ROOT = os.path.join(HERE, "books")
# Config locale editable par la modale ⚙ (op_save_config). Variable de module
# pour la meme raison: les tests ecrivent dans un tmp, jamais la vraie.
CONFIG_PATH = os.path.join(HERE, "config.json")

_LOCK = threading.Lock()
# Empreinte du code CHARGE (mtime de ce fichier a l'import): un serveur qui
# tourne sur du code plus vieux que le disque est PERIME - le relancement
# doit le dire au lieu de le reutiliser (symptome sinon: 'unknown op').
_CODE_MTIME = os.path.getmtime(os.path.abspath(__file__))


def load_config():
    for p in (CONFIG_PATH, os.path.join(HERE, "config-sample.json")):
        if os.path.isfile(p):
            try:
                with open(p, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception as e:
                print(f"[c2c] {os.path.basename(p)} unreadable ({e}), ignored",
                      file=sys.stderr)
    return {}


class Studio:
    """Server state: the project folder + the engines. The project itself is
    NOT kept in memory (stateless, like the rest of the family)."""

    def __init__(self, project_dir, config):
        self.dir = os.path.abspath(project_dir)
        self.config = config
        self.engines = c2c_engines.load_engines(config)
        self._emb_cache = {}          # (name, ref path, mtime) -> embedding
        if not os.path.isfile(cz_comic.project_json_path(self.dir)):
            raise FileNotFoundError(f"no project.json in {self.dir}")
        self.repairs = self._repair_ids()

    def _repair_ids(self):
        """Duplicate page ids (an old import bug) are repaired on open: the
        fixes are logged and kept in self.repairs so the UI can say it."""
        try:
            project = cz_comic.load_project(self.dir)
        except Exception:
            return []
        fixes = c2c_state.repair_page_ids(project)
        if fixes:
            cz_comic.save_project(project, self.dir)
            for f in fixes:
                print(f"[c2c] repaired duplicate page id {f['cid']}.{f['old']} -> "
                      f"{f['new']} ({f['panels_to_redraw']} panel(s) to redraw)",
                      flush=True)
        return fixes

    def load(self):
        return cz_comic.load_project(self.dir)

    def _default_engine(self, name=None):
        name = name or self.config.get("engine") or next(iter(self.engines),
                                                         None)
        return name, self.engines.get(name)

    def _letter_kit(self, project):
        """(face_detector, char_embeddings) for face-aware lettering, served
        by the default engine's running instance (cli_faces endpoint of the
        CLI protocol). (None, None) when no instance/detector is available -
        render_lettering then uses its fallback placement. Reference-portrait
        embeddings are cached by (path, mtime)."""
        _name, eng = self._default_engine()
        if eng is None or not hasattr(eng, "faces") or not eng.alive():
            return None, None
        caps = eng.alive()
        if not (caps.get("supports") or {}).get("faces"):
            return None, None

        from PIL import Image

        def detector(img):
            return eng.faces(img) or []

        emb = {}
        for cname, char in (project.get("casting") or {}).items():
            if char.get("kind", "character") != "character":
                continue
            for r in char.get("refs") or []:
                p = r if os.path.isabs(r) else os.path.join(
                    self.dir, *str(r).replace("\\", "/").split("/"))
                if not os.path.isfile(p):
                    continue
                key = (cname.lower(), os.path.abspath(p),
                       int(os.path.getmtime(p)))
                if key in self._emb_cache:
                    vec = self._emb_cache[key]
                else:
                    try:
                        with Image.open(p) as im:
                            faces = eng.faces(im) or []
                    except Exception:
                        faces = []
                    vec = faces[0].get("embedding") if faces else None
                    self._emb_cache[key] = vec
                if vec:
                    emb[cname.strip().lower()] = vec
                break                     # first existing ref = the portrait
        return detector, (emb or None)

    # ---- API ops (each returns a dict ready to serialize) ----
    def op_index(self, _data):
        return c2c_state.book_index(self.load(), self.dir)

    def op_chapter(self, data):
        return c2c_state.chapter_state(self.load(), self.dir, data["cid"])

    def op_move_page(self, data):
        with _LOCK:
            project = self.load()
            c2c_state.move_page(project, data["cid"], data["pid"],
                                data.get("to_cid", data["cid"]),
                                data["to_index"])
            cz_comic.save_project(project, self.dir)
        return c2c_state.book_index(project, self.dir)

    def op_set_role(self, data):
        with _LOCK:
            project = self.load()
            c2c_state.set_role(project, data["cid"], data["pid"], data["role"])
            cz_comic.save_project(project, self.dir)
        return c2c_state.book_index(project, self.dir)

    def op_compose(self, data):
        with _LOCK:
            project = self.load()
            fd, emb = self._letter_kit(project)
            c2c_state.compose_one(project, self.dir, data["cid"], data["pid"],
                                  face_detector=fd, char_embeddings=emb)
        return c2c_state.book_index(project, self.dir)

    def op_compose_book(self, _data):
        with _LOCK:
            project = self.load()
            fd, emb = self._letter_kit(project)
            for ch, pg in cz_comic.book_order(project):
                c2c_state.compose_one(project, self.dir, ch["id"], pg["id"],
                                      face_detector=fd, char_embeddings=emb)
        return c2c_state.book_index(project, self.dir)

    def op_ask(self, data):
        """Aide contextuelle '?' (Ollama): {question, context}. Le contexte
        decrit le champ/ecran d'ou vient la question."""
        return c2c_ollama.ask(str(data.get("question") or ""),
                              str(data.get("context") or ""),
                              self.config.get("ollama"))

    def op_improve(self, data):
        """✨ Improve du texte d'une case via Ollama (config 'ollama').
        Warning explicite si le modele a perdu un @Name ou un <lora:>."""
        return c2c_ollama.improve(str(data.get("text") or ""),
                                  self.config.get("ollama"))

    def op_config(self, _data):
        """Reglages pour la modale ⚙: moteur par defaut, moteurs connus,
        Ollama (url/modele + etat live: joignable, modeles installes)."""
        oll = self.config.get("ollama") or {}
        return {"ok": True, "engine": self.config.get("engine"),
                "engines": sorted(self.engines),
                "ollama": {"url": oll.get("url") or c2c_ollama.DEFAULT_URL,
                           "model": oll.get("model") or ""},
                "ollama_status": c2c_ollama.models(oll)}

    def op_save_config(self, data):
        """Sauve les reglages de la modale ⚙ dans config.json (cree depuis
        le sample au premier enregistrement) et les applique a chaud."""
        with _LOCK:
            # Base = la config COURANTE (pas une relecture disque: elle
            # ecraserait ce que le serveur tient deja, p.ex. en tests).
            cfg = dict(self.config)
            eng = data.get("engine")
            if eng:
                if eng not in self.engines:
                    return {"ok": False,
                            "error": f"unknown engine '{eng}' (config.json "
                                     f"'engines')"}
                cfg["engine"] = eng
            oll = data.get("ollama")
            if isinstance(oll, dict):
                cur = dict(cfg.get("ollama") or {})
                for k in ("url", "model"):
                    if k in oll:
                        cur[k] = str(oll[k] or "").strip()
                cfg["ollama"] = cur
            tmp = CONFIG_PATH + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(cfg, f, indent=2, ensure_ascii=False)
            os.replace(tmp, CONFIG_PATH)
            self.config = cfg
            self.engines = c2c_engines.load_engines(cfg)
        return {"ok": True, "engine": cfg.get("engine"),
                "ollama": cfg.get("ollama") or {}}

    # Cycle de gabarits varies pour "ajouter N planches" (structure de
    # depart): alternance large/dense, jamais deux fois le meme d'affilee.
    LAYOUT_CYCLE = ("3-hero", "4-grid", "2-rows", "5-hero", "3-rows",
                    "6-grid")

    def op_add_chapter(self, data):
        """Nouveau chapitre: {name}. Renvoie l'index (+ cid cree)."""
        with _LOCK:
            project = self.load()
            ch = cz_comic.add_chapter(
                project, str(data.get("name") or "").strip() or "Chapter")
            cz_comic.save_project(project, self.dir)
        idx = c2c_state.book_index(project, self.dir)
        idx["created"] = ch["id"]
        return idx

    def op_add_page(self, data):
        """Ajoute des planches: {cid, layout, role?, count?}. layout
        'varied' = cycle de gabarits varies (structure de depart). Les
        planches sont composees (placeholders) pour apparaitre tout de
        suite dans le chemin de fer."""
        cid = data["cid"]
        role = data.get("role") or "story"
        count = max(1, min(int(data.get("count") or 1), 50))
        layout = data.get("layout") or "4-grid"
        with _LOCK:
            project = self.load()
            ch = cz_comic.find_chapter(project, cid)
            start = len(ch["pages"])
            made = []
            for i in range(count):
                lay = (self.LAYOUT_CYCLE[(start + i) % len(self.LAYOUT_CYCLE)]
                       if layout == "varied" else layout)
                pg = cz_comic.add_page(project, cid, lay, role=role)
                made.append(pg["id"])
            cz_comic.save_project(project, self.dir)
            for pid in made:
                c2c_state.compose_one(project, self.dir, cid, pid)
        idx = c2c_state.book_index(project, self.dir)
        idx["created"] = made
        return idx

    def op_set_layout(self, data):
        """Change le gabarit d'une planche: {cid, pid, layout}. En
        reduisant, les cases retirees sont RENDUES dans la reponse (id +
        texte) - rien ne disparait en silence, regle maison."""
        with _LOCK:
            project = self.load()
            _pg, removed = cz_comic.set_layout(project, data["cid"],
                                               data["pid"], data["layout"])
            cz_comic.save_project(project, self.dir)
            fd, emb = self._letter_kit(project)
            c2c_state.compose_one(project, self.dir, data["cid"], data["pid"],
                                  face_detector=fd, char_embeddings=emb)
        idx = c2c_state.book_index(project, self.dir)
        idx["removed"] = [{"id": p["id"], "text": p.get("text") or ""}
                          for p in removed]
        if removed:
            idx["warnings"] = [
                f"{len(removed)} panel(s) removed by the smaller layout - "
                f"their text is shown here so nothing is lost: " +
                "; ".join(f"{p['id']}: {(p.get('text') or '(empty)')[:60]}"
                          for p in removed)]
        return idx

    def op_delete_page(self, data):
        """Supprime une planche du livre: {cid, pid}. Le project.json ne la
        reference plus, mais les FICHIERS (cases generees, planche composee)
        restent sur disque dans panels/ et pages/ - recuperables a la main.
        La reponse recapitule les textes des cases supprimees (regle
        maison: rien ne disparait sans trace)."""
        with _LOCK:
            project = self.load()
            ch = cz_comic.find_chapter(project, data["cid"])
            page = cz_comic.find_page(project, data["cid"], data["pid"])
            ch["pages"].remove(page)
            cz_comic.save_project(project, self.dir)
        idx = c2c_state.book_index(project, self.dir)
        texts = [f"{pn['id']}: {(pn.get('text') or '(empty)')[:60]}"
                 for pn in page["panels"]]
        idx["warnings"] = [
            f"page {data['cid']}.{data['pid']} removed from the book - its "
            f"panel texts: " + "; ".join(texts) + ". Generated files stay "
            f"on disk under panels/ and pages/ (recoverable)."]
        return idx

    def op_save_panel(self, data):
        """Sauve le TEXTE d'une case avant regeneration: {cid, pid, pnid,
        text?, dialogue?, seed?}. Le dialogue est reparse (syntaxe
        scenariste) en recollant les positions de bulles posees au drag.
        Renvoie l'index + 'unknown' (les @Name absents du casting)."""
        with _LOCK:
            project = self.load()
            panel = cz_comic.find_panel(project, data["cid"], data["pid"],
                                        data["pnid"])
            if data.get("text") is not None:
                panel["text"] = str(data["text"]).strip()
            if data.get("dialogue") is not None:
                panel["dialogue"] = c2c_state.merge_dialogue(
                    panel.get("dialogue") or [],
                    cz_comic.parse_dialogue(str(data["dialogue"])))
            if data.get("seed") is not None:
                try:
                    panel["seed"] = int(data["seed"])
                except (TypeError, ValueError):
                    pass
            if data.get("locked") is not None:
                has_img = bool(panel.get("image") and os.path.isfile(panel["image"]))
                if data["locked"]:
                    panel["status"] = "locked"
                else:
                    panel["status"] = "rendered" if has_img else "draft"
            cz_comic.save_project(project, self.dir)
        idx = c2c_state.book_index(project, self.dir)
        idx["unknown"] = cz_comic.resolve_casting(
            panel.get("text") or "", project.get("casting"))["unknown"]
        return idx

    def op_set_book_engine(self, data):
        """Moteur/modele voulus par le LIVRE OUVERT (edition apres coup de ce
        que le wizard enregistre a la creation): {engine, model}. engine
        vide = retour au moteur par defaut de la config."""
        with _LOCK:
            project = self.load()
            name = str(data.get("engine") or "").strip()
            if name and name not in self.engines:
                return {"ok": False,
                        "error": f"unknown engine '{name}' (config.json "
                                 f"'engines')"}
            if name:
                project["engine"] = {"name": name,
                                     "model": str(data.get("model") or "")}
            else:
                project.pop("engine", None)
            # LoRAs globales du livre (style.loras): liste COMPLETE envoyee
            # par l'UI - decocher = retirer. None/absent = pas touche.
            if isinstance(data.get("loras"), list):
                project.setdefault("style", {})["loras"] = \
                    [str(x) for x in data["loras"] if str(x).strip()]
            cz_comic.save_project(project, self.dir)
        return c2c_state.book_index(project, self.dir)

    # ------------------------------------------------------------------
    # 📖 Bible visuelle: casting (personnages + decors), style, moods
    # ------------------------------------------------------------------
    def op_bible(self, _data):
        return c2c_state.bible_state(self.load(), self.dir)

    @staticmethod
    def _cast_name(raw):
        return c2c_state.name_token(raw)

    def op_save_cast(self, data):
        """Cree/modifie une fiche: {name, desc, kind, loras[], negative,
        rename_from?}. Un renommage reecrit les @Ancien dans TOUTES les
        cases (compte renvoye) - rien ne se perd. Le nom est un token
        @Name: lettres, chiffres, _ et - (les autres caracteres sont
        retires, l'utilisateur voit le nom retenu)."""
        name = self._cast_name(data.get("name"))
        if not name:
            return {"ok": False, "error": "give the character/setting a name "
                                          "(letters, digits, _ or -): it is "
                                          "what you type as @Name in the panels"}
        kind = data.get("kind") if data.get("kind") in ("character", "setting") \
            else "character"
        loras = [str(x).strip() for x in (data.get("loras") or []) if str(x).strip()]
        with _LOCK:
            project = self.load()
            casting = project.setdefault("casting", {})
            old = self._cast_name(data.get("rename_from"))
            renamed = 0
            if old and old != name:
                key, fiche = cz_comic._casting_lookup(casting, old)
                if key is None:
                    return {"ok": False, "error": f"no entry named @{old}"}
                if cz_comic._casting_lookup(casting, name)[0]:
                    return {"ok": False,
                            "error": f"@{name} already exists - pick another "
                                     f"name or edit that entry"}
                casting.pop(key)
                pat = re.compile(r"(?<!@)@" + re.escape(key) + r"(?![A-Za-z0-9_\-])",
                                 re.IGNORECASE)
                for ch in project.get("chapters") or []:
                    for pg in ch.get("pages") or []:
                        for pn in pg.get("panels") or []:
                            t = pn.get("text") or ""
                            t2, n = pat.subn("@" + name, t)
                            if n:
                                pn["text"] = t2
                                renamed += n
                            for d in pn.get("dialogue") or []:
                                if isinstance(d, dict) and \
                                        str(d.get("speaker") or "").lower() == key.lower():
                                    d["speaker"] = name
                cur = fiche
            else:
                key, cur = cz_comic._casting_lookup(casting, name)
                if key and key != name:          # meme nom, autre casse
                    casting.pop(key)
                cur = dict(cur or cz_comic.new_character("", kind=kind))
            # cles absentes = inchangees (un renommage seul ne vide rien)
            if "desc" in data:
                cur["desc"] = str(data.get("desc") or "").strip()
            if "kind" in data:
                cur["kind"] = kind
            if "loras" in data:
                cur["loras"] = loras
            if "negative" in data:
                cur["negative"] = str(data.get("negative") or "").strip()
            cur.setdefault("kind", kind)
            cur.setdefault("refs", [])
            casting[name] = cur
            cz_comic.save_project(project, self.dir)
        res = c2c_state.bible_state(project, self.dir)
        res.update({"saved": name, "renamed_mentions": renamed})
        return res

    def op_delete_cast(self, data):
        """Supprime une fiche {name, force?}. Refus si des cases la citent,
        sauf force (les @Nom restent dans les textes et sont signales comme
        inconnus - rien n'est reecrit en douce). Les images de reference
        restent sur disque (refs/)."""
        name = self._cast_name(data.get("name"))
        with _LOCK:
            project = self.load()
            casting = project.get("casting") or {}
            key, _f = cz_comic._casting_lookup(casting, name)
            if key is None:
                return {"ok": False, "error": f"no entry named @{name}"}
            uses = c2c_state.casting_uses(project).get(key) or []
            if uses and not data.get("force"):
                where = ", ".join(f"{u['cid']}.{u['pid']}/{u['pnid']}"
                                  for u in uses[:6])
                return {"ok": False, "needs_force": True,
                        "error": f"@{key} is used by {len(uses)} panel(s) "
                                 f"({where}{'…' if len(uses) > 6 else ''}) - "
                                 f"delete anyway? Their @{key} will show as "
                                 f"unknown until you edit them"}
            casting.pop(key)
            cz_comic.save_project(project, self.dir)
        res = c2c_state.bible_state(project, self.dir)
        res.update({"deleted": key, "orphans": len(uses)})
        return res

    def op_add_ref(self, data):
        """Ajoute une image de reference a une fiche: {name, image (data
        URL / base64 PNG-JPEG)} OU {name, cid, pid, pnid} (la case dessinee
        devient reference). Fichier en refs/<Name>/<n>.png, chemin relatif
        POSIX dans la fiche (le protocole recoit l'absolu au moment voulu)."""
        name = self._cast_name(data.get("name"))
        with _LOCK:
            project = self.load()
            casting = project.get("casting") or {}
            key, fiche = cz_comic._casting_lookup(casting, name)
            if key is None:
                return {"ok": False, "error": f"no entry named @{name} - save "
                                              f"the entry first"}
            from PIL import Image
            import io
            if data.get("pnid"):
                pn = cz_comic.find_panel(project, data["cid"], data["pid"],
                                         data["pnid"])
                src = pn.get("image")
                if not (src and os.path.isfile(src)):
                    return {"ok": False,
                            "error": f"{data['pnid']} has no image yet"}
                with Image.open(src) as im:
                    img = im.convert("RGB")
                    img.load()
                note = f"from {data['cid']}.{data['pid']}/{data['pnid']}"
            else:
                raw = str(data.get("image") or "")
                if "," in raw and raw.startswith("data:"):
                    raw = raw.split(",", 1)[1]
                try:
                    img = Image.open(io.BytesIO(base64.b64decode(raw)))
                    img = img.convert("RGB")
                    img.load()
                except Exception as e:
                    return {"ok": False, "error": f"unreadable image: {e}"}
                note = "uploaded"
            if max(img.size) > 2048:                 # une ref n'a pas besoin de plus
                img.thumbnail((2048, 2048))
            d = os.path.join(self.dir, "refs", key)
            os.makedirs(d, exist_ok=True)
            n = 1
            while os.path.exists(os.path.join(d, f"{n:02d}.png")):
                n += 1
            path = os.path.join(d, f"{n:02d}.png")
            img.save(path + ".tmp", "PNG")
            os.replace(path + ".tmp", path)
            rel = f"refs/{key}/{n:02d}.png"
            fiche.setdefault("refs", []).append(rel)
            cz_comic.save_project(project, self.dir)
        res = c2c_state.bible_state(project, self.dir)
        res.update({"added_ref": rel, "name": key, "note": note})
        return res

    def op_remove_ref(self, data):
        """Retire une reference d'une fiche {name, ref}. Le fichier part
        dans refs/_trash/ (jamais supprime)."""
        name = self._cast_name(data.get("name"))
        ref = str(data.get("ref") or "")
        with _LOCK:
            project = self.load()
            key, fiche = cz_comic._casting_lookup(project.get("casting") or {},
                                                  name)
            if key is None or ref not in (fiche.get("refs") or []):
                return {"ok": False, "error": f"@{name}: no such reference"}
            fiche["refs"] = [r for r in fiche["refs"] if r != ref]
            ap = ref if os.path.isabs(ref) else os.path.join(
                self.dir, *ref.replace("\\", "/").split("/"))
            if os.path.isfile(ap):
                tr = os.path.join(self.dir, "refs", "_trash")
                os.makedirs(tr, exist_ok=True)
                dst = os.path.join(tr, f"{key}_{os.path.basename(ap)}")
                k = 1
                while os.path.exists(dst):
                    k += 1
                    dst = os.path.join(tr, f"{key}_{k}_{os.path.basename(ap)}")
                shutil.move(ap, dst)
            cz_comic.save_project(project, self.dir)
        res = c2c_state.bible_state(project, self.dir)
        res.update({"removed_ref": ref, "name": key})
        return res

    def op_formats(self, _data):
        """Formats de livre proposes a la creation (presets de page)."""
        return {"ok": True,
                "formats": [{"name": n, "note": cz_comic.PAGE_NOTES.get(n, ""), **v}
                            for n, v in cz_comic.PAGE_PRESETS.items()]}

    def op_fonts(self, _data):
        """Polices utilisables pour les bulles: candidates systeme qui se
        chargent + fonts/ du module + fonts/ du livre."""
        return {"ok": True,
                "fonts": cz_comic.available_fonts(
                    extra_dirs=[os.path.join(self.dir, "fonts")])}

    def op_save_style(self, data):
        """Style global + moods: {prompt_suffix?, negative?, loras?, mood?,
        chapter_moods? {cid: mood}}. Les cles absentes ne bougent pas."""
        with _LOCK:
            project = self.load()
            style = project.setdefault("style", {})
            for k in ("prompt_suffix", "negative", "mood", "font"):
                if k in data:
                    style[k] = str(data.get(k) or "").strip()
            if "loras" in data:
                style["loras"] = [str(x).strip() for x in (data.get("loras") or [])
                                  if str(x).strip()]
            cm = data.get("chapter_moods")
            unknown = []
            if isinstance(cm, dict):
                for cid, mood in cm.items():
                    ch = next((c for c in project.get("chapters") or []
                               if c["id"] == cid), None)
                    if ch is None:
                        unknown.append(cid)
                        continue
                    ch["mood"] = str(mood or "").strip()
            cz_comic.save_project(project, self.dir)
        res = c2c_state.bible_state(project, self.dir)
        res["warnings"] = [f"unknown chapter '{c}' ignored" for c in unknown]
        return res

    # ------------------------------------------------------------------
    # 📝 Script (.czs): the whole book as text, import/export without loss
    # ------------------------------------------------------------------
    def op_script_get(self, data):
        """{cid?} -> .czs text of the book (or of one chapter)."""
        project = self.load()
        cid = (data.get("cid") or "").strip() or None
        return {"ok": True, "cid": cid,
                "text": c2c_script.format_script(project, cid),
                "layouts": sorted(cz_comic.LAYOUTS),
                "chapters": [{"id": c["id"], "name": c.get("name") or c["id"]}
                             for c in project.get("chapters") or []]}

    def _script_apply(self, text, dry):
        try:
            outline = c2c_script.parse_script(text)
        except c2c_script.ScriptError as e:
            return {"ok": False, "line": e.line_no, "error": str(e)}
        import copy
        project = self.load()
        work = copy.deepcopy(project) if dry else project
        rep = c2c_script.apply_script(work, outline)
        rep.update({"ok": True, "dry": dry,
                    "chapters": len(outline["chapters"]),
                    "pages": sum(len(c["pages"]) for c in outline["chapters"]),
                    "panels": sum(len(pg["panels"]) for c in outline["chapters"]
                                  for pg in c["pages"])})
        return rep

    def op_script_check(self, data):
        """Dry run: parse + merge on a COPY, report only (nothing written)."""
        return self._script_apply(str(data.get("text") or ""), dry=True)

    def op_script_import(self, data):
        """Parse + merge into the book, save. A parse error refuses the
        whole import (nothing half-applied). Every removal is quoted in the
        report. The pages touched are NOT recomposed here (Compose book)."""
        with _LOCK:
            text = str(data.get("text") or "")
            try:
                outline = c2c_script.parse_script(text)
            except c2c_script.ScriptError as e:
                return {"ok": False, "line": e.line_no, "error": str(e)}
            project = self.load()
            rep = c2c_script.apply_script(project, outline)
            cz_comic.save_project(project, self.dir)
            # keep a copy of what was imported next to the project (diffable)
            try:
                with open(os.path.join(self.dir, "script.czs"), "w",
                          encoding="utf-8") as f:
                    f.write(c2c_script.format_script(project))
            except OSError:
                pass
        idx = c2c_state.book_index(project, self.dir)
        idx.update({"report": rep})
        return idx

    def op_production(self, _data):
        """📊 Tableau de suivi: compteurs + listes (filtres cliquables)."""
        return c2c_state.production_state(self.load(), self.dir)

    # ------------------------------------------------------------------
    # 📦 Bundle (.md): settings + style + bibles + script in ONE text file
    # ------------------------------------------------------------------
    def op_bundle_get(self, _data):
        project = self.load()
        return {"ok": True, "text": c2c_bundle.export_bundle(project),
                "name": project.get("name") or "book"}

    def _ref_exists(self, r):
        ap = r if os.path.isabs(str(r)) else os.path.join(
            self.dir, *str(r).replace("\\", "/").split("/"))
        return os.path.isfile(ap)

    def _bundle_apply(self, text, dry):
        try:
            bundle = c2c_bundle.parse_bundle(text)
        except c2c_bundle.BundleError as e:
            return {"ok": False, "error": str(e)}
        import copy
        project = self.load()
        work = copy.deepcopy(project) if dry else project
        try:
            rep = c2c_bundle.apply_bundle(work, bundle, ref_exists=self._ref_exists)
        except c2c_script.ScriptError as e:
            return {"ok": False, "line": e.line_no,
                    "error": f"script section, {e}"}
        rep.update({"ok": True, "dry": dry,
                    "sections": [k for k in ("settings", "style", "bible", "casting",
                                             "script") if bundle.get(k) is not None]})
        return rep, work

    def op_bundle_check(self, data):
        res = self._bundle_apply(str(data.get("text") or ""), dry=True)
        return res[0] if isinstance(res, tuple) else res

    def op_bundle_import(self, data):
        """Apply the bundle to the book and save; a parse error in any
        section refuses the whole import (the check runs on a copy first,
        so nothing is half-applied). A book.md copy is kept in the book."""
        with _LOCK:
            text = str(data.get("text") or "")
            chk = self._bundle_apply(text, dry=True)
            if not isinstance(chk, tuple):
                return chk
            res = self._bundle_apply(text, dry=False)
            rep, project = res
            cz_comic.save_project(project, self.dir)
            try:
                with open(os.path.join(self.dir, "book.md"), "w", encoding="utf-8") as f:
                    f.write(c2c_bundle.export_bundle(project))
            except OSError:
                pass
        idx = c2c_state.book_index(project, self.dir)
        idx.update({"report": rep})
        return idx

    def op_set_shape(self, data):
        """Forme d'une case: {cid, pid, pnid, points: [[fx, fy, r?]...] | null,
        z?}. points null = retour au rectangle de la cellule. Un polygone
        a au moins 3 points; coordonnees en fractions de page, clampees.
        Recompose la planche."""
        cid, pid, pnid = data["cid"], data["pid"], data["pnid"]
        with _LOCK:
            project = self.load()
            panel = cz_comic.find_panel(project, cid, pid, pnid)
            pts = data.get("points")
            if pts is None and "z" not in data:
                panel.pop("shape", None)
            else:
                shape = dict(panel.get("shape") or {})
                if pts is not None:
                    clean = []
                    for q in pts:
                        try:
                            fx, fy = float(q[0]), float(q[1])
                            r = float(q[2]) if len(q) > 2 and q[2] is not None else 0.0
                        except (TypeError, ValueError, IndexError):
                            return {"ok": False, "error": "bad shape point (need [fx, fy, r?])"}
                        clean.append([round(max(0.0, min(1.0, fx)), 4),
                                      round(max(0.0, min(1.0, fy)), 4),
                                      round(max(0.0, min(1.0, r)), 3)])
                    if len(clean) < 3:
                        return {"ok": False, "error": "a panel shape needs at least 3 corners"}
                    shape["points"] = clean
                if "z" in data:
                    try:
                        shape["z"] = int(data.get("z") or 0)
                    except (TypeError, ValueError):
                        shape["z"] = 0
                if not shape.get("points"):
                    page = cz_comic.find_page(project, cid, pid)
                    shape["points"] = cz_comic.cell_shape(
                        project, page, page["panels"].index(panel))["points"]
                panel["shape"] = shape
            cz_comic.save_project(project, self.dir)
            fd, emb = self._letter_kit(project)
            c2c_state.compose_one(project, self.dir, cid, pid,
                                  face_detector=fd, char_embeddings=emb)
        return c2c_state.book_index(project, self.dir)

    def op_shape_presets(self, _data):
        """Gabarits obliques: [{name, label, count, shapes (u/v)}] pour l'UI."""
        return {"ok": True,
                "presets": [{"name": n, "label": v["label"], "count": len(v["shapes"]),
                             "shapes": v["shapes"]}
                            for n, v in cz_comic.SHAPE_PRESETS.items()]}

    def op_apply_shape_preset(self, data):
        """Pose un gabarit oblique sur une planche: {cid, pid, name}. La
        planche doit avoir autant de cases que le gabarit (sinon message:
        changer d'abord de mise en page). Recompose."""
        cid, pid, name = data["cid"], data["pid"], str(data.get("name") or "")
        with _LOCK:
            project = self.load()
            page = cz_comic.find_page(project, cid, pid)
            pg = cz_comic.page_size(project.get("page"))
            try:
                shapes = cz_comic.shape_preset_shapes(name, pg)
            except ValueError as e:
                return {"ok": False, "error": str(e)}
            n = len(page.get("panels") or [])
            if n != len(shapes):
                return {"ok": False,
                        "error": f"'{name}' is for {len(shapes)} panels, this page "
                                 f"has {n} - pick a layout with {len(shapes)} "
                                 f"panels first (Layout), then apply the preset"}
            for pn, sh in zip(page["panels"], shapes):
                pn["shape"] = sh
            cz_comic.save_project(project, self.dir)
            fd, emb = self._letter_kit(project)
            c2c_state.compose_one(project, self.dir, cid, pid,
                                  face_detector=fd, char_embeddings=emb)
        idx = c2c_state.book_index(project, self.dir)
        idx["applied"] = name
        return idx

    def op_set_bubble(self, data):
        """Deplace une bulle: {cid, pid, pnid, index, pos|anchor: [fx, fy]}
        ou {clear: ["pos", "anchor"]}. pos = coin haut-gauche de la bulle,
        anchor = pointe de la queue - en FRACTIONS DE CASE, clampes ici.
        Recompose la planche (lettrage) et renvoie l'index."""
        with _LOCK:
            project = self.load()
            panel = cz_comic.find_panel(project, data["cid"], data["pid"],
                                        data["pnid"])
            dlg = panel.get("dialogue") or []
            idx = int(data.get("index", -1))
            if not 0 <= idx < len(dlg):
                raise IndexError(f"dialogue index {idx} out of range "
                                 f"(panel has {len(dlg)} line(s))")
            for key in ("pos", "anchor"):
                if data.get(key) is not None:
                    fx, fy = data[key]
                    dlg[idx][key] = [max(0.0, min(1.0, float(fx))),
                                     max(0.0, min(1.0, float(fy)))]
            # type de bulle (parole/pensee/cartouche/sfx), forme, homothetie
            if data.get("kind") is not None:
                if data["kind"] not in cz_comic.DIALOGUE_KINDS:
                    raise ValueError(f"kind must be one of "
                                     f"{cz_comic.DIALOGUE_KINDS}")
                dlg[idx]["kind"] = data["kind"]
            if "style" in data:
                st = data.get("style")
                if st in cz_comic.BUBBLE_STYLES:
                    dlg[idx]["style"] = st
                elif st in (None, ""):
                    dlg[idx].pop("style", None)   # retour au style du livre
                else:
                    raise ValueError(f"style must be one of "
                                     f"{cz_comic.BUBBLE_STYLES} (or empty)")
            if data.get("scale") is not None:
                sc = max(0.4, min(3.0, float(data["scale"])))
                if abs(sc - 1.0) < 0.05:
                    dlg[idx].pop("scale", None)   # ~1.0 = pas d'override
                else:
                    dlg[idx]["scale"] = round(sc, 2)
            # 👁 masquee: gardee dans le scenario, pas lettree (le texte est
            # deja dans l'image, ou la case doit rester muette)
            if data.get("hidden") is not None:
                if data["hidden"]:
                    dlg[idx]["hidden"] = True
                else:
                    dlg[idx].pop("hidden", None)
            if data.get("outline") is not None:
                ok_ = max(0.3, min(3.0, float(data["outline"])))
                if abs(ok_ - 1.0) < 0.05:
                    dlg[idx].pop("outline", None)
                else:
                    dlg[idx]["outline"] = round(ok_, 2)
            if "font" in data:
                f = str(data.get("font") or "").strip()
                if f:
                    dlg[idx]["font"] = f
                else:
                    dlg[idx].pop("font", None)      # police du livre
            for key in data.get("clear") or []:
                if key in ("pos", "anchor", "scale", "style", "hidden", "font", "outline"):
                    dlg[idx].pop(key, None)
            if data.get("remove"):
                # 🗑 la replique quitte le dialogue de la case (le texte est
                # renvoye dans la reponse: rien de perdu sans trace)
                gone = dlg.pop(idx)
                panel["dialogue"] = dlg
            cz_comic.save_project(project, self.dir)
            fd, emb = self._letter_kit(project)
            c2c_state.compose_one(project, self.dir, data["cid"], data["pid"],
                                  face_detector=fd, char_embeddings=emb)
        out = c2c_state.book_index(project, self.dir)
        if data.get("remove"):
            out["removed"] = gone
        return out

    # ---- generation du livre ENTIER en tache de fond (progression, stop) ----
    JOB = {"running": False, "stop": False, "total": 0, "done": 0,
           "current": "", "generated": 0, "errors": [], "warnings": [],
           "started": 0.0, "finished": 0.0}

    def op_generate_all(self, data):
        """🎨 Genere toutes les cases manquantes du livre, planche par
        planche, dans un thread: l'UI suit via job_status (compteurs, page
        courante) et peut arreter avec job_stop (fin de la planche en
        cours). Arret a la premiere erreur moteur - jamais un trou
        silencieux."""
        job = Studio.JOB
        if job["running"]:
            return {"ok": False,
                    "error": "a book generation is already running - wait "
                             "or press Stop"}
        project = self.load()
        force = bool(data.get("force"))
        # taches = (cid, pid, pnid|None): tout le livre planche par planche,
        # ou une liste de cases precises ({cid, pid, pnid}) - p.ex. "redessiner
        # les 7 cases ou @Lea apparait" apres un changement de fiche
        if isinstance(data.get("panels"), list) and data["panels"]:
            tasks = []
            for t in data["panels"]:
                if isinstance(t, dict) and t.get("cid") and t.get("pid"):
                    tasks.append((t["cid"], t["pid"], t.get("pnid")))
            force = True                          # on redessine ce qu'on cite
        else:
            tasks = [(ch["id"], pg["id"], None)
                     for ch, pg in cz_comic.book_order(project)]
        job.update({"running": True, "stop": False, "total": len(tasks),
                    "done": 0, "current": "", "generated": 0, "errors": [],
                    "warnings": [], "started": time.time(), "finished": 0.0,
                    "label": str(data.get("label") or
                                 ("Redrawing the whole book" if force and not
                                  data.get("panels") else
                                  "Generating the missing panels"))[:120],
                    "unit": "panel" if data.get("panels") else "page",
                    "last_s": 0.0, "log": [], "paused": False})
        engine = data.get("engine")
        storyboard = bool(data.get("storyboard"))
        if storyboard:
            job["label"] = str(data.get("label") or "Storyboard (quick sketches)")[:120]

        def worker():
            try:
                for cid, pid, pnid in tasks:
                    # ⏸ pause: on attend ICI, entre deux elements - jamais au
                    # milieu d'un rendu (l'image en cours se termine d'abord)
                    while job["paused"] and not job["stop"]:
                        job["current"] = ""
                        time.sleep(0.3)
                    if job["stop"]:
                        break
                    job["current"] = f"{cid}.{pid}" + (f"/{pnid}" if pnid else "")
                    t1 = time.time()
                    res = self.op_generate({"cid": cid, "pid": pid,
                                            "pnid": pnid, "force": force,
                                            "engine": engine,
                                            "storyboard": storyboard})
                    job["last_s"] = round(time.time() - t1, 1)
                    if not res.get("ok"):
                        job["errors"].append(f"{cid}.{pid}: "
                                             f"{res.get('error')}")
                        job["log"].append(f"❌ {job['current']}: {res.get('error')}")
                        break
                    n = len(res.get("generated") or [])
                    job["generated"] += n
                    seeds = ", ".join(str(g.get("seed_used")) for g in
                                      (res.get("generated") or [])[:4])
                    job["log"].append(f"✓ {job['current']}: {n} panel(s) in "
                                      f"{job['last_s']}s" + (f" (seed {seeds})"
                                                             if seeds else ""))
                    del job["log"][:-12]
                    for w in res.get("warnings") or []:
                        if w not in job["warnings"] and len(job["warnings"]) < 40:
                            job["warnings"].append(w)
                    job["done"] += 1
            except Exception as e:                  # jamais un thread muet
                job["errors"].append(f"{type(e).__name__}: {e}")
            finally:
                job["running"] = False
                job["current"] = ""
                job["finished"] = time.time()

        threading.Thread(target=worker, daemon=True).start()
        return {"ok": True, "job": dict(job)}

    def op_job_status(self, _data):
        return {"ok": True, "job": dict(Studio.JOB)}

    def op_job_stop(self, _data):
        Studio.JOB["stop"] = True
        return {"ok": True, "job": dict(Studio.JOB)}

    def op_job_pause(self, data):
        """⏸ / ▶ : {paused: bool}. La pause prend effet apres l'element en
        cours (une image ne s'interrompt pas a mi-chemin) ; reprendre
        continue la liste la ou elle en etait."""
        job = Studio.JOB
        if not job.get("running"):
            return {"ok": False, "error": "no generation running"}
        job["paused"] = bool(data.get("paused", True))
        return {"ok": True, "job": dict(job)}

    def op_edit_panel(self, data):
        """✏️ Retoucher UNE case par INSTRUCTION (op edit du protocole:
        image + instruction -> image, Qwen-Image-Edit / Z-Image Omni).
        {cid, pid, pnid, instruction, engine?}. L'image precedente est
        gardee en <pnid>.prev.png (↩ restore_panel), la planche recomposee."""
        cid, pid, pnid = data["cid"], data["pid"], data["pnid"]
        instr = str(data.get("instruction") or "").strip()
        if not instr:
            return {"ok": False, "error": "write the edit instruction first "
                                          "(e.g. 'add rain', 'she looks left')"}
        project = self.load()
        name, eng = self._default_engine(data.get("engine"))
        book_eng = (project.get("engine") or {}).get("name")
        if not data.get("engine") and book_eng in self.engines:
            name, eng = self._default_engine(book_eng)
        if eng is None:
            return {"ok": False,
                    "error": f"no engine '{name}' (config.json 'engines')"}
        if not hasattr(eng, "edit"):
            return {"ok": False, "error": f"engine '{name}' cannot edit"}
        panel = cz_comic.find_panel(project, cid, pid, pnid)
        src = panel.get("image")
        if not (src and os.path.isfile(src)):
            return {"ok": False,
                    "error": f"{pnid} has no image yet - generate it first, "
                             f"then edit it"}
        res = eng.edit({"input": os.path.abspath(src), "prompt": instr,
                        "out_dir": os.path.dirname(os.path.abspath(src))})
        if not res.get("ok"):
            return {"ok": False, "engine": name,
                    "error": f"{pnid}: {res.get('error')}"}
        out = (res.get("images") or [None])[0]
        if not out or not os.path.isfile(out):
            return {"ok": False, "engine": name,
                    "error": f"{pnid}: engine returned no readable image"}
        with _LOCK:
            c2c_state.replace_panel_image(src, out, "edit", instr)
            c2c_state.discard_engine_output(out, os.path.dirname(src))
            panel["status"] = "rendered"
            cz_comic.save_project(project, self.dir)
            fd, emb = self._letter_kit(project)
            c2c_state.compose_one(project, self.dir, cid, pid,
                                  face_detector=fd, char_embeddings=emb)
        idx = c2c_state.book_index(project, self.dir)
        idx.update({"engine": name, "edited": pnid,
                    "warnings": res.get("warnings") or []})
        return idx

    def op_vary_panel(self, data):
        """🔁 VARIATION d'une case (img2img): on repart de l'image existante
        avec une force de denoise - 0.3 = meme composition legerement
        redessinee, 0.6 = variation franche. Passe par l'op upscale du
        protocole (facteur 1, ESRGAN saute par le facteur, refine guide
        par le TEXTE de la case: c'est l'image entiere, pas un fragment).
        Marche sur TOUS les moteurs, pas seulement ceux qui savent editer.
        {cid, pid, pnid, strength?, engine?} ; ancienne image gardee (↩)."""
        cid, pid, pnid = data["cid"], data["pid"], data["pnid"]
        try:
            strength = float(data.get("strength", 0.45))
        except (TypeError, ValueError):
            strength = 0.45
        strength = max(0.1, min(0.9, strength))
        project = self.load()
        name, eng = self._default_engine(data.get("engine"))
        book_eng = (project.get("engine") or {}).get("name")
        if not data.get("engine") and book_eng in self.engines:
            name, eng = self._default_engine(book_eng)
        if eng is None or not hasattr(eng, "upscale"):
            return {"ok": False,
                    "error": f"no engine '{name}' able to refine images"}
        panel = cz_comic.find_panel(project, cid, pid, pnid)
        src = panel.get("image")
        if not (src and os.path.isfile(src)):
            return {"ok": False,
                    "error": f"{pnid} has no image yet - generate it first"}
        page = cz_comic.find_page(project, cid, pid)
        spec = cz_comic.resolve_panel(project, page, panel,
                                      index=page["panels"].index(panel))
        res = eng.upscale({"input": os.path.abspath(src), "factor": 1.0,
                           "denoise": strength, "prompt": spec["prompt"],
                           "out_dir": os.path.dirname(os.path.abspath(src))})
        if not res.get("ok"):
            return {"ok": False, "engine": name,
                    "error": f"{pnid}: {res.get('error')}"}
        out = (res.get("images") or [None])[0]
        if not out or not os.path.isfile(out):
            return {"ok": False, "engine": name,
                    "error": f"{pnid}: engine returned no readable image"}
        with _LOCK:
            c2c_state.replace_panel_image(src, out, "variation",
                                          f"strength {strength}")
            c2c_state.discard_engine_output(out, os.path.dirname(src))
            cz_comic.save_project(project, self.dir)
            fd, emb = self._letter_kit(project)
            c2c_state.compose_one(project, self.dir, cid, pid,
                                  face_detector=fd, char_embeddings=emb)
        idx = c2c_state.book_index(project, self.dir)
        idx.update({"engine": name, "varied": pnid, "strength": strength,
                    "warnings": res.get("warnings") or []})
        return idx

    def op_inpaint_panel(self, data):
        """🖌 INPAINT d'une case: la zone peinte (masque PNG, blanc = a
        redessiner) est redessinee selon un prompt LOCAL (ce qui doit
        apparaitre LA - jamais le prompt de scene sur un fragment). Op
        inpaint du protocole, tous les moteurs. {cid, pid, pnid, mask
        (data URL PNG ou base64, a la taille de l'image ou non), prompt?,
        strength?, engine?}. Masque garde en <pnid>.mask.png (refaire avec
        la meme zone), ancienne image dans l'historique."""
        cid, pid, pnid = data["cid"], data["pid"], data["pnid"]
        raw = str(data.get("mask") or "")
        if "," in raw and raw.startswith("data:"):
            raw = raw.split(",", 1)[1]
        try:
            mask_bytes = base64.b64decode(raw) if raw else b""
        except Exception:
            mask_bytes = b""
        if not mask_bytes:
            return {"ok": False, "error": "paint the area to redraw first "
                                          "(the mask is empty)"}
        prompt = str(data.get("prompt") or "").strip()
        try:
            strength = float(data.get("strength", 0.9))
        except (TypeError, ValueError):
            strength = 0.9
        strength = max(0.2, min(1.0, strength))
        project = self.load()
        name, eng = self._default_engine(data.get("engine"))
        book_eng = (project.get("engine") or {}).get("name")
        if not data.get("engine") and book_eng in self.engines:
            name, eng = self._default_engine(book_eng)
        if eng is None or not hasattr(eng, "inpaint"):
            return {"ok": False,
                    "error": f"no engine '{name}' able to inpaint"}
        panel = cz_comic.find_panel(project, cid, pid, pnid)
        src = panel.get("image")
        if not (src and os.path.isfile(src)):
            return {"ok": False,
                    "error": f"{pnid} has no image yet - generate it first"}
        mask_path = os.path.splitext(src)[0] + ".mask.png"
        with open(mask_path + ".tmp", "wb") as f:
            f.write(mask_bytes)
        os.replace(mask_path + ".tmp", mask_path)
        extra = []
        try:
            from PIL import Image
            with Image.open(src) as im:
                img_size = im.size
            with Image.open(mask_path) as mk:
                g = mk.convert("L")
                if g.size != img_size:
                    # masque peint a la taille d'AFFICHAGE -> taille de l'image
                    g = g.resize(img_size, Image.NEAREST)
                    g.save(mask_path)
                if g.getbbox() is None:
                    return {"ok": False,
                            "error": "the painted area is empty - brush "
                                     "over what should be redrawn"}
                h = g.histogram()
                white = sum(h[128:]) / float(g.size[0] * g.size[1])
                if white > 0.95:
                    extra.append(f"the mask covers {white:.0%} of the panel: "
                                 f"nearly everything is redrawn (Variation "
                                 f"or Regenerate may be what you want)")
        except Exception as e:
            return {"ok": False, "error": f"unreadable mask: {e}"}
        # style GLOBAL du livre (suffixe) pour rester dans le trait; le prompt
        # de scene de la case, lui, n'est JAMAIS envoye sur un fragment
        suffix = ((project.get("style") or {}).get("prompt_suffix") or "").strip()
        full_prompt = ", ".join(x for x in (prompt, suffix) if x)
        res = eng.inpaint({"input": os.path.abspath(src),
                           "mask": os.path.abspath(mask_path),
                           "prompt": full_prompt, "denoise": strength,
                           "seed": int(data.get("seed", -1) or -1),
                           "out_dir": os.path.dirname(os.path.abspath(src))})
        if not res.get("ok"):
            return {"ok": False, "engine": name,
                    "error": f"{pnid}: {res.get('error')}"}
        out = (res.get("images") or [None])[0]
        if not out or not os.path.isfile(out):
            return {"ok": False, "engine": name,
                    "error": f"{pnid}: engine returned no readable image"}
        with _LOCK:
            c2c_state.replace_panel_image(src, out, "inpaint",
                                          prompt or "(no prompt)")
            c2c_state.discard_engine_output(out, os.path.dirname(src))
            panel["status"] = "rendered"
            cz_comic.save_project(project, self.dir)
            fd, emb = self._letter_kit(project)
            c2c_state.compose_one(project, self.dir, cid, pid,
                                  face_detector=fd, char_embeddings=emb)
        idx = c2c_state.book_index(project, self.dir)
        idx.update({"engine": name, "inpainted": pnid, "strength": strength,
                    "seed_used": res.get("seed_used"),
                    "warnings": extra + (res.get("warnings") or [])})
        return idx

    def op_restore_panel(self, data):
        """Revenir a une version de l'historique de la case: {cid, pid,
        pnid, version?}. version = nom de fichier dans <pnid>.history/
        (absent = la plus recente). La version courante est archivee avant
        (rien de perdu), la planche recomposee."""
        cid, pid, pnid = data["cid"], data["pid"], data["pnid"]
        version = (data.get("version") or "").strip() or None
        with _LOCK:
            project = self.load()
            panel = cz_comic.find_panel(project, cid, pid, pnid)
            src = panel.get("image") or cz_comic.panel_path(
                self.dir, cid, pid, pnid)
            picked = c2c_state.restore_panel_version(src, version)
            if picked is None:
                return {"ok": False,
                        "error": f"{pnid}: no previous version to restore"
                                 + (f" ({version} not in the history)"
                                    if version else "")}
            panel["image"] = src
            panel["status"] = "rendered"
            cz_comic.save_project(project, self.dir)
            fd, emb = self._letter_kit(project)
            c2c_state.compose_one(project, self.dir, cid, pid,
                                  face_detector=fd, char_embeddings=emb)
        idx = c2c_state.book_index(project, self.dir)
        idx.update({"restored": pnid, "version": picked})
        return idx

    def op_export_print(self, data):
        """🖨 Sortie PRINT: chaque case remonte a la resolution d'impression
        via l'op upscale du protocole (ESRGAN + refine du moteur), puis les
        planches sont recomposees en A4 300dpi (lettrage vectoriel = net) et
        exportees en PDF. {engine?, page_preset?}. Reprise gratuite: une
        case deja upscalee a la bonne taille n'est pas refaite."""
        name, eng = self._default_engine(data.get("engine"))
        if eng is None:
            return {"ok": False,
                    "error": f"no engine '{name}' (config.json 'engines')"}
        preset = data.get("page_preset") or "A4 300dpi"
        project = self.load()
        src_pg = cz_comic.page_size(project.get("page"))
        prj = dict(project)
        pg = dict(cz_comic.page_size(preset))
        pg["page_numbers"] = bool(src_pg.get("page_numbers", False))
        if src_pg.get("reading"):
            pg["reading"] = src_pg["reading"]
        prj["page"] = pg
        from PIL import Image
        done_up, warnings, missing = 0, [], 0
        out_dir = os.path.join(self.dir, "print")
        folios = c2c_state._folios(prj)
        numbers = bool(pg.get("page_numbers", False))
        for ch, page in cz_comic.book_order(prj):
            rects = cz_comic.page_rects(prj, page, pg)
            images = {}
            for i, panel in enumerate(page["panels"]):
                srcp = panel.get("image")
                if not (srcp and os.path.isfile(srcp)):
                    missing += 1
                    continue
                if i >= len(rects):
                    continue
                tw = rects[i][2]
                dst = os.path.join(out_dir, "panels", ch["id"], page["id"],
                                   f"{panel['id']}.png")
                with Image.open(srcp) as im:
                    sw = im.width
                need = tw / max(1, sw)
                if need <= 1.02:
                    images[panel["id"]] = Image.open(srcp)
                    continue
                if os.path.isfile(dst) and \
                        os.path.getmtime(dst) >= os.path.getmtime(srcp):
                    images[panel["id"]] = Image.open(dst)   # deja fait
                    continue
                # sujet LOCAL pour le refine (JAMAIS le prompt de scene sur
                # un fragment - regle maison): description du personnage
                subject = cz_comic.detail_prompt(prj, panel)
                res = eng.upscale({"input": os.path.abspath(srcp),
                                   "factor": min(8.0, round(need + 0.05, 2)),
                                   "prompt": subject,
                                   "out_dir": os.path.dirname(dst)})
                if not res.get("ok"):
                    return {"ok": False, "engine": name,
                            "error": f"{ch['id']}.{page['id']}."
                                     f"{panel['id']}: {res.get('error')}"}
                up = (res.get("images") or [None])[0]
                if not up or not os.path.isfile(up):
                    return {"ok": False, "engine": name,
                            "error": f"{panel['id']}: engine returned no "
                                     f"readable image"}
                os.makedirs(os.path.dirname(dst), exist_ok=True)
                shutil.copy2(up, dst)
                images[panel["id"]] = Image.open(dst)
                done_up += 1
            sheet = cz_comic.compose_page(prj, page, images=images)
            for im in images.values():
                try:
                    im.close()
                except Exception:
                    pass
            fd, emb = self._letter_kit(prj)
            cz_comic.render_lettering(prj, page, sheet, face_detector=fd,
                                      char_embeddings=emb)
            folio = folios.get((ch["id"], page["id"]))
            if folio and numbers:
                cz_comic._draw_page_number(sheet, pg, folio)
            pdst = os.path.join(out_dir, "pages", ch["id"],
                                f"{page['id']}.png")
            os.makedirs(os.path.dirname(pdst), exist_ok=True)
            tmpf = pdst + f".{os.getpid()}.tmp"
            sheet.save(tmpf, "PNG")
            os.replace(tmpf, pdst)
        paths = [os.path.join(out_dir, "pages", ch["id"], f"{p['id']}.png")
                 for ch, p in cz_comic.book_order(prj)]
        pdf = cz_comic.export_pdf(
            [Image.open(x) for x in paths],
            os.path.join(out_dir, "book-print.pdf"),
            dpi=int(pg.get("dpi", 300)))
        if missing:
            warnings.append(f"{missing} panel(s) have no image yet - they "
                            f"print as placeholders (Generate them first)")
        return {"ok": True, "engine": name, "pdf": "print/book-print.pdf",
                "upscaled": done_up, "pages": len(paths),
                "preset": preset, "warnings": warnings}

    def op_generate(self, data):
        """Generate the panels of ONE page through a family engine (CLI
        protocol): {cid, pid, pnid?, engine?, force?}. Without pnid = every
        panel WITHOUT an image (force = redo them all). The engine writes
        into ITS output folder, the image is copied to
        panels/<cid>/<pid>/<pnid>.png and project.json is saved after EACH
        panel (a failure in the middle loses nothing). Stops at the first
        engine failure - never a silent hole. Recomposes the page at the
        end."""
        cid, pid = data["cid"], data["pid"]
        only, force = data.get("pnid"), bool(data.get("force"))
        project = self.load()
        # priorite du moteur: demande explicite > moteur du LIVRE (wizard) >
        # defaut de la config
        book_eng = (project.get("engine") or {}).get("name")
        name, eng = self._default_engine(
            data.get("engine") or (book_eng if book_eng in self.engines
                                   else None))
        if eng is None:
            return {"ok": False,
                    "error": f"no engine '{name}' (config.json 'engines')"}
        done, warnings = [], []
        # divergence de modele: le livre a un modele voulu, l'instance en a
        # un autre charge -> on PREVIENT (jamais de swap sous les pieds)
        want = ((project.get("engine") or {}).get("model") or "").strip()
        caps = eng.alive() if hasattr(eng, "alive") else None
        loaded = str((caps or {}).get("model_loaded") or "")
        if want and loaded and \
                os.path.basename(want).lower() != os.path.basename(loaded).lower():
            warnings.append(
                f"this book is set to model '{os.path.basename(want)}' but "
                f"the running {name} instance has '{loaded}' loaded - switch "
                f"it in the app or the pages will mix styles")
        page = cz_comic.find_page(project, cid, pid)
        for i in range(len(page["panels"])):
            panel = page["panels"][i]
            if only and panel["id"] != only:
                continue
            if (panel.get("image") and os.path.isfile(panel["image"])
                    and not force):
                continue
            if panel.get("status") == "locked" and not only:
                # 🔒 une case verrouillee n'est jamais redessinee par un
                # 'generate missing' / 'redraw all' - seulement a la demande
                warnings.append(f"{panel['id']}: locked - skipped")
                continue
            # garde-fou sur le TEXTE de la case, pas le prompt resolu: une
            # case vide + un suffixe de style global donnerait un prompt
            # 'style seulement' qui partirait au moteur pour rien
            if not (panel.get("text") or "").strip():
                warnings.append(f"{panel['id']}: empty panel text - skipped "
                                f"(write the panel description first)")
                continue
            storyboard = bool(data.get("storyboard"))
            if storyboard:
                # 🎞 profil storyboard: croquis rapide pour valider decoupage,
                # cadrage et place du texte - style remplace par un crayonne,
                # pas de mood, pas de LoRA, pas de refs, moitie de pixels,
                # 4 steps. La case est marquee 'storyboard' -> Production
                # propose le rendu final.
                import copy
                sb = copy.deepcopy(project)
                sb["style"] = {"prompt_suffix": "rough pencil storyboard sketch, loose "
                                                "lines, greyscale, simple shapes, "
                                                "clear composition",
                               "negative": "color, detailed, photo, text", "loras": [],
                               "mood": ""}
                for c_ in sb.get("chapters") or []:
                    c_["mood"] = ""
                for c_ in (sb.get("casting") or {}).values():
                    c_["refs"], c_["loras"] = [], []
                sb_page = cz_comic.find_page(sb, cid, pid)
                spec = cz_comic.resolve_panel(sb, sb_page, sb_page["panels"][i],
                                              index=i, target_pixels=512 * 512)
                spec["loras"], spec["refs"] = [], []
                spec["steps"] = int(self.config.get("storyboard_steps") or 4)
            else:
                spec = cz_comic.resolve_panel(project, page, panel, index=i)
            if not (spec["prompt"] or "").strip():
                warnings.append(f"{panel['id']}: empty panel text - skipped "
                                f"(write the panel description first)")
                continue
            if spec["unknown"]:
                warnings.append(f"{panel['id']}: unknown casting "
                                f"{', '.join(spec['unknown'])}")
            # refs v2: casting refs are stored project-relative (POSIX) -
            # the protocol wants LOCAL ABSOLUTE paths. A ref missing on disk
            # is reported and dropped HERE (we know the project); sending it
            # would fail the whole panel with a code-2 spec error.
            refs = []
            for r in spec.get("refs") or []:
                p = r if os.path.isabs(r) else os.path.join(
                    self.dir, *str(r).replace("\\", "/").split("/"))
                if os.path.isfile(p):
                    refs.append(os.path.abspath(p))
                else:
                    warnings.append(f"{panel['id']}: ref missing on disk, "
                                    f"dropped: {r}")
            payload = {"prompt": spec["prompt"],
                       "negative": spec["negative"],
                       "width": spec["width"], "height": spec["height"],
                       "seed": spec["seed"], "loras": spec["loras"],
                       "refs": refs}
            if storyboard:
                payload["steps"] = spec["steps"]
            # Detailer auto: une case qui met en scene un PERSONNAGE du
            # casting recoit la passe visages (ADetailer cote moteur).
            # Absent sinon -> reglage par defaut de l'outil. Mains: opt-in
            # global (config detail_hands: paquet ultralytics optionnel).
            used = cz_comic.resolve_casting(
                panel.get("text") or "", project.get("casting"))["used"]
            if any((project.get("casting", {}).get(u) or {})
                   .get("kind", "character") == "character" for u in used) \
                    and not storyboard:
                payload["detail_faces"] = True
            if self.config.get("detail_hands") and not storyboard:
                payload["detail_hands"] = True
            res = eng.gen(payload)
            if not res.get("ok"):
                return {"ok": False, "engine": name, "generated": done,
                        "error": f"{panel['id']}: {res.get('error')}"}
            src = (res.get("images") or [None])[0]
            if not src or not os.path.isfile(src):
                return {"ok": False, "engine": name, "generated": done,
                        "error": f"{panel['id']}: engine returned no "
                                 f"readable image ({src})"}
            dst = cz_comic.panel_path(self.dir, cid, pid, panel["id"])
            with _LOCK:                     # re-read: the user may have edited
                # une case deja dessinee (force / regenerate): l'ancienne
                # version part dans l'historique, jamais ecrasee en silence
                c2c_state.replace_panel_image(
                    dst, src, "regenerate",
                    f"seed {res.get('seed_used')}"
                    if res.get("seed_used") is not None else "")
                c2c_state.discard_engine_output(src, os.path.dirname(dst))
                project = self.load()
                page = cz_comic.find_page(project, cid, pid)
                pn = cz_comic.find_panel(project, cid, pid, panel["id"])
                pn["image"] = dst
                pn["status"] = "rendered"
                pn["style_sig"] = ("storyboard" if storyboard else
                                   c2c_state.style_signature(project, page))
                if storyboard:
                    pn["storyboard"] = True
                else:
                    pn.pop("storyboard", None)
                cz_comic.save_project(project, self.dir)
            done.append({"panel": panel["id"],
                         "seed_used": res.get("seed_used"),
                         "total_s": (res.get("timings") or {}).get("total_s")})
            for w in res.get("warnings") or []:
                if w not in warnings:
                    warnings.append(w)
        if done:
            with _LOCK:
                project = self.load()
                fd, emb = self._letter_kit(project)
                c2c_state.compose_one(project, self.dir, cid, pid,
                                      face_detector=fd, char_embeddings=emb)
        out = c2c_state.book_index(self.load(), self.dir)
        out.update({"engine": name, "generated": done, "warnings": warnings})
        return out

    def op_engines(self, _data):
        """Capacites de chaque moteur + ce que le LIVRE demande et que le moteur
        courant ne sait pas faire.

        Sans ce recoupement, un champ rempli dans le livre peut n'avoir AUCUN
        effet sans que rien ne le dise: le negative prompt d'un style, par exemple,
        est inerte sur un modele distille (crispz-klein). On le decouvrait apres
        avoir attendu un rendu, ou jamais."""
        caps = {name: e.caps() for name, e in self.engines.items()}
        cur = self.config.get("engine")
        warnings = []
        sup = ((caps.get(cur) or {}).get("supports") or {})
        try:
            project = self.load()
        except Exception:
            project = None
        if project and sup:
            style = project.get("style") or {}
            if sup.get("negative") is False:
                users = []
                if (style.get("negative") or "").strip():
                    users.append("the book style")
                if any((c.get("negative") or "").strip()
                       for c in (project.get("casting") or {}).values()):
                    users.append("a casting entry")
                if users:
                    warnings.append(
                        f"engine '{cur}' ignores negative prompts (distilled model, "
                        f"no CFG) but {' and '.join(users)} set one - it has no "
                        f"effect on the image")
            if sup.get("refs") is False and any(
                    (c.get("refs") or []) for c in (project.get("casting") or {}).values()):
                warnings.append(
                    f"engine '{cur}' cannot use reference images, but the casting "
                    f"has some - @Name consistency will fall back to the text only")
            if sup.get("inpaint") is False:
                warnings.append(f"engine '{cur}' has no inpaint pipeline - "
                                f"the Inpaint tool is unavailable")
        return {"ok": True, "engines": caps, "default": cur,
                "warnings": warnings}

    def dispatch(self, op, data):
        fn = getattr(self, f"op_{op}", None)
        if fn is None:
            return {"ok": False, "error": f"unknown op '{op}'"}
        try:
            out = fn(data or {})
            out.setdefault("ok", True)
            return out
        except Exception as e:
            return {"ok": False, "error": f"{type(e).__name__}: {e}"}


def _safe_join(root, rel):
    """File path under `root`, or None when the request tries to escape it."""
    rel = posixpath.normpath(rel.lstrip("/"))
    if rel.startswith("..") or os.path.isabs(rel):
        return None
    p = os.path.abspath(os.path.join(root, *rel.split("/")))
    return p if p.startswith(os.path.abspath(root)) else None


class Handler(BaseHTTPRequestHandler):
    studio = None                      # set by serve()

    # ---- helpers ----
    def _send(self, code, body, ctype="application/json; charset=utf-8"):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _send_json(self, obj, code=200):
        self._send(code, json.dumps(obj, ensure_ascii=False).encode("utf-8"))

    def _send_file(self, path, cache=False):
        if not path or not os.path.isfile(path):
            self._send_json({"ok": False, "error": "not found"}, 404)
            return
        ctype = mimetypes.guess_type(path)[0] or "application/octet-stream"
        with open(path, "rb") as f:
            body = f.read()
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        if cache:
            self.send_header("Cache-Control", "max-age=31536000, immutable")
        else:
            self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    # ---- routes ----
    def do_GET(self):
        path = self.path.split("?", 1)[0]
        if path in ("/", "/index.html"):
            self._send_file(os.path.join(ASSETS, "studio.html"))
        elif path.startswith("/assets/"):
            self._send_file(_safe_join(ASSETS, path[len("/assets/"):]))
        elif path.startswith("/file/"):
            self._send_file(_safe_join(self.studio.dir, path[len("/file/"):]))
        elif path.startswith("/bookcover/"):
            # vignette de COUVERTURE d'un livre (galerie bibliotheque):
            # la 1re planche de l'ordre de publication, si composee (404
            # sinon -> la SPA montre un placeholder)
            name = os.path.basename(path[len("/bookcover/"):])
            if name.endswith(".jpg"):
                name = name[:-4]
            d = os.path.join(BOOKS_ROOT, name)
            t = None
            try:
                if os.path.isfile(cz_comic.project_json_path(d)):
                    proj = cz_comic.load_project(d)
                    order = cz_comic.book_order(proj)
                    if order:
                        ch0, pg0 = order[0]
                        t = c2c_state.page_thumb(d, ch0["id"], pg0["id"])
            except Exception:
                t = None
            self._send_file(t)
        elif path.startswith("/thumb/"):
            parts = path[len("/thumb/"):].strip("/").split("/")
            if len(parts) != 2:
                self._send_json({"ok": False, "error": "bad thumb path"}, 404)
                return
            pid = parts[1][:-4] if parts[1].endswith(".jpg") else parts[1]
            try:
                t = c2c_state.page_thumb(self.studio.dir, parts[0], pid)
            except Exception:
                t = None
            # cache=False: the thumbnail changes when the page is recomposed
            self._send_file(t)
        else:
            self._send_json({"ok": False, "error": "not found"}, 404)

    def do_POST(self):
        path = self.path.split("?", 1)[0]
        if not path.startswith("/api/"):
            self._send_json({"ok": False, "error": "not found"}, 404)
            return
        op = path[len("/api/"):].strip("/")
        try:
            n = int(self.headers.get("Content-Length") or 0)
            data = json.loads(self.rfile.read(n).decode("utf-8")) if n else {}
        except Exception as e:
            self._send_json({"ok": False, "error": f"bad JSON body: {e}"}, 400)
            return
        # Ops de niveau serveur (selecteur de livres) - dispo meme sans livre
        if op == "ping":
            self._send_json({"ok": True, "mtime": _CODE_MTIME})
            return
        if op in ("books", "open_book", "new_book", "delete_book"):
            self._send_json(books_op(op, data))
            return
        if self.studio is None:
            self._send_json({"ok": False, "no_book": True,
                             "error": "no book open - create or open one "
                                      "with the 📚 menu"})
            return
        self._send_json(self.studio.dispatch(op, data))

    def log_message(self, fmt, *args):          # compact logs
        sys.stderr.write("[c2c] %s\n" % (fmt % args))


def already_running(port, timeout=1.5):
    """Un comics2crispz repond-il deja sur ce port ? http.server accepte le
    DOUBLE bind en silence (SO_REUSEADDR): deux serveurs lances par megarde
    se partagent le port et l'un des deux sert du VIEUX code - symptome
    classique: 'unknown op' apres une mise a jour. On verifie AVANT de
    binder, et on reutilise l'existant."""
    import urllib.request
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/",
                                    timeout=timeout) as r:
            return b"comics2crispz" in r.read(4096)
    except Exception:
        return False


def serve(project_dir, port=DEFAULT_PORT, config=None):
    """project_dir=None -> demarre SANS livre: l'UI montre l'ecran d'accueil
    (creer / ouvrir un livre), pour que le premier lancement d'un utilisateur
    non-dev ne finisse jamais sur une erreur de terminal."""
    Handler.studio = (Studio(project_dir, config or load_config())
                      if project_dir else None)
    srv = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    print(f"[c2c] serving {Handler.studio.dir if Handler.studio else '(no book yet)'}")
    print(f"[c2c] open   http://127.0.0.1:{port}/")
    return srv


def find_latest_book():
    """Newest books/*/project.json - the default project when start.bat /
    start.sh are run without an argument ('open my current book')."""
    best, best_m = None, -1.0
    if os.path.isdir(BOOKS_ROOT):
        for name in os.listdir(BOOKS_ROOT):
            pj = os.path.join(BOOKS_ROOT, name, "project.json")
            if os.path.isfile(pj) and os.path.getmtime(pj) > best_m:
                best, best_m = os.path.join(BOOKS_ROOT, name), \
                    os.path.getmtime(pj)
    return best


# ----------------------------------------------------------------------------
# Selecteur de livres (ops SERVEUR, pas Studio: elles changent le livre ouvert)
# ----------------------------------------------------------------------------
def list_books():
    """Les livres de books/ (titre lisible, nb de planches) + lequel est
    ouvert. C'est la source du menu 📚 de l'UI."""
    out = []
    cur = os.path.abspath(Handler.studio.dir) if Handler.studio else ""
    if os.path.isdir(BOOKS_ROOT):
        for name in sorted(os.listdir(BOOKS_ROOT)):
            pj = os.path.join(BOOKS_ROOT, name, "project.json")
            if not os.path.isfile(pj):
                continue
            title, pages = name, 0
            try:
                with open(pj, "r", encoding="utf-8") as f:
                    data = json.load(f)
                title = data.get("name") or name
                pages = sum(len(c.get("pages") or [])
                            for c in data.get("chapters") or [])
            except Exception:
                pass
            out.append({"book": name, "title": title, "pages": pages,
                        "mtime": int(os.path.getmtime(pj)),
                        "current": os.path.abspath(
                            os.path.join(BOOKS_ROOT, name)) == cur})
    return {"ok": True, "books": out,
            "open": bool(Handler.studio)}


def books_op(op, data):
    """Ops de niveau serveur: books / open_book {book} / new_book {name}.
    Toujours {ok: ...}; apres open/new, le livre devient le livre OUVERT et
    la reponse contient son index (l'UI bascule sans recharger)."""
    try:
        warnings = []
        with _LOCK:
            if op == "books":
                return list_books()
            cfg = Handler.studio.config if Handler.studio else load_config()
            if op == "open_book":
                name = os.path.basename(str(data.get("book") or "").strip())
                if not name:
                    raise ValueError("pick a book in the list")
                d = os.path.join(BOOKS_ROOT, name)
                if not os.path.isfile(cz_comic.project_json_path(d)):
                    raise ValueError(f"'{name}' has no project.json (was the "
                                     f"folder moved?)")
                studio = Studio(d, cfg)
            elif op == "delete_book":
                # CORBEILLE, jamais de suppression definitive: le dossier
                # part dans books/_trash/<nom[-n]> (le remettre a la racine
                # de books/ le restaure). _trash n'a pas de project.json,
                # le selecteur ne le liste donc jamais.
                name = os.path.basename(str(data.get("book") or "").strip())
                d = os.path.join(BOOKS_ROOT, name)
                if not name or not os.path.isfile(
                        cz_comic.project_json_path(d)):
                    raise ValueError(f"'{name}' is not a book in books/")
                trash = os.path.join(BOOKS_ROOT, "_trash")
                os.makedirs(trash, exist_ok=True)
                dst, n = os.path.join(trash, name), 1
                while os.path.exists(dst):
                    n += 1
                    dst = os.path.join(trash, f"{name}-{n}")
                if Handler.studio and \
                        os.path.abspath(Handler.studio.dir) == os.path.abspath(d):
                    Handler.studio = None
                shutil.move(d, dst)
                warnings.append(f"book '{name}' moved to books/_trash/"
                                f"{os.path.basename(dst)} - move the folder "
                                f"back into books/ to restore it")
                nxt = find_latest_book()
                if nxt and Handler.studio is None:
                    Handler.studio = Studio(nxt, cfg)
                studio = Handler.studio
                if studio is None:
                    return {"ok": True, "no_book": True, "deleted": name,
                            "warnings": warnings}
            elif op == "new_book":
                title = str(data.get("name") or "").strip()
                slug = re.sub(r"[^A-Za-z0-9_-]+", "-", title.lower()).strip("-")
                if not slug:
                    raise ValueError("give the new book a name")
                d = os.path.join(BOOKS_ROOT, slug)
                if os.path.isfile(cz_comic.project_json_path(d)):
                    raise ValueError(f"a book named '{slug}' already exists - "
                                     f"pick it in the list or choose another "
                                     f"name")
                fmt = str(data.get("page") or "Web").strip()
                if fmt not in cz_comic.PAGE_PRESETS:
                    return {"ok": False,
                            "error": f"unknown book format '{fmt}' (known: "
                                     f"{', '.join(cz_comic.PAGE_PRESETS)})"}
                p = cz_comic.new_project(title, page=fmt)
                p["page"]["page_numbers"] = True
                if data.get("manga"):
                    p["page"]["reading"] = "rtl"   # sens manga (droite→gauche)
                pages = max(0, min(int(data.get("pages") or 0), 100))
                # Story Bible (amorce): concept/pitch + langue des dialogues.
                # Ne part JAMAIS dans un prompt image - nourrit le mode fun
                # et les aides Ollama.
                concept = str(data.get("concept") or "").strip()
                language = str(data.get("language") or "").strip()
                if concept or language:
                    p["bible"] = {"story": {"concept": concept,
                                            "language": language}}
                if data.get("fun"):
                    # ✨ mode fun: Ollama invente tout depuis le titre. En cas
                    # d'echec -> structure variee + warning, jamais de livre
                    # casse ni d'erreur seche au premier lancement.
                    lay_counts = {n: len(cz_comic.layout_cells(n))
                                  for n in cz_comic.layout_names()}
                    fb = c2c_ollama.fun_book(title, pages or 6, lay_counts,
                                             cfg.get("ollama"),
                                             concept=concept,
                                             language=language)
                    if fb.get("ok"):
                        cov = cz_comic.add_chapter(p, "Cover")
                        pg = cz_comic.add_page(p, cov["id"], "splash",
                                               role="cover")
                        cz_comic.add_dialogue(pg["panels"][0], title.upper(),
                                              kind="sfx")
                        pg["panels"][0]["text"] = (
                            f"dramatic comic book cover for a story titled "
                            f"'{title}'")
                        _ch, ol_warnings = c2c_state.build_from_outline(
                            p, fb["outline"])
                        warnings.extend(ol_warnings)
                        got = len(fb["outline"].get("pages") or [])
                        want = pages or 6
                        if got < want:
                            warnings.append(
                                f"you asked for {want} story pages, the AI "
                                f"wrote {got} - add pages from the flatplan "
                                f"(➕ Add pages) or run the fun mode again")
                        back = cz_comic.add_page(p, _ch["id"], "splash",
                                                 role="back")
                        back["panels"][0]["text"] = \
                            "minimalist comic back cover"
                        warnings.insert(0, f"✨ invented by {fb['model']} - "
                                           f"everything is editable")
                    else:
                        warnings.append(f"fun mode unavailable "
                                        f"({fb.get('error')}) - built a "
                                        f"varied structure instead")
                        data = dict(data, fun=False)
                if not data.get("fun"):
                    ch = cz_comic.add_chapter(p, "Chapter 1")
                    cz_comic.add_page(p, ch["id"], "splash", role="cover")
                    # structure de depart optionnelle: N planches variees +
                    # dos - un livre pret a remplir case par case
                    if pages:
                        cycle = Studio.LAYOUT_CYCLE
                        for i in range(pages):
                            cz_comic.add_page(p, ch["id"],
                                              cycle[i % len(cycle)])
                        cz_comic.add_page(p, ch["id"], "splash", role="back")
                    else:
                        cz_comic.add_page(p, ch["id"], "4-grid")
                # Reglages du wizard: moteur/modele voulus par CE livre (le
                # modele est un SOUHAIT verifie a la generation - on ne swap
                # jamais le modele de l'instance, on PREVIENT si ca diverge)
                # + style global (suffixe/negatif/LoRAs/bulle).
                if data.get("engine"):
                    p["engine"] = {"name": str(data["engine"]),
                                   "model": str(data.get("model") or "")}
                loras = [str(x) for x in (data.get("loras") or []) if x]
                if loras:
                    p["style"]["loras"] = loras
                if data.get("style_suffix") is not None:
                    p["style"]["prompt_suffix"] = \
                        str(data.get("style_suffix") or "").strip()
                if data.get("style_negative") is not None:
                    p["style"]["negative"] = \
                        str(data.get("style_negative") or "").strip()
                if data.get("bubble") in cz_comic.BUBBLE_STYLES:
                    p["style"]["bubble"] = data["bubble"]
                cz_comic.save_project(p, d)
                for c, pg in cz_comic.book_order(p):
                    c2c_state.compose_one(p, d, c["id"], pg["id"])
                studio = Studio(d, cfg)
            else:
                raise ValueError(f"unknown op '{op}'")
            Handler.studio = studio
        idx = c2c_state.book_index(studio.load(), studio.dir)
        idx["opened"] = os.path.basename(studio.dir)
        if warnings:
            idx["warnings"] = warnings
        return idx
    except Exception as e:
        return {"ok": False, "error": str(e)}


def main(argv=None):
    parser = argparse.ArgumentParser(
        prog="c2c", description="comics2crispz - comic book studio (local)")
    parser.add_argument("project", nargs="?", default=None,
                        help="folder holding a project.json (default: the "
                             "most recently edited book under books/)")
    parser.add_argument("--port", type=int, default=None,
                        help=f"server port (default: config 'port' or "
                             f"{DEFAULT_PORT})")
    parser.add_argument("--open", action="store_true",
                        help="open the browser once the server is up")
    args = parser.parse_args(argv)
    config = load_config()
    port = args.port or int(config.get("port") or DEFAULT_PORT)
    if already_running(port):
        # Perime ? (le process qui tient le port a charge un c2c_server.py
        # plus vieux que celui sur le disque)
        stale = False
        try:
            import urllib.request
            req = urllib.request.Request(
                f"http://127.0.0.1:{port}/api/ping", data=b"{}",
                headers={"Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=2) as r:
                other = json.loads(r.read().decode("utf-8"))
            stale = float(other.get("mtime") or 0) < _CODE_MTIME - 1
        except Exception:
            stale = True                     # vieux serveur sans /api/ping
        if stale:
            print(f"[c2c] a comics2crispz server runs on port {port} with "
                  f"OLDER code than this folder. Close it (its window / "
                  f"Ctrl+C), or run:")
            print("        powershell -Command \"Get-CimInstance "
                  "Win32_Process -Filter \\\"Name='python.exe'\\\" | "
                  "Where-Object {$_.CommandLine -like '*c2c_server*'} | "
                  "ForEach-Object { Stop-Process -Id $_.ProcessId -Force }\"")
            print("      then start again.")
            return 1
        print(f"[c2c] a comics2crispz server ALREADY runs on port {port} - "
              f"reusing it.")
        if args.open:
            import webbrowser
            webbrowser.open(f"http://127.0.0.1:{port}/")
        return 0
    # Pas de livre ? On sert quand meme: l'UI accueille avec "creer/ouvrir".
    project = args.project or find_latest_book()
    try:
        srv = serve(project, port, config)
    except FileNotFoundError as e:
        print(f"[c2c] {e}", file=sys.stderr)
        return 2
    if args.open:
        import threading
        import webbrowser
        threading.Timer(0.8, webbrowser.open,
                        [f"http://127.0.0.1:{port}/"]).start()
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
