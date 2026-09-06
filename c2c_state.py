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
import time
import shutil

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
            "layouts": sorted(cz_comic.LAYOUTS),
            "casting": [{"name": n, "kind": (c or {}).get("kind", "character")}
                        for n, c in sorted((project.get("casting") or {}).items(),
                                           key=lambda kv: kv[0].lower())],
            "style_loras": list((project.get("style") or {}).get("loras")
                                or []),
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
                "history": panel_history(img, project_dir) if img else [],
                "has_prev": bool(img and panel_history(img, project_dir)),
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
        extra = []
        if x.get("hidden"):
            extra.append("hidden")
        if x.get("font"):
            extra.append("font=" + str(x["font"]))
        if x.get("outline") not in (None, "", 1, 1.0):
            extra.append("outline=" + ("%g" % float(x["outline"])))
        if k in ("caption", "sfx"):
            head = "CAP" if k == "caption" else "SFX"
            lines.append(f"{head} ({', '.join(extra)}): {t}" if extra else f"{head}: {t}")
        else:
            mods = []
            if k == "thought":
                mods.append("think")
            if x.get("style"):
                mods.append(x["style"])
            mods += extra
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


def name_token(raw):
    """@Name token from a free name: accents transliterated (Le Reve ->
    LeReve, not LeRve), then anything outside letters/digits/_/- dropped."""
    import unicodedata
    txt = unicodedata.normalize("NFKD", str(raw or ""))
    txt = "".join(ch for ch in txt if not unicodedata.combining(ch))
    return _NAME_OK.sub("", txt.strip().lstrip("@"))


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
        name = name_token(raw)
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
    mood = str(outline.get("mood") or "").strip()
    if mood and not (project.get("style") or {}).get("mood"):
        project.setdefault("style", {})["mood"] = mood[:300]
    ch = cz_comic.add_chapter(project, chapter_name,
                              str(outline.get("synopsis") or "").strip())
    cast_names = sorted(project.get("casting") or {}, key=len, reverse=True)
    at_fixes = 0
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
            # Les LLM ecrivent souvent 'Lea' au lieu de '@Lea': sans le @,
            # le casting n'est PAS substitue (pas de desc, refs, LoRA,
            # detailer). On prefixe les noms du casting cites tels quels.
            for cname in cast_names:
                fixed = re.sub(r"(?<![@\w])" + re.escape(cname) + r"\b",
                               "@" + cname, panel["text"])
                if fixed != panel["text"]:
                    panel["text"] = fixed
                    at_fixes += 1
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
    if at_fixes:
        warnings.append(f"{at_fixes} panel text(s): casting names written "
                        f"without @ were fixed (e.g. Lea -> @Lea) so the "
                        f"casting applies")
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


# ---------------------------------------------------------------------------
# Historique par case: chaque Regenerate / Edit / Variation / Inpaint archive
# la version qu'il remplace dans <pnid>.history/ (a cote de <pnid>.png), avec
# une vignette et un index.json (op, note, date). Plafond HISTORY_MAX: la plus
# vieille version part. Regle maison "rien de perdu": un retour a une version
# archive AUSSI la version courante -> on peut toujours revenir dans les deux
# sens.
# ---------------------------------------------------------------------------
HISTORY_MAX = 10
_THUMB_W = 320


def history_dir(src):
    return os.path.splitext(src)[0] + ".history"


def _history_index_path(src):
    return os.path.join(history_dir(src), "index.json")


def _read_history(src):
    p = _history_index_path(src)
    if not os.path.isfile(p):
        return []
    try:
        with open(p, "r", encoding="utf-8") as f:
            entries = json.load(f) or []
    except (OSError, ValueError):
        return []
    hd = history_dir(src)
    return [e for e in entries
            if isinstance(e, dict) and e.get("file")
            and os.path.isfile(os.path.join(hd, e["file"]))]


def _write_history(src, entries):
    hd = history_dir(src)
    os.makedirs(hd, exist_ok=True)
    p = _history_index_path(src)
    tmp = p + f".{os.getpid()}.tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(entries, f, ensure_ascii=False, indent=1)
    os.replace(tmp, p)


