"""comics2crispz - .czs script format (crispz script): the whole book as text.

Write the book in the Script tab or in any editor, then import; export the
project back to the same text at any time (round-trip without loss).

    === ch01 : The departure ===
    > Alex gets an impossible delivery.        # synopsis (optional)
    ~ mood: cold night, blue moonlight         # chapter mood (optional)

    --- cover splash
    [pn1] dramatic comic book cover, @Alex on a cafe racer under neon rain
    SFX: PROJET TYPE

    --- page 3-classic seed=1234
    [pn1] interior of @Garage, @Alex pushes the door open, dripping wet
    CAP: Three in the morning. Again.
    Alex: Bruno? You there?
    [pn2] close-up of @Bruno looking up from an engine
    seed=42
    Bruno (think): Of course I am...
    [pn3] @Alex hands a sealed envelope to @Bruno

    --- back splash

Grammar:
  === id? : Name ===   chapter (id optional: absent = created on import)
  > text               chapter synopsis      ~ mood: text   chapter mood
  --- role? layout options?   page; role in cover/title/story/back (default
                       story); layout from cz_comic.LAYOUTS; seed=N optional
  [pnN] text           panel: the text may continue on the next lines until
                       the first dialogue line (Name: ..., CAP: ..., SFX: ...)
                       or the next [pn]; a `seed=N` line inside = panel seed
  # comment            ignored (a line starting with #)

House rules: parse errors REFUSE the import and cite the line (nothing is
half-applied); anything the import removes is quoted in the report; panels
whose text did not change keep image, status and seed; balloon positions
placed by dragging are re-attached to unchanged lines (merge_dialogue).
Pure module: no I/O, no server - fully testable.
"""

import re

import cz_comic
import c2c_state

_RE_CHAPTER = re.compile(r"^===\s*(?:(ch\d+)\s*)?:?\s*(.*?)\s*===\s*$")
_RE_PAGE = re.compile(r"^---\s*(.*?)\s*$")
_RE_PANEL = re.compile(r"^\[(pn\d+)\]\s*(.*)$")
_RE_SEED = re.compile(r"^seed\s*=\s*(-?\d+)\s*$", re.IGNORECASE)
# a dialogue line: 'CAP:', 'SFX:', or 'Name: text' / 'Name (mods): text'
_RE_DIALOGUE = re.compile(r"^(CAP|SFX|[^:\[\]#]{1,40}?(?:\s*\([^)]*\))?)\s*:\s*\S")


class ScriptError(ValueError):
    def __init__(self, line_no, msg):
        super().__init__(f"line {line_no}: {msg}")
        self.line_no = line_no


def _is_dialogue(line):
    return bool(_RE_DIALOGUE.match(line))


