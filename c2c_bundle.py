"""comics2crispz - the book bundle: ONE Markdown file with everything that
defines a book (settings, style & mood, story bible, visual bible, script),
exportable and importable without loss.

    # La fille des ruines fleuries
    <!-- comics2crispz bundle v1 -->

    ## Settings
    ```json
    {"name": "...", "description": "...", "page": {...}, "engine": {...}}
    ```
    ## Style
    ```json
    {"prompt_suffix": "...", "negative": "...", "loras": [...], "mood": "...", "font": "..."}
    ```
    ## Story bible
    ```json
    {"story": {"concept": "...", "language": "..."}}
    ```
    ## Visual bible
    ### @Lea (character)
    ```json
    {"desc": "...", "refs": ["refs/Lea/01.png"], "loras": [], "negative": "", "kind": "character"}
    ```
    ## Script
    ```czs
    === ch01 : ... ===
    ```

Readable as a document, exact as data: every block is fenced JSON (or the
.czs script), so the round-trip export -> import -> export is identical.
Reference IMAGES are not inside the file (Markdown is text): the bundle
lists their paths, relative to the book folder; on import a missing file is
reported, never silently dropped from the card. The drawings (panels/) and
composed pages are not part of the bundle either - they are the OUTPUT;
the bundle is the RECIPE. Pure module: no I/O, no server.
"""

import re
import json

import cz_comic
import c2c_script

BUNDLE_VERSION = 1
_SETTINGS_KEYS = ("name", "description", "page", "engine")
_STYLE_KEYS = ("prompt_suffix", "negative", "loras", "mood", "font")
_CARD_KEYS = ("desc", "refs", "loras", "negative", "kind")


def _fence(lang, text):
    return f"```{lang}\n{text.rstrip()}\n```\n"


def _json(obj):
    return json.dumps(obj, ensure_ascii=False, indent=1)


def export_bundle(project):
    """project -> Markdown bundle text."""
    st = project.get("style") or {}
    out = [f"# {project.get('name') or 'Untitled'}",
           f"<!-- comics2crispz bundle v{BUNDLE_VERSION} - settings, style, bibles and "
           f"script of the book; import it back with the Script tab -->", ""]
    out.append("## Settings")
    out.append(_fence("json", _json({k: project.get(k) for k in _SETTINGS_KEYS
                                     if project.get(k) is not None})))
    out.append("## Style")
    out.append(_fence("json", _json({k: st.get(k) for k in _STYLE_KEYS
                                     if st.get(k) not in (None, "", [])})))
    out.append("## Story bible")
    out.append(_fence("json", _json(project.get("bible") or {})))
    out.append("## Visual bible")
    casting = project.get("casting") or {}
    if not casting:
        out.append("(no character or setting yet)\n")
    for name, c in sorted(casting.items(), key=lambda kv: kv[0].lower()):
        kind = (c or {}).get("kind", "character")
        out.append(f"### @{name} ({kind})")
        card = {k: (c or {}).get(k) for k in _CARD_KEYS}
        card["kind"] = kind
        out.append(_fence("json", _json(card)))
    out.append("## Script")
    out.append(_fence("czs", c2c_script.format_script(project)))
    return "\n".join(out)


class BundleError(ValueError):
    pass


