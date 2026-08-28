# -*- coding: utf-8 -*-
"""Cree books/demo6/ : un livre de 6 planches PRET A GENERER (cover, 4 story,
back - 14 cases, casting 2 personnages + 1 decor, dialogues complets), puis
genere les cases via un moteur de la famille (protocole CLI) si demande.

    python tools/make_demo6.py            # cree le projet seulement
    python tools/make_demo6.py --generate # + genere via l'instance qui tourne
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
        "Le Phare", description="Demo 6 planches generee par la famille "
                                "crispz via le protocole CLI.",
        page="Web")
    p["page"]["page_numbers"] = True
    p["page"]["border"] = 6
    p["style"]["prompt_suffix"] = ("comic book style, clean ink lines, flat "
                                   "cel shading, dramatic lighting")
    p["style"]["negative"] = "photo, photorealistic, blurry, watermark, text"

    p["casting"]["Nora"] = cc.new_character(
        "a young lighthouse keeper woman with a yellow raincoat and short "
        "dark hair", negative="old")
    p["casting"]["Tom"] = cc.new_character(
        "a skinny teenage boy with round glasses and a heavy backpack")
    p["casting"]["Phare"] = cc.new_character(
        "a tall white-and-red lighthouse on a rocky cliff above a stormy sea",
        kind="setting")

    ch = cc.add_chapter(p, "Le Phare", "Une nuit de tempete, la lampe "
                                       "s'eteint.")

    cover = cc.add_page(p, ch["id"], "splash", role="cover", texts=[
        "dramatic comic book cover, @Phare at night under a storm, a tiny "
        "lit window, huge waves crashing below, low angle"])
    cc.add_dialogue(cover["panels"][0], "LE PHARE", kind="sfx")
    cc.add_dialogue(cover["panels"][0], "Une histoire de la famille crispz",
                    kind="caption")

    s1 = cc.add_page(p, ch["id"], "3-classic", texts=[
        "wide shot of @Phare in the rain at dusk, a small figure climbing "
        "the cliff path",
        "@Tom knocking at a heavy wooden door, soaked, wind-blown",
        "@Nora opening the door, warm lamplight spilling out, surprised"])
    cc.add_dialogue(s1["panels"][0], "La tempete est arrivee avant lui.",
                    kind="caption")
    cc.add_dialogue(s1["panels"][1], "TOC TOC", kind="sfx")
    cc.add_dialogue(s1["panels"][2], "Un visiteur ? Par ce temps ?",
                    speaker="Nora")

    s2 = cc.add_page(p, ch["id"], "4-grid", texts=[
        "@Tom drying by an iron stove, steam rising from his coat, cozy "
        "lighthouse interior",
        "@Nora pouring tea, looking out a rain-streaked window",
        "close-up of the lighthouse lamp mechanism, brass gears, oil lamp",
        "sudden darkness, only two faces lit by the stove embers"])
    cc.add_dialogue(s2["panels"][0], "Merci... Je cherchais le village.",
                    speaker="Tom")
    cc.add_dialogue(s2["panels"][1], "Il n'y a que la mer, ici.",
                    speaker="Nora", style="rounded")
    cc.add_dialogue(s2["panels"][2], "CLIC", kind="sfx")
    cc.add_dialogue(s2["panels"][3], "La lampe !", speaker="Nora",
                    style="angular")

    s3 = cc.add_page(p, ch["id"], "5-hero", texts=[
        "@Nora and @Tom climbing a spiral staircase with a storm lantern, "
        "dynamic angle",
        "close-up of @Nora's hands repairing a brass mechanism",
        "@Tom holding the lantern high, worried face in warm light",
        "a cargo ship in huge waves, dangerously close to rocks, seen "
        "through a round window",
        "the lighthouse lamp blazing back to life, beam cutting the rain"])
    cc.add_dialogue(s3["panels"][0], "Tiens la lanterne. Et ne tombe pas.",
                    speaker="Nora")
    cc.add_dialogue(s3["panels"][2], "Il y a un bateau ! La-bas !",
                    speaker="Tom")
    cc.add_dialogue(s3["panels"][4], "VRAOUM", kind="sfx")

    s4 = cc.add_page(p, ch["id"], "3-strip", texts=[
        "the cargo ship turning away from the rocks, guided by the light "
        "beam, storm calming",
        "@Nora and @Tom side by side at the gallery railing, dawn light, "
        "wet but smiling",
        "wide shot of @Phare at sunrise, calm sea, gulls"])
    cc.add_dialogue(s4["panels"][0], "Le faisceau a suffi.", kind="caption")
    cc.add_dialogue(s4["panels"][1], "Tu cherchais le village...",
                    speaker="Nora")
    cc.add_dialogue(s4["panels"][1], "Je crois que je l'ai trouve.",
                    speaker="Tom", kind="thought")
    cc.add_dialogue(s4["panels"][2], "FIN", kind="sfx")

    back = cc.add_page(p, ch["id"], "splash", role="back", texts=[
        "minimalist back cover, a small storm lantern on dark blue "
        "background, one light beam"])
    cc.add_dialogue(back["panels"][0],
                    "Genere par comics2crispz via le protocole CLI famille.",
                    kind="caption")

    cc.save_project(p, dest)
    for c, pg in cc.book_order(p):
        c2c_state.compose_one(p, dest, c["id"], pg["id"])
    print(f"OK projet: {dest} — 6 planches, "
          f"{sum(len(pg['panels']) for _c, pg in cc.book_order(p))} cases.")
    return p


def generate(dest):
    studio = c2c_server.Studio(dest, c2c_server.load_config())
    project = studio.load()
    t0 = time.time()
    total = 0
    for c, pg in cc.book_order(project):
        res = studio.op_generate({"cid": c["id"], "pid": pg["id"]})
        if not res.get("ok"):
            print(f"ECHEC {c['id']}.{pg['id']}: {res.get('error')}")
            return 1
        done = res.get("generated") or []
        total += len(done)
        for w in res.get("warnings") or []:
            print(f"  ! {w}")
        for g in done:
            print(f"  {c['id']}.{pg['id']}.{g['panel']}  "
                  f"seed={g['seed_used']}  {g['total_s']}s")
    print(f"GENERATION OK: {total} case(s) en {time.time() - t0:.0f}s "
          f"(moteur: {res.get('engine')})")
    return 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--generate", action="store_true")
    ap.add_argument("--dest", default=None)
    args = ap.parse_args()
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    dest = args.dest or os.path.join(root, "books", "demo6")
    if not os.path.isfile(cc.project_json_path(dest)):
        build(dest)
    else:
        print(f"projet existant: {dest}")
    sys.exit(generate(dest) if args.generate else 0)
