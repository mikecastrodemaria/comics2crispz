"""comics2crispz - etat pagine + operations de structure (chemin de fer).

Couche PURE au-dessus de cz_comic (vendore de crispz-studio, voir README) :
aucun HTTP ici, tout est testable sans serveur ni GPU. Deux niveaux d'etat,
pattern days.json/manifests de l'Asset Browser :

  book_index()    -> l'INDEX maigre (navigateur + chemin de fer) : chapitres,
                     pages (role, layout, folio, avancement), quelques Ko meme
                     a 100 pages. C'est lui qui est recharge apres chaque op.
  chapter_state() -> l'etat COMPLET d'un chapitre (rects en fractions,
                     panneaux, dialogues, placements) - charge a la demande.

Regles maison (heritees de la famille crispz) : rien n'est perdu en silence
(move_page refuse un index hors bornes au lieu de clamper sans le dire), les
ids de pages restent STABLES (les chemins panels/pages ne bougent pas quand on
reordonne : l'ordre = la position dans le tableau, le folio fait foi a
l'affichage).
"""

import os
import json

from PIL import Image

import cz_comic

THUMB_DIRNAME = "_studio_thumbs"


# ----------------------------------------------------------------------------
# Avancement d'une planche
# ----------------------------------------------------------------------------
def page_progress(project_dir, chapter, page):
    """{panels_total, panels_done, composed, locked} d'une planche.
    'composed' = un PNG de planche existe ET est plus recent que la derniere
    image de case (sinon il est perime et l'UI doit le montrer)."""
    total, done, latest, locked = len(page["panels"]), 0, 0.0, 0
    for pn in page["panels"]:
        p = pn.get("image")
        if p and os.path.isfile(p):
            done += 1
            latest = max(latest, os.path.getmtime(p))
        if pn.get("status") == "locked":
            locked += 1
    pp = cz_comic.page_path(project_dir, chapter["id"], page["id"])
    composed = os.path.isfile(pp) and (done == 0
                                       or os.path.getmtime(pp) >= latest)
    return {"panels_total": total, "panels_done": done,
            "composed": bool(composed), "locked": locked == total and total > 0}


def _folios(project):
    """{(cid, pid): folio} des planches 'story' dans l'ordre de publication."""
    out, folio = {}, 0
    for ch, pg in cz_comic.book_order(project):
        if pg.get("role", "story") == "story":
            folio += 1
            out[(ch["id"], pg["id"])] = folio
    return out


def book_index(project, project_dir):
    """Index maigre du livre : la seule chose que le navigateur et le chemin
    de fer chargent. L'ordre de `book` est l'ORDRE DE PUBLICATION."""
    folios = _folios(project)
    chapters = [{"id": ch["id"], "name": ch.get("name") or ch["id"],
                 "pages": [pg["id"] for pg in ch["pages"]]}
                for ch in project["chapters"]]
    book = []
    for ch, pg in cz_comic.book_order(project):
        prog = page_progress(project_dir, ch, pg)
        pp = cz_comic.page_path(project_dir, ch["id"], pg["id"])
        book.append({"cid": ch["id"], "pid": pg["id"],
                     "chapter": ch.get("name") or ch["id"],
                     "role": pg.get("role", "story"),
                     "layout": pg["layout"],
                     "folio": folios.get((ch["id"], pg["id"])),
                     "mtime": (int(os.path.getmtime(pp))
                               if os.path.isfile(pp) else 0),
                     **prog})
    pg_conf = cz_comic.page_size(project.get("page"))
    pos = {id(p): i for i, p in enumerate(book)}
    sp = [[(pos[id(p)] if p is not None else None) for p in row]
          for row in spreads(book)]
    return {"ok": True, "name": project.get("name") or "Untitled",
            "page": {"width": pg_conf["width"], "height": pg_conf["height"]},
            "chapters": chapters, "book": book, "spreads": sp}


