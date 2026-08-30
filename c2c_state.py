"""comics2crispz - paginated state + structure operations (flatplan).

PURE layer on top of cz_comic (vendored from crispz-studio, see README):
no HTTP here, everything is testable without a server or a GPU. Two levels
of state, following the Asset Browser days.json/manifest pattern:

  book_index()    -> the THIN index (navigator + flatplan): chapters, pages
                     (role, layout, folio, progress), a few KB even at
                     100 pages. Reloaded after every operation.
  chapter_state() -> the FULL state of one chapter (rects as page fractions,
                     panels, dialogues, placements) - loaded on demand.

House rules (inherited from the crispz family): nothing is lost silently
(move_page refuses an out-of-range index instead of clamping quietly), page
ids stay STABLE (panel/page paths never move when reordering: order = the
position in the array, the folio is what readers see).
"""

import os
import re
import json

from PIL import Image

import cz_comic

THUMB_DIRNAME = "_studio_thumbs"


# ----------------------------------------------------------------------------
# Page progress
# ----------------------------------------------------------------------------
def page_progress(project_dir, chapter, page):
    """{panels_total, panels_done, composed, locked} for one page.
    'composed' = a page PNG exists AND is newer than the latest panel image
    (otherwise it is stale and the UI must show it)."""
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
    """{(cid, pid): folio} of the 'story' pages in publication order."""
    out, folio = {}, 0
    for ch, pg in cz_comic.book_order(project):
        if pg.get("role", "story") == "story":
            folio += 1
            out[(ch["id"], pg["id"])] = folio
    return out


def book_index(project, project_dir):
    """Thin index of the book: the only thing the navigator and the flatplan
    load. The order of `book` is the PUBLICATION order."""
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
    rtl = str(pg_conf.get("reading") or "ltr").lower() == "rtl"
    pos = {id(p): i for i, p in enumerate(book)}
    sp = [[(pos[id(p)] if p is not None else None) for p in row]
          for row in spreads(book, rtl=rtl)]
    return {"ok": True, "name": project.get("name") or "Untitled",
            "engine": project.get("engine") or None,
            "reading": "rtl" if rtl else "ltr",
            "page": {"width": pg_conf["width"], "height": pg_conf["height"]},
            "chapters": chapters, "book": book, "spreads": sp,
            # cellules des gabarits en fractions: la SPA dessine les
            # mini-apercus cliquables du selecteur de layout avec ca
            "layouts": {name: [list(c) for c in cz_comic.layout_cells(name)]
                        for name in cz_comic.layout_names()}}


def spreads(book, rtl=False):
    """Facing-page spreads from the index: the cover (and any 'cover'/'back'
    page) stands ALONE, the body goes in [verso, recto] pairs - the recto
    (right-hand page) carries the beat. A 'title' page consumes its slot in
    the parity (that is the point: SEE where right-hand pages land).

    rtl=True (manga - project['page']['reading'] = 'rtl'): the book reads
    right to left. Same pairing, but the order INSIDE each spread is
    mirrored: the first-read page is the right-hand side of the book."""
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
    if rtl:
        out = [list(reversed(row)) for row in out]
    return out


def _pair(pages):
    """Body of the book: the first story page is a RECTO (right-hand page,
    like in a bound album), then verso/recto pairs."""
    out = []
    if pages:
        out.append([None, pages[0]])
        rest = pages[1:]
        for i in range(0, len(rest), 2):
            out.append([rest[i], rest[i + 1] if i + 1 < len(rest) else None])
    return out


# ----------------------------------------------------------------------------
# Full state of one chapter (page view / overlays)
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
    """Full state of ONE chapter's pages (page-fraction rects for the
    clickable overlays, dialogues, sidecarred placements) - loaded on
    demand."""
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
                "dialogue_text": fmt_dialogue(pn.get("dialogue")),
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
# Reordering (the flatplan drag)
# ----------------------------------------------------------------------------
def move_page(project, cid, pid, to_cid, to_index):
    """Move a page to `to_index`: 0-based position in the target chapter's
    pages array AFTER the moved page has been removed (the natural index of
    a drop: the client counts the remaining cards); past the upper bound =
    append. Same chapter = reorder. The page id NEVER changes (panel/page
    paths are keyed by ids); an id collision in the target chapter is an
    explicit error - nothing is renamed silently."""
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
# Dialogue: writer syntax <-> structured lines (lossless round-trip)
# ----------------------------------------------------------------------------
def fmt_dialogue(dlg):
    """Structured lines -> writer syntax (the inverse of parse_dialogue),
    keeping the style modifiers ('Rook (angular): ...')."""
    lines = []
    for x in dlg or []:
        k, t, s = x.get("kind", "speech"), x.get("text", ""), x.get("speaker", "")
        if k == "caption":
            lines.append(f"CAP: {t}")
        elif k == "sfx":
            lines.append(f"SFX: {t}")
        else:
            mods = []
            if k == "thought":
                mods.append("think")
            if x.get("style"):
                mods.append(x["style"])
            lines.append(f"{s} ({', '.join(mods)}): {t}" if mods else f"{s}: {t}")
    return "\n".join(lines)


