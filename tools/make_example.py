# -*- coding: utf-8 -*-
"""Build books/exemple: a demo book for the flatplan (2 chapters,
cover/title/story/back, varied layouts, dialogues), pages composed with
placeholders (no GPU).

    python tools/make_example.py              # 14 pages
    python tools/make_example.py --pages 100  # stress book (one extra
                                              # chapter of N pages)
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import cz_comic as cc
import c2c_state

LAYOUT_CYCLE = ["3-hero", "4-grid", "5-hero", "3-rows", "2-rows", "6-grid"]


def build(dest, extra_pages=0):
    if os.path.isfile(cc.project_json_path(dest)):
        print(f"ALREADY EXISTS - untouched: {dest}")
        return 1
    p = cc.new_project("Exemple c2c", page="Web")
    p["page"]["page_numbers"] = True
    p["casting"]["Alex"] = cc.new_character("a young courier, orange jacket")
    p["casting"]["Bruno"] = cc.new_character("a cheerful bearded mechanic")

    ch1 = cc.add_chapter(p, "Chapitre 1 — Le départ")
    cover = cc.add_page(p, ch1["id"], "splash", role="cover",
                        texts=["dramatic cover, @Alex on a motorcycle"])
    cc.add_dialogue(cover["panels"][0], "EXEMPLE", kind="sfx")
    title = cc.add_page(p, ch1["id"], "splash", role="title")
    cc.add_dialogue(title["panels"][0], "Chapitre 1", kind="caption")
    for i in range(5):
        pg = cc.add_page(p, ch1["id"], LAYOUT_CYCLE[i % len(LAYOUT_CYCLE)])
        cc.add_dialogue(pg["panels"][0], f"Planche {i + 1}, on avance.",
                        speaker="Alex")
        if len(pg["panels"]) > 1:
            cc.add_dialogue(pg["panels"][1], "Plus vite !", speaker="Bruno")

    ch2 = cc.add_chapter(p, "Chapitre 2 — La route")
    t2 = cc.add_page(p, ch2["id"], "splash", role="title")
    cc.add_dialogue(t2["panels"][0], "Chapitre 2", kind="caption")
    for i in range(5):
        pg = cc.add_page(p, ch2["id"], LAYOUT_CYCLE[(i + 3) % len(LAYOUT_CYCLE)])
        cc.add_dialogue(pg["panels"][0], "CAP: La route continue.", kind="caption")

    if extra_pages:
        ch3 = cc.add_chapter(p, f"Chapitre 3 — Stress ({extra_pages} pages)")
        for i in range(extra_pages):
            cc.add_page(p, ch3["id"], LAYOUT_CYCLE[i % len(LAYOUT_CYCLE)])

    back = cc.add_page(p, ch2["id"], "splash", role="back")
    cc.add_dialogue(back["panels"][0], "Dos d'album.", kind="caption")
    cc.save_project(p, dest)

    for ch, pg in cc.book_order(p):
        c2c_state.compose_one(p, dest, ch["id"], pg["id"])
    n = len(cc.book_order(p))
    print(f"OK: {dest} — {n} page(s) composed.")
    print("Start with:  run.bat books\\exemple")
    return 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--pages", type=int, default=0,
                    help="extra stress pages (chapter 3)")
    ap.add_argument("--dest", default=None)
    args = ap.parse_args()
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    dest = args.dest or os.path.join(root, "books", "exemple")
    sys.exit(build(dest, args.pages))