def spreads(book):
    """Doubles pages en regard depuis l'index : la couverture (et toute page
    'cover'/'back') est SEULE, le corps va par paires [verso, recto] - le
    recto (page de droite) porte la chute. Une page 'title' consomme sa place
    dans la parite (c'est le but : voir ou tombent les pages droites)."""
    out, body = [], []
    for p in book:
        if p["role"] in ("cover", "back"):
            if body:
                out.extend(_pair(body))
                body = []
            out.append([p])
        else:
            body.append(p)
    if body:
        out.extend(_pair(body))
    return out


def _pair(pages):
    """Corps du livre : la premiere page d'histoire est un RECTO (page de
    droite, comme dans un album relie), puis verso/recto."""
    out = []
    if pages:
        out.append([None, pages[0]])
        rest = pages[1:]
        for i in range(0, len(rest), 2):
            out.append([rest[i], rest[i + 1] if i + 1 < len(rest) else None])
    return out


# ----------------------------------------------------------------------------
# Etat complet d'un chapitre (vue planche / overlays)
# ----------------------------------------------------------------------------
def _placements(project_dir, cid, pid):
    sp = cz_comic.page_path(project_dir, cid, pid, ext="placements.json")
    if os.path.isfile(sp):
        try:
            with open(sp, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return None
    return None


def _rel(path, project_dir):
    try:
        rel = os.path.relpath(path, project_dir)
    except ValueError:
        return None
    return None if rel.startswith("..") else rel.replace("\\", "/")


def chapter_state(project, project_dir, cid):
    """Etat complet des planches d'UN chapitre (fractions de page pour les
    overlays, dialogues, placements sidecarres) - charge a la demande."""
    ch = cz_comic.find_chapter(project, cid)
    pg_conf = cz_comic.page_size(project.get("page"))
    W, H = float(pg_conf["width"]), float(pg_conf["height"])
    folios = _folios(project)
    pages = []
    for page in ch["pages"]:
        cells = cz_comic.layout_cells(page["layout"])
        rects = cz_comic.panel_rects(cells, pg_conf["width"], pg_conf["height"],
                                     pg_conf["margin"], pg_conf["gutter"])
        pp = cz_comic.page_path(project_dir, cid, page["id"])
        panels = []
        for i, pn in enumerate(page["panels"]):
            img = pn.get("image")
            panels.append({
                "id": pn["id"], "text": pn.get("text") or "",
                "seed": int(pn.get("seed", -1)),
                "status": pn.get("status", "draft"),
                "img": (_rel(img, project_dir)
                        if img and os.path.isfile(img) else None),
                "dialogue": pn.get("dialogue") or [],
                "rect": ([rects[i][0] / W, rects[i][1] / H,
                          rects[i][2] / W, rects[i][3] / H]
                         if i < len(rects) else None)})
        pages.append({"cid": cid, "pid": page["id"],
                      "role": page.get("role", "story"),
                      "layout": page["layout"],
                      "folio": folios.get((cid, page["id"])),
                      "url": (_rel(pp, project_dir)
                              if os.path.isfile(pp) else None),
                      "mtime": (int(os.path.getmtime(pp))
                                if os.path.isfile(pp) else 0),
                      "panels": panels,
                      "placements": _placements(project_dir, cid, page["id"])})
    return {"ok": True, "cid": cid, "name": ch.get("name") or cid,
            "pages": pages}


# ----------------------------------------------------------------------------
# Reordonnancement (le drag du chemin de fer)
# ----------------------------------------------------------------------------
def move_page(project, cid, pid, to_cid, to_index):
    """Deplace une planche a `to_index` : position 0-based dans le tableau
    pages du chapitre cible APRES retrait de la page deplacee (c'est l'index
    naturel d'un drop : le client compte les cartes restantes) ; hors borne
    superieure = insertion en fin. Meme chapitre = reordonnancement.
    L'id de la page ne change JAMAIS (les chemins
    panels/pages restent valides) ; collision d'id dans le chapitre cible =
    erreur explicite, on ne renumerote pas en silence."""
    src = cz_comic.find_chapter(project, cid)
    dst = cz_comic.find_chapter(project, to_cid)
    page = cz_comic.find_page(project, cid, pid)
    if cid != to_cid and any(p["id"] == pid for p in dst["pages"]):
        raise ValueError(
            f"page id '{pid}' already exists in chapter '{to_cid}' - move "
            f"refused (panel/page paths are keyed by ids; nothing is renamed "
            f"silently)")
    try:
        to_index = int(to_index)
    except (TypeError, ValueError):
        raise ValueError(f"invalid target index {to_index!r}")
    if to_index < 0:
        raise ValueError(f"invalid target index {to_index}")
    src["pages"].remove(page)
    to_index = min(to_index, len(dst["pages"]))
    dst["pages"].insert(to_index, page)
    return page


def set_role(project, cid, pid, role):
    return cz_comic.set_page_role(project, cid, pid, role)


# ----------------------------------------------------------------------------
# Composition d'une planche (+ sidecar de placements, comme Comic Studio)
# ----------------------------------------------------------------------------
def compose_one(project, project_dir, cid, pid, face_detector=None,
                char_embeddings=None):
    """Compose UNE planche + lettrage + folio (memes regles que compose_book :
    seules les planches 'story' sont foliotees, si page_numbers est actif),
    sauve le PNG et le sidecar de placements (fractions de page + index de
    replique par panneau). Renvoie les placements."""
    page = cz_comic.find_page(project, cid, pid)
    pg = cz_comic.page_size(project.get("page"))
    sheet = cz_comic.compose_page(project, page)
    raw = cz_comic.render_lettering(project, page, sheet,
                                    face_detector=face_detector,
                                    char_embeddings=char_embeddings)
    folio = _folios(project).get((cid, pid))
    if folio and bool(pg.get("page_numbers", False)):
        cz_comic._draw_page_number(sheet, pg, folio)
    dst = cz_comic.page_path(project_dir, cid, pid)
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    sheet.save(dst)
    W, H = float(pg["width"]), float(pg["height"])
    counters, placements = {}, []
    for pl in raw:
        pnid = pl["panel"]
        idx = counters.get(pnid, 0)
        counters[pnid] = idx + 1
        x, y, w, h = pl["rect"]
        placements.append({
            "panel": pnid, "kind": pl["kind"], "index": idx,
            "rect": [x / W, y / H, w / W, h / H],
            "tip": ([pl["tip"][0] / W, pl["tip"][1] / H]
                    if pl.get("tip") else None),
            "clean": bool(pl.get("clean", True))})
    with open(cz_comic.page_path(project_dir, cid, pid,
                                 ext="placements.json"),
              "w", encoding="utf-8") as f:
        json.dump(placements, f, ensure_ascii=False)
    return placements


# ----------------------------------------------------------------------------
# Vignettes du chemin de fer (cache, pattern Asset Browser)
# ----------------------------------------------------------------------------
def page_thumb(project_dir, cid, pid, size=360):
    """Chemin de la vignette JPEG de la planche composee (regeneree si le PNG
    est plus recent). None si la planche n'est pas composee. Ecriture atomique
    (tmp + replace) : la SPA la sert pendant qu'on la regenere."""
    src = cz_comic.page_path(project_dir, cid, pid)
    if not os.path.isfile(src):
        return None
    tdir = os.path.join(project_dir, THUMB_DIRNAME, cid)
    dst = os.path.join(tdir, f"{pid}.jpg")
    if os.path.isfile(dst) and os.path.getmtime(dst) >= os.path.getmtime(src):
        return dst
    os.makedirs(tdir, exist_ok=True)
    tmp = dst + f".{os.getpid()}.tmp"
    try:
        with Image.open(src) as im:
            im = im.convert("RGB")
            im.thumbnail((size, size * 2), Image.LANCZOS)
            im.save(tmp, "JPEG", quality=82, optimize=True)
        os.replace(tmp, dst)
    finally:
        if os.path.exists(tmp):
            try:
                os.remove(tmp)
            except OSError:
                pass
    return dst