def merge_dialogue(old, new):
    """parse_dialogue starts over from TEXT: balloon positions placed by
    dragging (anchor/pos) would be lost on every save. Re-attach them to
    unchanged lines: same (kind, speaker, text) first, else the first free
    line of the same (kind, speaker)."""
    used = set()
    for nd in new:
        best = None
        for j, od in enumerate(old or []):
            if j in used:
                continue
            if (od.get("kind") == nd.get("kind")
                    and (od.get("speaker") or "") == (nd.get("speaker") or "")):
                if (od.get("text") or "") == (nd.get("text") or ""):
                    best = j
                    break
                if best is None:
                    best = j
        if best is not None:
            used.add(best)
            for k in ("anchor", "pos"):
                if k in old[best] and k not in nd:
                    nd[k] = old[best][k]
    return new


# ----------------------------------------------------------------------------
# Fun mode: build a book from an LLM outline (repairs are NEVER silent)
# ----------------------------------------------------------------------------
_NAME_OK = re.compile(r"[^A-Za-z0-9_\-]+")


def _closest_layout(n_panels):
    """Layout whose panel count is closest to n_panels (ties: fewer panels)."""
    names = cz_comic.layout_names()
    return min(names, key=lambda x: (abs(len(cz_comic.layout_cells(x))
                                         - n_panels),
                                     len(cz_comic.layout_cells(x))))


def build_from_outline(project, outline, chapter_name="Story"):
    """Fills `project` from an LLM outline (✨ fun mode): casting, one
    chapter, story pages with panel texts and dialogues. EVERY repair is
    reported in the returned warnings list - an invalid layout is swapped
    for the closest one, extra panels are dropped WITH their text quoted,
    invalid dialogue kinds fall back to speech. Nothing disappears
    silently, the family rule."""
    warnings = []
    for c in (outline.get("casting") or [])[:8]:
        raw = str(c.get("name") or "").strip()
        name = _NAME_OK.sub("", raw)
        if not name:
            warnings.append("casting entry without a usable name - skipped")
            continue
        kind = c.get("kind") if c.get("kind") in ("character", "setting") \
            else "character"
        if c.get("kind") not in ("character", "setting", None):
            warnings.append(f"casting '{name}': unknown kind "
                            f"'{c.get('kind')}' - treated as character")
        project.setdefault("casting", {})[name] = cz_comic.new_character(
            str(c.get("desc") or "").strip(), kind=kind)
    ch = cz_comic.add_chapter(project, chapter_name,
                              str(outline.get("synopsis") or "").strip())
    for i, pd in enumerate(outline.get("pages") or []):
        if not isinstance(pd, dict):
            warnings.append(f"page {i + 1}: not an object - skipped")
            continue
        panels_data = [p for p in (pd.get("panels") or [])
                       if isinstance(p, dict)]
        layout = pd.get("layout")
        try:
            n_cells = len(cz_comic.layout_cells(layout))
        except (ValueError, TypeError):
            layout = _closest_layout(max(1, len(panels_data)))
            n_cells = len(cz_comic.layout_cells(layout))
            warnings.append(f"page {i + 1}: unknown layout "
                            f"'{pd.get('layout')}' - using {layout}")
        page = cz_comic.add_page(project, ch["id"], layout)
        if len(panels_data) > n_cells:
            lost = "; ".join((str(x.get("text") or "(empty)")[:60])
                             for x in panels_data[n_cells:])
            warnings.append(f"page {i + 1} ({page['id']}): "
                            f"{len(panels_data) - n_cells} panel(s) beyond "
                            f"the {layout} grid dropped - their text: {lost}")
        for j, pnd in enumerate(panels_data[:n_cells]):
            panel = page["panels"][j]
            panel["text"] = str(pnd.get("text") or "").strip()
            for d in (pnd.get("dialogue") or [])[:6]:
                if not isinstance(d, dict):
                    continue
                text = str(d.get("text") or "").strip()
                if not text:
                    continue
                kind = d.get("kind") \
                    if d.get("kind") in cz_comic.DIALOGUE_KINDS else "speech"
                if d.get("kind") not in cz_comic.DIALOGUE_KINDS:
                    warnings.append(f"{page['id']}.{panel['id']}: unknown "
                                    f"dialogue kind '{d.get('kind')}' - "
                                    f"treated as speech")
                cz_comic.add_dialogue(panel, text,
                                      speaker=str(d.get("speaker") or ""),
                                      kind=kind)
    return ch, warnings


# ----------------------------------------------------------------------------
# Page composition (+ placements sidecar, like Comic Studio)
# ----------------------------------------------------------------------------
def compose_one(project, project_dir, cid, pid, face_detector=None,
                char_embeddings=None):
    """Compose ONE page + lettering + folio (same rules as compose_book:
    only 'story' pages get a folio, when page_numbers is on), save the PNG
    and the placements sidecar (page fractions + per-panel dialogue index).
    Returns the placements."""
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
    # Ecriture ATOMIQUE (tmp + replace): la SPA recharge ce PNG pendant qu'on
    # recompose - un save() direct tronque le fichier sous le serveur HTTP et
    # le navigateur affiche une planche noire/cassee (lecon Asset Browser).
    tmp = dst + f".{os.getpid()}.tmp"
    try:
        sheet.save(tmp, "PNG")
        os.replace(tmp, dst)
    finally:
        if os.path.exists(tmp):
            try:
                os.remove(tmp)
            except OSError:
                pass
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
# Flatplan thumbnails (cached, Asset Browser pattern)
# ----------------------------------------------------------------------------
def page_thumb(project_dir, cid, pid, size=360):
    """Path of the composed page's cached JPEG thumbnail (regenerated when
    the PNG is newer). None when the page is not composed. Atomic write
    (tmp + replace): the SPA serves it while it is being regenerated."""
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