def parse_script(text):
    """.czs text -> outline dict (chapters/pages/panels) or ScriptError.
    Returns {"chapters": [...], "warnings": [...]}."""
    chapters, warnings = [], []
    ch = page = panel = None
    in_text = False           # inside a panel, still reading its description
    for no, raw in enumerate((text or "").splitlines(), 1):
        line = raw.rstrip()
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        m = _RE_CHAPTER.match(stripped)
        if m:
            cid, name = m.group(1), m.group(2).strip()
            if cid and any(c.get("id") == cid for c in chapters):
                raise ScriptError(no, f"chapter {cid} declared twice")
            ch = {"id": cid, "name": name or "Chapter", "synopsis": "",
                  "mood": "", "pages": [], "line": no}
            chapters.append(ch)
            page = panel = None
            continue
        if stripped.startswith("---"):
            if ch is None:
                raise ScriptError(no, "a page before any '=== chapter ==='")
            m = _RE_PAGE.match(stripped)
            toks = m.group(1).split() if m else []
            role, layout, seed = "story", None, None
            for t in toks:
                tl = t.lower()
                if tl in cz_comic.PAGE_ROLES and layout is None and role == "story" \
                        and tl != "story":
                    role = tl
                elif tl in ("story", "page"):        # '--- page 3-classic'
                    role = "story"
                elif tl.startswith("seed="):
                    try:
                        seed = int(tl[5:])
                    except ValueError:
                        raise ScriptError(no, f"bad page seed '{t}'")
                elif layout is None:
                    layout = t
                else:
                    raise ScriptError(no, f"unexpected token '{t}' on a page line "
                                          f"(--- role? layout seed=N?)")
            if not layout:
                raise ScriptError(no, "a page needs a layout (--- page 3-classic)")
            canon = cz_comic.canonical_layout(layout)
            if canon is None:
                raise ScriptError(no, f"unknown layout '{layout}' (known: "
                                      f"{', '.join(cz_comic.layout_names())})")
            layout = canon          # un alias est normalise ici: le project.json
                                    # ne contient QUE des noms canoniques
            page = {"role": role, "layout": layout, "seed": seed, "panels": [],
                    "line": no}
            ch["pages"].append(page)
            panel = None
            continue
        if ch is not None and page is None and stripped.startswith(">"):
            ch["synopsis"] = (ch["synopsis"] + " " + stripped[1:].strip()).strip()
            continue
        if ch is not None and page is None and stripped.lower().startswith("~ mood:"):
            ch["mood"] = stripped.split(":", 1)[1].strip()
            continue
        m = _RE_PANEL.match(stripped)
        if m:
            if page is None:
                raise ScriptError(no, "a [pn] panel before any '--- page' line")
            pnid = m.group(1)
            if any(p["id"] == pnid for p in page["panels"]):
                raise ScriptError(no, f"panel {pnid} declared twice on this page")
            cells = len(cz_comic.layout_cells(page["layout"]))
            if len(page["panels"]) >= cells:
                raise ScriptError(no, f"page has layout '{page['layout']}' "
                                      f"({cells} cells) but a {len(page['panels']) + 1}"
                                      f"th panel [{pnid}] - pick a larger layout or "
                                      f"split the page (texts are never dropped)")
            panel = {"id": pnid, "text": m.group(2).strip(), "seed": None,
                     "dialogue_lines": [], "line": no}
            page["panels"].append(panel)
            in_text = True
            continue
        if panel is None:
            raise ScriptError(no, f"text outside a panel: '{stripped[:40]}' "
                                  f"(start a panel with [pn1])")
        m = _RE_SEED.match(stripped)
        if m:
            panel["seed"] = int(m.group(1))
            continue
        if in_text and not _is_dialogue(stripped):
            panel["text"] = (panel["text"] + " " + stripped).strip()
            continue
        in_text = False
        panel["dialogue_lines"].append(stripped)
    if not chapters:
        raise ScriptError(0, "empty script: start with '=== ch01 : Name ==='")
    for c in chapters:
        for pg in c["pages"]:
            for pn in pg["panels"]:
                pn["dialogue"] = cz_comic.parse_dialogue("\n".join(pn["dialogue_lines"]))
                del pn["dialogue_lines"]
                if not pn["text"]:
                    warnings.append(f"line {pn['line']}: [{pn['id']}] has no "
                                    f"description (it will be skipped by Generate)")
    return {"chapters": chapters, "warnings": warnings}


def format_script(project, chapter_id=None):
    """project -> .czs text (the whole book, or one chapter). Inverse of
    parse_script + apply_script: round-trip without loss (balloon positions
    are placement, not script - they live in project.json only)."""
    out = []
    for ch in project.get("chapters") or []:
        if chapter_id and ch["id"] != chapter_id:
            continue
        out.append(f"=== {ch['id']} : {ch.get('name') or ch['id']} ===")
        if (ch.get("synopsis") or "").strip():
            out.append("> " + " ".join(ch["synopsis"].split()))
        if (ch.get("mood") or "").strip():
            out.append("~ mood: " + ch["mood"].strip())
        out.append("")
        for pg in ch.get("pages") or []:
            role = pg.get("role", "story")
            head = "--- " + (role + " " if role != "story" else "page ") + pg["layout"]
            if pg.get("seed") is not None and int(pg.get("seed", -1)) >= 0:
                head += f" seed={int(pg['seed'])}"
            out.append(head)
            for pn in pg.get("panels") or []:
                out.append(f"[{pn['id']}] " + " ".join((pn.get('text') or '').split()))
                if int(pn.get("seed", -1)) >= 0:
                    out.append(f"seed={int(pn['seed'])}")
                dlg = c2c_state.fmt_dialogue(pn.get("dialogue") or [])
                if dlg:
                    out.append(dlg)
            out.append("")
    return "\n".join(out).rstrip() + "\n"