def _migrate_prev(src):
    """Ancien mecanisme (<pnid>.prev.png, un seul cran): absorbe dans
    l'historique la premiere fois qu'on y touche."""
    prev = os.path.splitext(src)[0] + ".prev.png"
    if os.path.isfile(prev):
        archive_panel(prev, "previous", "before the last edit (old undo slot)",
                      as_src=src)
        os.remove(prev)


def archive_panel(image_path, op, note="", as_src=None):
    """Copie image_path dans l'historique de la case as_src (defaut: elle-
    meme), vignette comprise; renvoie l'entree creee. Purge au-dela de
    HISTORY_MAX."""
    src = as_src or image_path
    if not os.path.isfile(image_path):
        return None
    hd = history_dir(src)
    os.makedirs(hd, exist_ok=True)
    ts = time.strftime("%Y%m%d-%H%M%S")
    base = f"{ts}_{re.sub(r'[^a-z0-9]+', '-', str(op).lower()) or 'version'}"
    fname, n = base + ".png", 1
    while os.path.exists(os.path.join(hd, fname)):
        n += 1
        fname = f"{base}-{n}.png"
    shutil.copy2(image_path, os.path.join(hd, fname))
    thumb = os.path.splitext(fname)[0] + ".thumb.jpg"
    try:
        with Image.open(image_path) as im:
            im = im.convert("RGB")
            im.thumbnail((_THUMB_W, _THUMB_W))
            im.save(os.path.join(hd, thumb), "JPEG", quality=80)
    except Exception:
        thumb = None
    entry = {"file": fname, "thumb": thumb, "op": str(op),
             "note": str(note or "")[:200], "ts": int(time.time())}
    entries = _read_history(src) + [entry]
    while len(entries) > HISTORY_MAX:
        old = entries.pop(0)
        for f in (old.get("file"), old.get("thumb")):
            if f:
                try:
                    os.remove(os.path.join(hd, f))
                except OSError:
                    pass
    _write_history(src, entries)
    return entry


def replace_panel_image(src, new_image, op, note=""):
    """Remplace l'image de la case par new_image APRES avoir archive la
    version courante (si elle existe). Ecriture atomique."""
    if os.path.isfile(src):
        _migrate_prev(src)
        archive_panel(src, op, note)
    os.makedirs(os.path.dirname(src), exist_ok=True)
    tmp = src + f".{os.getpid()}.tmp"
    shutil.copy2(new_image, tmp)
    os.replace(tmp, src)


def discard_engine_output(path, panel_dir):
    """Sortie brute du moteur ecrite DANS le dossier de la case (out_dir =
    panels/<ch>/<page>/, sous-dossier date): une fois copiee en <pnid>.png
    elle ne sert plus. Supprime le fichier et son dossier date s'il est
    vide. Ailleurs (galerie de l'outil, ou n'importe quoi hors de ce
    dossier): on ne touche a rien."""
    try:
        ap, root = os.path.abspath(path), os.path.abspath(panel_dir)
        if not ap.lower().startswith(root.lower() + os.sep):
            return
        os.remove(ap)
        d = os.path.dirname(ap)
        # sidecars du moteur (<image>.json, <image>.txt...) : memes stem
        stem = os.path.basename(ap)
        for f in os.listdir(d):
            if f.startswith(stem) and f != stem:
                try:
                    os.remove(os.path.join(d, f))
                except OSError:
                    pass
        if not os.listdir(d):
            os.rmdir(d)
    except OSError:
        pass


