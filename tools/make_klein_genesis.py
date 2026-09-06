# -*- coding: utf-8 -*-
"""Build books/klein-genesis: a 6-page comic about porting the crispz family
from a 20B model to FLUX.2 Klein 4B, and the bugs that showed up on the way.

Every gag is a real thing that happened during the port (see crispz-klein's
FORK.md and CHANGELOG): the guidance slider that changed nothing, the GGUFs
filtered out by an inherited config key, the 9B checkpoint that died on a shape
mismatch, the reasoning model that leaked its monologue into the image prompt,
and a layout name that did not exist.

    python tools/make_klein_genesis.py             # build the project only
    python tools/make_klein_genesis.py --generate  # + generate via the running engine

Layouts are chosen to stay inside the model's usable aspect range on this page
format - cz_comic.layout_warning() is asserted at build time, so this example
never ships a page that would render stretched.
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
        "Four Billion Reasons",
        description="How the crispz family went from a 20B model to FLUX.2 "
                    "Klein 4B, and every bug that came along for the ride.",
        page="Web")
    p["page"]["page_numbers"] = True
    p["page"]["border"] = 6
    # Un seul registre de style, pas une pile de mots-clefs contradictoires:
    # sur un modele distille a 4 steps, une soupe de styles s'effondre toujours
    # sur le meme rendu (leçon apprise sur 'le-jardin-de-beton').
    p["style"]["prompt_suffix"] = ("comic book style, clean ink lines, flat cel "
                                   "shading, warm desk lamp light")
    # NB: inerte sur crispz-klein (modele distille, pas de CFG). Conserve pour
    # les autres moteurs de la famille, qui l'honorent.
    p["style"]["negative"] = "photo, photorealistic, blurry, watermark"

    # Un desc de casting dit QUI, jamais ce que le personnage fait: une action
    # dans le desc part dans TOUTES les cases ou le nom apparait.
    p["casting"]["Dev"] = cc.new_character(
        "a developer in a hoodie, three-day beard, tired eyes, a cold mug of "
        "coffee never far away")
    p["casting"]["Klein"] = cc.new_character(
        "a small round robot the size of a melon, matte white shell, a single "
        "calm blue eye, four tiny thruster fins")
    p["casting"]["Bigmodel"] = cc.new_character(
        "an enormous rusted mainframe robot, twenty times the small one, "
        "dented chrome plating, cooling pipes, a dim orange eye")
    p["casting"]["Lab"] = cc.new_character(
        "a cluttered home lab at night, three monitors, a tower PC with a "
        "glowing GPU window, cables everywhere, sticky notes on the wall",
        kind="setting")

    ch = cc.add_chapter(p, "Four Billion Reasons",
                        "One GPU, two models, and a long list of things that "
                        "were not the seed.")

    # --- p1: couverture -------------------------------------------------
    p1 = cc.add_page(p, ch["id"], "splash", role="cover", texts=[
        "@Dev asleep face down on the desk in @Lab. @Bigmodel looms behind the "
        "chair filling the whole room, its orange eye dim. @Klein sits on the "
        "keyboard, wide awake, lit by the GPU glow"])
    cc.add_dialogue(p1["panels"][0], "FOUR BILLION REASONS", kind="sfx")
    cc.add_dialogue(p1["panels"][0], "a crispz family story", kind="caption")

    # --- p2: l'ere du 20B ------------------------------------------------
    p2 = cc.add_page(p, ch["id"], "6-grid", texts=[
        "@Bigmodel filling the entire @Lab, pipes bent against the ceiling",
        "close on a VRAM gauge pinned at the top, needle bent",
        "@Dev typing one word, then leaning back to wait",
        "@Dev drinking coffee, then a second cup, the clock behind him moved",
        "a second identical mainframe rolling in through the door, @Dev horrified",
        "@Dev face down on the desk, both mainframes humming"])
    cc.add_dialogue(p2["panels"][0], "Twenty billion parameters.", kind="caption")
    cc.add_dialogue(p2["panels"][1], "Forty gigabytes. For one picture.",
                    speaker="Dev")
    cc.add_dialogue(p2["panels"][2], "...", speaker="Dev", kind="thought")
    cc.add_dialogue(p2["panels"][3], "Still loading the text encoder.",
                    speaker="Bigmodel")
    cc.add_dialogue(p2["panels"][4], "And this is the one that EDITS.",
                    speaker="Bigmodel")
    cc.add_dialogue(p2["panels"][5], "Two models. Same picture.", kind="caption")

    # --- p3: klein arrive -------------------------------------------------
    p3 = cc.add_page(p, ch["id"], "3-hero", texts=[
        "a small shipping crate open on the desk in @Lab, @Klein standing in it "
        "no bigger than the coffee mug, @Dev leaning down to look",
        "close on @Klein's single blue eye, calm",
        "@Klein hovering above the keyboard on its four tiny fins while "
        "@Bigmodel watches from the shadows"])
    cc.add_dialogue(p3["panels"][0], "Four billion. Fifteen gigabytes. Four steps.",
                    kind="caption")
    cc.add_dialogue(p3["panels"][1], "You are going to be disappointed.",
                    speaker="Bigmodel")
    cc.add_dialogue(p3["panels"][2], "One point six seconds.", speaker="Klein")

    # --- p4: la parade de bugs -------------------------------------------
    p4 = cc.add_page(p, ch["id"], "9-grid", texts=[
        "@Dev turning a large physical dial labelled GUIDANCE, satisfied",
        "two identical drawings pinned side by side, a magnifier between them",
        "@Klein shrugging with its tiny fins",
        "@Dev holding a long list of model files, all crossed out in red",
        "close on a config sticky note reading only a crossed-out old name",
        "@Klein carrying one enormous file box while a slightly bigger box "
        "waits behind it",
        "@Dev in front of a wall of error text, one line circled",
        "a chat bubble machine printing a very long paper strip of rambling text",
        "@Dev cutting the paper strip with scissors, keeping one short piece"])
    cc.add_dialogue(p4["panels"][0], "More guidance. Obviously.", speaker="Dev")
    cc.add_dialogue(p4["panels"][1], "Bit for bit identical.", kind="caption")
    cc.add_dialogue(p4["panels"][2], "Distilled. The dial is decoration.",
                    speaker="Klein")
    cc.add_dialogue(p4["panels"][3], "Eleven checkpoints. All skipped.",
                    speaker="Dev")
    cc.add_dialogue(p4["panels"][4], "It was still filtering for the OLD model.",
                    kind="caption")
    cc.add_dialogue(p4["panels"][5], "Nine billion does not fit in four.",
                    speaker="Klein")
    cc.add_dialogue(p4["panels"][6], "expected 3072, got 4096", kind="sfx")
    cc.add_dialogue(p4["panels"][7], "Okay, so the user wants a prompt about...",
                    speaker="Bigmodel")
    cc.add_dialogue(p4["panels"][8], "Nobody needs to read you think.",
                    speaker="Dev")

    # --- p5: les correctifs ----------------------------------------------
    p5 = cc.add_page(p, ch["id"], "6-grid", texts=[
        "@Dev writing on a whiteboard: a short list replacing a long one",
        "@Klein slotting a small labelled key into a lock that finally turns",
        "@Dev holding up a page template, measuring its proportions with a ruler",
        "a very tall narrow page panel being rejected, stamped",
        "@Klein and @Dev shaking hand and fin over a tidy desk",
        "@Bigmodel unplugged in the corner, a dust sheet half over it, "
        "its orange eye off"])
    cc.add_dialogue(p5["panels"][0], "Say it once. Say what to do.", speaker="Dev")
    cc.add_dialogue(p5["panels"][1], "And stop calling things by the old name.",
                    speaker="Klein")
    cc.add_dialogue(p5["panels"][2], "One to three point five?", speaker="Dev")
    cc.add_dialogue(p5["panels"][3], "Outside the training range.", kind="caption")
    cc.add_dialogue(p5["panels"][4], "Warn before the render. Not after sixty.",
                    kind="caption")
    cc.add_dialogue(p5["panels"][5], "Thank you for your service.", speaker="Dev")

    # --- p6: finale -------------------------------------------------------
    p6 = cc.add_page(p, ch["id"], "splash", role="back", texts=[
        "wide shot of @Lab at dawn, a finished comic page pinned above the "
        "monitors, @Klein perched on the frame, @Dev asleep in the chair with "
        "a fresh mug still steaming"])
    cc.add_dialogue(p6["panels"][0], "Sixty panels. Three minutes.", kind="caption")
    cc.add_dialogue(p6["panels"][0], "The seed was never the problem.",
                    kind="caption")

    # Garde-fou: aucune planche de l'exemple ne doit produire de case hors de
    # la plage de ratios exploitable par le modele sur ce format de page.
    bad = []
    for _c, pg in cc.book_order(p):
        w = cc.layout_warning(pg["layout"], p.get("page"))
        if w:
            bad.append(f"{pg['id']}: {w}")
    if bad:
        raise SystemExit("layout out of range in the example:\n  " + "\n  ".join(bad))

    cc.save_project(p, dest)
    n = sum(len(pg["panels"]) for _c, pg in cc.book_order(p))
    print(f"built {dest}: {len(ch['pages'])} pages, {n} panels")
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
    dest = args.dest or os.path.join(root, "books", "klein-genesis")
    if not os.path.isfile(cc.project_json_path(dest)):
        build(dest)
    else:
        print(f"existing project: {dest}")
    sys.exit(generate(dest) if args.generate else 0)