def apply_script(project, outline):
    """Merge an outline (parse_script) INTO the project, in place. Chapters
    match by id (else created), pages by position inside the chapter, panels
    by id. Returns a report; every removal is quoted."""
    rep = {"chapters_added": [], "pages_added": 0, "pages_removed": [],
           "panels_changed": 0, "panels_unchanged": 0, "panels_removed": [],
           "dialogue_changed": 0, "warnings": list(outline.get("warnings") or [])}
    seen_ch = set()
    for sc in outline["chapters"]:
        ch = next((c for c in project.get("chapters") or []
                   if c["id"] == sc.get("id")), None) if sc.get("id") else None
        if ch is None:
            ch = cz_comic.add_chapter(project, sc["name"], sc.get("synopsis", ""),
                                      mood=sc.get("mood", ""))
            if sc.get("id"):                 # keep the id the author wrote
                ch["id"] = sc["id"]
            rep["chapters_added"].append(ch["id"])
        else:
            ch["name"] = sc["name"]
            ch["synopsis"] = sc.get("synopsis", "")
            ch["mood"] = sc.get("mood", "")
        seen_ch.add(ch["id"])
        old_pages = list(ch.get("pages") or [])
        new_pages = []
        for j, sp in enumerate(sc["pages"]):
            pg = old_pages[j] if j < len(old_pages) else None
            if pg is None:
                pg = cz_comic.add_page(project, ch["id"], sp["layout"], role=sp["role"])
                ch["pages"].remove(pg)          # re-inserted in order below
                rep["pages_added"] += 1
            else:
                pg["layout"] = sp["layout"]
                pg["role"] = sp["role"]
            if sp.get("seed") is not None:
                pg["seed"] = int(sp["seed"])
            old_panels = {p["id"]: p for p in pg.get("panels") or []}
            panels = []
            for spn in sp["panels"]:
                pn = old_panels.pop(spn["id"], None)
                if pn is None:
                    pn = cz_comic.new_panel(spn["id"], spn["text"])
                    rep["panels_changed"] += 1
                else:
                    if (pn.get("text") or "") != spn["text"]:
                        pn["text"] = spn["text"]
                        rep["panels_changed"] += 1
                    else:
                        rep["panels_unchanged"] += 1
                if spn.get("seed") is not None:
                    pn["seed"] = int(spn["seed"])
                old_dlg = pn.get("dialogue") or []
                new_dlg = c2c_state.merge_dialogue(old_dlg, spn["dialogue"])
                if c2c_state.fmt_dialogue(old_dlg) != c2c_state.fmt_dialogue(new_dlg):
                    rep["dialogue_changed"] += 1
                pn["dialogue"] = new_dlg
                panels.append(pn)
            # the layout may have FEWER cells than before: keep the extra
            # panels' texts in the report (never lost silently)
            for pid_, pn in old_panels.items():
                if (pn.get("text") or "").strip() or pn.get("image"):
                    rep["panels_removed"].append(
                        {"cid": ch["id"], "pid": pg["id"], "pnid": pid_,
                         "text": pn.get("text") or "", "had_image": bool(pn.get("image"))})
            pg["panels"] = panels
            new_pages.append(pg)
        for pg in old_pages[len(sc["pages"]):]:
            rep["pages_removed"].append(
                {"cid": ch["id"], "pid": pg["id"], "layout": pg["layout"],
                 "texts": [p.get("text") or "" for p in pg.get("panels") or []]})
        ch["pages"] = new_pages
    # chapters absent from the script are kept (a per-chapter import must not
    # wipe the others) - reported so nobody is surprised
    for ch in project.get("chapters") or []:
        if ch["id"] not in seen_ch:
            rep["warnings"].append(f"chapter {ch['id']} ({ch.get('name')}) is not "
                                   f"in the script - kept as is")
    return rep
