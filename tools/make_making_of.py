# -*- coding: utf-8 -*-
"""Build books/making-of: the tutorial book used by the README - a short
comic telling how comics2crispz came to be (one workshop, many engines, one
CLI protocol). Optionally generates every panel through a family engine.

    python tools/make_making_of.py             # build the project only
    python tools/make_making_of.py --generate  # + generate via the running engine
"""
import argparse
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import cz_comic as cc
import c2c_state
import c2c_server


def build(dest):
    p = cc.new_project(
        "The Making of comics2crispz",
        description="Tutorial book: the genesis of comics2crispz, drawn by "
                    "the crispz family through the CLI protocol.",
        page="Web")
    p["page"]["page_numbers"] = True
    p["page"]["border"] = 6
    p["style"]["prompt_suffix"] = ("comic book style, clean ink lines, flat "
                                   "cel shading, warm colors")
    p["style"]["negative"] = "photo, photorealistic, blurry, watermark, text"

    p["casting"]["Mika"] = cc.new_character(
        "a comic artist with round glasses, ink-stained fingers and a grey "
        "hoodie")
    p["casting"]["Robi"] = cc.new_character(
        "a small friendly courier robot with a satchel full of envelopes, "
        "one glowing eye")
    p["casting"]["Atelier"] = cc.new_character(
        "a cozy cluttered artist workshop at night, drawing desk, monitors, "
        "coffee mugs, warm lamp light", kind="setting")

    ch = cc.add_chapter(p, "The Making Of",
                        "One workshop, many engines, one protocol.")

    cover = cc.add_page(p, ch["id"], "splash", role="cover", texts=[
        "dramatic comic book cover, @Mika drawing at a desk in @Atelier, "
        "comic pages pinned on the wall behind, low warm light"])
    cc.add_dialogue(cover["panels"][0], "THE MAKING OF", kind="sfx")
    cc.add_dialogue(cover["panels"][0], "How comics2crispz was born",
                    kind="caption")

    s1 = cc.add_page(p, ch["id"], "3-hero", texts=[
        "@Mika surrounded by five glowing computer screens, each showing a "
        "different user interface, overwhelmed, papers flying",
        "close-up of @Mika's tired face lit by screens",
        "@Mika slamming a sketchbook on the desk, determined"])
    cc.add_dialogue(s1["panels"][0], "Five tools. Five interfaces. One comic "
                                     "to draw.", kind="caption")
    cc.add_dialogue(s1["panels"][1], "There has to be a better way...",
                    speaker="Mika", kind="thought")
    cc.add_dialogue(s1["panels"][2], "One workshop. Whatever engine runs "
                                     "behind.", speaker="Mika")

    s2 = cc.add_page(p, ch["id"], "4-grid", texts=[
        "@Robi popping out of a cardboard box on the desk, cheerful",
        "@Robi holding up a paper envelope labeled with a gear symbol",
        "@Robi sliding the envelope under the door of a huge humming server "
        "tower",
        "the tower lighting up, gears turning, a picture coming out of a "
        "slot"])
    cc.add_dialogue(s2["panels"][0], "BIP !", kind="sfx")
    cc.add_dialogue(s2["panels"][1], "One envelope, same shape for every "
                                     "engine.", speaker="Robi")
    cc.add_dialogue(s2["panels"][2], "The engine that is awake answers.",
                    kind="caption")
    cc.add_dialogue(s2["panels"][3], "VROOM", kind="sfx")

    s3 = cc.add_page(p, ch["id"], "5-hero", texts=[
        "wide shot of @Mika pinning comic page thumbnails on a big cork "
        "wall in @Atelier, pages arranged in pairs",
        "close-up of two facing pages pinned side by side",
        "@Mika moving one small page card to another spot on the wall",
        "@Robi carrying a tiny freshly printed panel like a waiter",
        "the wall complete, a whole comic book laid out, @Mika smiling arms "
        "crossed"])
    cc.add_dialogue(s3["panels"][0], "The flatplan: the whole book at a "
                                     "glance.", kind="caption")
    cc.add_dialogue(s3["panels"][2], "The right-hand page carries the "
                                     "surprise.", speaker="Mika")
    cc.add_dialogue(s3["panels"][3], "Fresh panel !", speaker="Robi",
                    style="rounded")

    s4 = cc.add_page(p, ch["id"], "3-rows", texts=[
        "night scene, @Atelier window glowing, the server tower humming, "
        "envelopes flying between them like birds",
        "@Mika asleep on the desk, @Robi gently covering them with a "
        "blanket, pages printing quietly",
        "morning light, a finished comic book on the desk, coffee steaming"])
    cc.add_dialogue(s4["panels"][0], "While the artist sleeps, the protocol "
                                     "keeps its word.", kind="caption")
    cc.add_dialogue(s4["panels"][1], "Good night, boss.", speaker="Robi",
                    kind="thought")
    cc.add_dialogue(s4["panels"][2], "FIN", kind="sfx")

    back = cc.add_page(p, ch["id"], "splash", role="back", texts=[
        "minimalist back cover, a paper envelope with a gear symbol on a "
        "dark background, one warm light ray"])
    cc.add_dialogue(back["panels"][0],
                    "Made with comics2crispz and the crispz family CLI "
                    "protocol.", kind="caption")

    cc.save_project(p, dest)
    for c, pg in cc.book_order(p):
        c2c_state.compose_one(p, dest, c["id"], pg["id"])
    n = sum(len(pg["panels"]) for _c, pg in cc.book_order(p))
    print(f"OK project: {dest} - 6 pages, {n} panels.")
    return p


def generate(dest):
    studio = c2c_server.Studio(dest, c2c_server.load_config())
    project = studio.load()
    t0 = time.time()
    total = 0
    res = {}
    for c, pg in cc.book_order(project):
        res = studio.op_generate({"cid": c["id"], "pid": pg["id"]})
        if not res.get("ok"):
            print(f"FAILED {c['id']}.{pg['id']}: {res.get('error')}")
            return 1
        done = res.get("generated") or []
        total += len(done)
        for w in res.get("warnings") or []:
            print(f"  ! {w}")
        for g in done:
            print(f"  {c['id']}.{pg['id']}.{g['panel']}  "
                  f"seed={g['seed_used']}  {g['total_s']}s")
    print(f"GENERATION OK: {total} panel(s) in {time.time() - t0:.0f}s "
          f"(engine: {res.get('engine')})")
    return 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--generate", action="store_true")
    ap.add_argument("--dest", default=None)
    args = ap.parse_args()
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    dest = args.dest or os.path.join(root, "books", "making-of")
    if not os.path.isfile(cc.project_json_path(dest)):
        build(dest)
    else:
        print(f"existing project: {dest}")
    sys.exit(generate(dest) if args.generate else 0)
