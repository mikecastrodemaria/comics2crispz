"""comics2crispz - serveur local (stdlib) : SPA + API JSON d'un projet BD.

    python c2c_server.py <dossier_projet> [--port 8770]

Zero dependance serveur (http.server) ; Pillow seulement pour les vignettes et
la composition. Le projet reste UN project.json compatible famille crispz
(cz_comic vendore) : l'accordeon de crispz-studio, son Comic Studio et le CLI
peuvent travailler sur le meme dossier - tout est stateless, chaque operation
relit/reecrit le fichier.

Routes :
  GET  /                    -> assets/studio.html
  GET  /assets/<f>          -> fichiers de la SPA
  GET  /file/<rel>          -> fichiers du PROJET (pages/, panels/, refs/)
  GET  /thumb/<cid>/<pid>   -> vignette JPEG cachee de la planche composee
  POST /api/<op>            -> JSON in / JSON out :
       index                  l'index maigre (navigateur + chemin de fer)
       chapter {cid}          l'etat complet d'un chapitre
       move_page {cid,pid,to_cid,to_index}   drag du chemin de fer
       set_role {cid,pid,role}
       compose {cid,pid} | compose_book {}
       engines {}             caps des moteurs configures (protocole CLI)

Toute reponse API est {ok: true, ...} ou {ok: false, error} - la SPA n'a
qu'un chemin d'erreur. Un lock process serialise les ecritures project.json
(deux drags rapides ne se perdent pas).
"""

import os
import sys
import json
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
    """Etat du serveur : le dossier du projet + les moteurs. Le projet
    lui-meme n'est PAS garde en memoire (stateless, comme la famille)."""

    def __init__(self, project_dir, config):
        self.dir = os.path.abspath(project_dir)
        self.config = config
        self.engines = c2c_engines.load_engines(config)
        if not os.path.isfile(cz_comic.project_json_path(self.dir)):
            raise FileNotFoundError(f"no project.json in {self.dir}")

    def load(self):
        return cz_comic.load_project(self.dir)

    # ---- ops API (toutes renvoient un dict pret a serialiser) ----
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
            c2c_state.compose_one(project, self.dir, data["cid"], data["pid"])
        return c2c_state.book_index(project, self.dir)

    def op_compose_book(self, _data):
        with _LOCK:
            project = self.load()
            for ch, pg in cz_comic.book_order(project):
                c2c_state.compose_one(project, self.dir, ch["id"], pg["id"])
        return c2c_state.book_index(project, self.dir)

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
    """Chemin fichier sous `root`, ou None si la requete tente d'en sortir."""
    rel = posixpath.normpath(rel.lstrip("/"))
    if rel.startswith("..") or os.path.isabs(rel):
        return None
    p = os.path.abspath(os.path.join(root, *rel.split("/")))
    return p if p.startswith(os.path.abspath(root)) else None


class Handler(BaseHTTPRequestHandler):
    studio = None                      # renseigne par serve()

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
            # cache=False: la vignette change quand la planche est recomposee
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

    def log_message(self, fmt, *args):          # logs compacts
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