def parse_bundle(text):
    """Markdown bundle -> {"title", "settings", "style", "bible", "casting",
    "script"} (missing sections = None) or BundleError."""
    lines = (text or "").splitlines()
    if not lines or not any(l.strip().startswith("## ") for l in lines):
        raise BundleError("not a bundle: no '## Settings' / '## Script' section "
                          "(a .czs script goes through Import, not the bundle)")
    title = None
    sections = {}          # heading -> list of (subheading|None, fence_lang, body)
    cur, sub = None, None
    fence, buf = None, []
    for no, raw in enumerate(lines, 1):
        line = raw.rstrip("\n")
        if fence is not None:
            if line.strip().startswith("```"):
                sections.setdefault(cur, []).append((sub, fence, "\n".join(buf)))
                fence, buf = None, []
            else:
                buf.append(line)
            continue
        s = line.strip()
        if s.startswith("```"):
            if cur is None:
                raise BundleError(f"line {no}: a code block before any '## ' section")
            fence = s[3:].strip().lower() or "text"
            buf = []
        elif s.startswith("### "):
            sub = s[4:].strip()
        elif s.startswith("## "):
            cur = s[3:].strip().lower()
            sub = None
        elif s.startswith("# ") and title is None and cur is None:
            title = s[2:].strip()
    if fence is not None:
        raise BundleError("unterminated ``` code block")

    def one_json(key, what):
        blocks = [b for b in sections.get(key, []) if b[0] is None and b[1] == "json"]
        if not blocks:
            return None
        try:
            v = json.loads(blocks[0][2] or "{}")
        except ValueError as e:
            raise BundleError(f"'{what}' block is not valid JSON: {e}")
        if not isinstance(v, dict):
            raise BundleError(f"'{what}' block must be a JSON object")
        return v

    casting = None
    if "visual bible" in sections:
        casting = {}
        for sub_, lang, body in sections["visual bible"]:
            if not sub_ or lang != "json":
                continue
            m = re.match(r"^@?([A-Za-z0-9_\-]+)\s*(?:\(([^)]*)\))?\s*$", sub_)
            if not m:
                raise BundleError(f"visual bible: bad card heading '{sub_}' "
                                  f"(expected '### @Name (character|setting)')")
            try:
                card = json.loads(body or "{}")
            except ValueError as e:
                raise BundleError(f"card @{m.group(1)}: not valid JSON: {e}")
            kind = card.get("kind") or (m.group(2) or "").strip() or "character"
            if kind not in ("character", "setting"):
                raise BundleError(f"card @{m.group(1)}: kind must be character or "
                                  f"setting, not '{kind}'")
            casting[m.group(1)] = cz_comic.new_character(
                str(card.get("desc") or ""), refs=card.get("refs") or [],
                lora=card.get("loras") or [], negative=str(card.get("negative") or ""),
                kind=kind)
    script = None
    for sub_, lang, body in sections.get("script", []):
        if lang in ("czs", "text", ""):
            script = body
            break
    return {"title": title, "settings": one_json("settings", "Settings"),
            "style": one_json("style", "Style"),
            "bible": one_json("story bible", "Story bible"),
            "casting": casting, "script": script}


def apply_bundle(project, bundle, ref_exists=None):
    """Merge a parsed bundle INTO the project (in place). Sections absent
    from the file are left untouched. Returns a report; the script part
    reuses apply_script (same guarantees). `ref_exists(path) -> bool` lets
    the caller report reference images missing on disk."""
    rep = {"settings": [], "style": False, "bible": False,
           "casting_added": [], "casting_updated": [], "casting_removed": [],
           "refs_missing": [], "script": None, "warnings": []}
    st_ = bundle.get("settings")
    if st_:
        for k in _SETTINGS_KEYS:
            if k in st_ and st_[k] != project.get(k):
                if k == "page":
                    try:
                        project["page"] = cz_comic.page_size(st_["page"])
                    except Exception as e:
                        rep["warnings"].append(f"page settings ignored: {e}")
                        continue
                    if any(pn.get("image") for ch in project.get("chapters") or []
                           for pg in ch.get("pages") or [] for pn in pg.get("panels") or []):
                        rep["warnings"].append("page size/margins changed on a book "
                                               "that already has drawings: Compose "
                                               "book to re-letter, panels keep their "
                                               "old ratio until regenerated")
                else:
                    project[k] = st_[k]
                rep["settings"].append(k)
    if bundle.get("style") is not None:
        style = project.setdefault("style", {})
        new = {k: bundle["style"].get(k) for k in _STYLE_KEYS}
        new["loras"] = [str(x) for x in (new.get("loras") or [])]
        for k in ("prompt_suffix", "negative", "mood", "font"):
            new[k] = str(new.get(k) or "")
        if any(style.get(k) != new[k] for k in _STYLE_KEYS):
            style.update(new)
            rep["style"] = True
    if bundle.get("bible") is not None and bundle["bible"] != (project.get("bible") or {}):
        project["bible"] = bundle["bible"]
        rep["bible"] = True
    if bundle.get("casting") is not None:
        old = project.get("casting") or {}
        newc = bundle["casting"]
        for name, card in newc.items():
            if name not in old:
                rep["casting_added"].append(name)
            elif old[name] != card:
                rep["casting_updated"].append(name)
            for r in card.get("refs") or []:
                if ref_exists is not None and not ref_exists(r):
                    rep["refs_missing"].append(f"@{name}: {r}")
        for name in old:
            if name not in newc:
                rep["casting_removed"].append(name)
        project["casting"] = newc
    if bundle.get("script"):
        outline = c2c_script.parse_script(bundle["script"])      # ScriptError -> caller
        rep["script"] = c2c_script.apply_script(project, outline)
    return rep
