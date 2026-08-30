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
import shutil
import argparse
import threading
import mimetypes
import posixpath
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import cz_comic
import c2c_state
import c2c_engines
import c2c_ollama

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
    LAYOUT_CYCLE = ("3-classic", "4-grid", "2-up", "5-hero", "3-strip",
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
            for key in data.get("clear") or []:
                if key in ("pos", "anchor"):
                    dlg[idx].pop(key, None)
            cz_comic.save_project(project, self.dir)
            fd, emb = self._letter_kit(project)
            c2c_state.compose_one(project, self.dir, data["cid"], data["pid"],
                                  face_detector=fd, char_embeddings=emb)
        return c2c_state.book_index(project, self.dir)

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
            # garde-fou sur le TEXTE de la case, pas le prompt resolu: une
            # case vide + un suffixe de style global donnerait un prompt
            # 'style seulement' qui partirait au moteur pour rien
            if not (panel.get("text") or "").strip():
                warnings.append(f"{panel['id']}: empty panel text - skipped "
                                f"(write the panel description first)")
                continue
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
            res = eng.gen({"prompt": spec["prompt"],
                           "negative": spec["negative"],
                           "width": spec["width"], "height": spec["height"],
                           "seed": spec["seed"], "loras": spec["loras"],
                           "refs": refs})
            if not res.get("ok"):
                return {"ok": False, "engine": name, "generated": done,
                        "error": f"{panel['id']}: {res.get('error')}"}
            src = (res.get("images") or [None])[0]
            if not src or not os.path.isfile(src):
                return {"ok": False, "engine": name, "generated": done,
                        "error": f"{panel['id']}: engine returned no "
                                 f"readable image ({src})"}
            dst = cz_comic.panel_path(self.dir, cid, pid, panel["id"])
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            shutil.copy(src, dst)
            with _LOCK:                     # re-read: the user may have edited
                project = self.load()
                page = cz_comic.find_page(project, cid, pid)
                pn = cz_comic.find_panel(project, cid, pid, panel["id"])
                pn["image"] = dst
                pn["status"] = "rendered"
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
        return {"ok": True,
                "engines": {name: e.caps()
                            for name, e in self.engines.items()},
                "default": self.config.get("engine")}

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
                p = cz_comic.new_project(title, page="Web")
                p["page"]["page_numbers"] = True
                if data.get("manga"):
                    p["page"]["reading"] = "rtl"   # sens manga (droite→gauche)
                pages = max(0, min(int(data.get("pages") or 0), 100))
                if data.get("fun"):
                    # ✨ mode fun: Ollama invente tout depuis le titre. En cas
                    # d'echec -> structure variee + warning, jamais de livre
                    # casse ni d'erreur seche au premier lancement.
                    lay_counts = {n: len(cz_comic.layout_cells(n))
                                  for n in cz_comic.layout_names()}
                    fb = c2c_ollama.fun_book(title, pages or 6, lay_counts,
                                             cfg.get("ollama"))
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
