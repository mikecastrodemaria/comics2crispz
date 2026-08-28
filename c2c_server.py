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

HERE = os.path.dirname(os.path.abspath(__file__))
ASSETS = os.path.join(HERE, "assets")
DEFAULT_PORT = 8770

_LOCK = threading.Lock()


def load_config():
    for name in ("config.json", "config-sample.json"):
        p = os.path.join(HERE, name)
        if os.path.isfile(p):
            try:
                with open(p, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception as e:
                print(f"[c2c] {name} unreadable ({e}), ignored",
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
        name, eng = self._default_engine(data.get("engine"))
        if eng is None:
            return {"ok": False,
                    "error": f"no engine '{name}' (config.json 'engines')"}
        done, warnings = [], []
        project = self.load()
        page = cz_comic.find_page(project, cid, pid)
        for i in range(len(page["panels"])):
            panel = page["panels"][i]
            if only and panel["id"] != only:
                continue
            if (panel.get("image") and os.path.isfile(panel["image"])
                    and not force):
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
        self._send_json(self.studio.dispatch(op, data))

    def log_message(self, fmt, *args):          # compact logs
        sys.stderr.write("[c2c] %s\n" % (fmt % args))


def serve(project_dir, port=DEFAULT_PORT, config=None):
    Handler.studio = Studio(project_dir, config or load_config())
    srv = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    print(f"[c2c] serving {Handler.studio.dir}")
    print(f"[c2c] open   http://127.0.0.1:{port}/")
    return srv


def main(argv=None):
    parser = argparse.ArgumentParser(
        prog="c2c", description="comics2crispz - comic book studio (local)")
    parser.add_argument("project", help="folder holding a project.json")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    args = parser.parse_args(argv)
    try:
        srv = serve(args.project, args.port)
    except FileNotFoundError as e:
        print(f"[c2c] {e}", file=sys.stderr)
        return 2
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