def restore_panel_version(src, version=None):
    """Remet la version `version` (nom de fichier de l'historique; None = la
    plus recente) comme image courante. La version courante est archivee
    d'abord (op 'replaced'), la version restauree quitte l'historique.
    Renvoie l'entree restauree, ou None si rien a restaurer."""
    had_prev = os.path.isfile(os.path.splitext(src)[0] + ".prev.png")
    _migrate_prev(src)
    if version == "prev.png" and had_prev:
        version = None            # l'ancien cran vient d'etre migre = le dernier
    entries = _read_history(src)
    if not entries:
        return None
    pick = None
    for e in entries:
        if version is None or e["file"] == version:
            pick = e
    if pick is None:
        return None
    hd = history_dir(src)
    vpath = os.path.join(hd, pick["file"])
    if os.path.isfile(src):
        archive_panel(src, "replaced", f"before going back to {pick['op']}")
    tmp = src + f".{os.getpid()}.tmp"
    shutil.copy2(vpath, tmp)
    os.replace(tmp, src)
    entries = [e for e in _read_history(src) if e["file"] != pick["file"]]
    for f in (pick.get("file"), pick.get("thumb")):
        if f:
            try:
                os.remove(os.path.join(hd, f))
            except OSError:
                pass
    _write_history(src, entries)
    return pick


def panel_history(src, project_dir):
    """Historique d'une case pour l'UI, la plus recente d'abord, avec les
    URLs /file/ (chemins relatifs au livre)."""
    if not src:
        return []
    prev = os.path.splitext(src)[0] + ".prev.png"
    hd = history_dir(src)
    out = []
    for e in reversed(_read_history(src)):
        out.append({"file": e["file"], "op": e.get("op"),
                    "note": e.get("note") or "", "ts": e.get("ts"),
                    "url": _rel(os.path.join(hd, e["file"]), project_dir),
                    "thumb": (_rel(os.path.join(hd, e["thumb"]), project_dir)
                              if e.get("thumb") else None)})
    if os.path.isfile(prev):                     # ancien cran non migre
        out.append({"file": "prev.png", "op": "previous",
                    "note": "before the last edit",
                    "ts": int(os.path.getmtime(prev)),
                    "url": _rel(prev, project_dir), "thumb": None})
    return out


# ---------------------------------------------------------------------------
# Bible visuelle (editeur): casting (personnages + decors), style global,
# moods de chapitre - avec, pour chaque fiche, OU elle est utilisee.
# ---------------------------------------------------------------------------
def casting_uses(project):
    """{nom canonique: [{'cid','pid','pnid','has_image'}...]} - les cases
    dont le texte cite @Nom (via resolve_casting: casse insensible)."""
    casting = project.get("casting") or {}
    uses = {n: [] for n in casting}
    for ch in project.get("chapters") or []:
        for pg in ch.get("pages") or []:
            for pn in pg.get("panels") or []:
                used = cz_comic.resolve_casting(pn.get("text") or "",
                                                casting)["used"]
                for u in used:
                    if u in uses:
                        img = pn.get("image")
                        uses[u].append({"cid": ch["id"], "pid": pg["id"],
                                        "pnid": pn["id"],
                                        "has_image": bool(img and
                                                          os.path.isfile(img))})
    return uses


def bible_state(project, project_dir):
    uses = casting_uses(project)
    cast = []
    for name, c in sorted((project.get("casting") or {}).items(),
                          key=lambda kv: kv[0].lower()):
        refs = []
        for r in c.get("refs") or []:
            ap = r if os.path.isabs(r) else os.path.join(
                project_dir, *str(r).replace("\\", "/").split("/"))
            refs.append({"ref": r, "url": _rel(ap, project_dir),
                         "exists": os.path.isfile(ap)})
        cast.append({"name": name, "kind": c.get("kind", "character"),
                     "desc": c.get("desc") or "", "negative": c.get("negative") or "",
                     "loras": list(c.get("loras") or []), "refs": refs,
                     "uses": uses.get(name, [])})
    style = project.get("style") or {}
    return {"ok": True,
            "casting": cast,
            "style": {"prompt_suffix": style.get("prompt_suffix") or "",
                      "negative": style.get("negative") or "",
                      "loras": list(style.get("loras") or []),
                      "mood": style.get("mood") or "",
                      "font": style.get("font") or ""},
            "chapters": [{"id": ch["id"], "name": ch.get("name") or ch["id"],
                          "mood": ch.get("mood") or "",
                          "pages": len(ch.get("pages") or [])}
                         for ch in project.get("chapters") or []]}


# ---------------------------------------------------------------------------
# Production: le tableau de suivi transverse. Chaque compteur est un FILTRE
# (la liste des planches/cases concernees) - jamais une action automatique.
# ---------------------------------------------------------------------------
def style_signature(project, page=None):
    """Empreinte du look courant pour une planche: suffixe de style, negatif,
    LoRA du livre, moteur/modele et mood effectif (chapitre > livre). Stockee
    dans chaque case au rendu (panel['style_sig']) pour signaler ensuite les
    cases dessinees avec un AUTRE style que le style courant (derive)."""
    import hashlib
    st = project.get("style") or {}
    eng = project.get("engine") or {}
    parts = [str(st.get("prompt_suffix") or "").strip(),
             str(st.get("negative") or "").strip(),
             ",".join(sorted(str(x) for x in st.get("loras") or [])),
             str(eng.get("name") or ""), str(eng.get("model") or ""),
             cz_comic.effective_mood(project, page) if page else
             str(st.get("mood") or "").strip()]
    return hashlib.sha1("|".join(parts).encode("utf-8")).hexdigest()[:12]


def production_state(project, project_dir):
    """Compteurs + listes: cases dessinees / manquantes / verrouillees /
    texte vide, @Nom inconnus, planches jamais composees ou a recomposer
    (case plus recente que la planche), bulles sur un visage (clean:false),
    cases rendues avec un autre style que le style courant."""
    casting = project.get("casting") or {}
    tot = {"panels": 0, "drawn": 0, "missing": 0, "locked": 0, "empty": 0,
           "unknown": 0, "pages": 0, "composed": 0, "stale": 0, "never": 0,
           "dirty_balloons": 0, "drift": 0}
    lists = {"missing": [], "locked": [], "empty": [], "unknown": [],
             "never": [], "stale": [], "dirty_balloons": [], "drift": []}
    for ch, pg in cz_comic.book_order(project):
        cid, pid = ch["id"], pg["id"]
        tot["pages"] += 1
        pp = cz_comic.page_path(project_dir, cid, pid)
        page_m = os.path.getmtime(pp) if os.path.isfile(pp) else None
        newest_panel = 0.0
        sig_now = style_signature(project, pg)
        for pn in pg.get("panels") or []:
            tot["panels"] += 1
            img = pn.get("image")
            has = bool(img and os.path.isfile(img))
            ref = {"cid": cid, "pid": pid, "pnid": pn["id"],
                   "text": (pn.get("text") or "")[:80]}
            if has:
                tot["drawn"] += 1
                newest_panel = max(newest_panel, os.path.getmtime(img))
                sig = pn.get("style_sig")
                if sig and sig != sig_now:
                    tot["drift"] += 1
                    lists["drift"].append(ref)
            if pn.get("status") == "locked":
                tot["locked"] += 1
                lists["locked"].append(ref)
            if not (pn.get("text") or "").strip():
                tot["empty"] += 1
                lists["empty"].append(ref)
            elif not has and pn.get("status") != "locked":
                tot["missing"] += 1
                lists["missing"].append(ref)
            unk = cz_comic.resolve_casting(pn.get("text") or "", casting)["unknown"]
            if unk:
                tot["unknown"] += 1
                lists["unknown"].append(dict(ref, names=unk))
        if page_m is None:
            tot["never"] += 1
            lists["never"].append({"cid": cid, "pid": pid})
        else:
            tot["composed"] += 1
            if newest_panel > page_m + 1:
                tot["stale"] += 1
                lists["stale"].append({"cid": cid, "pid": pid})
            dirty = [pl for pl in _placements(project_dir, cid, pid)
                     if pl.get("clean") is False]
            if dirty:
                tot["dirty_balloons"] += len(dirty)
                lists["dirty_balloons"].append(
                    {"cid": cid, "pid": pid, "count": len(dirty),
                     "panels": sorted({pl.get("panel") for pl in dirty})})
    return {"ok": True, "totals": tot, "lists": lists,
            "style_sig": style_signature(project)}
