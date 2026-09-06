"""Le vocabulaire des gabarits n'est pas regulier: '2-up' = deux bandes empilees,
mais les trois bandes s'appellent '3-strip'. Ecrire '3-up' est donc l'erreur
naturelle -- surtout quand un LLM redige le script -- et elle bloquait tout le
parsing sur "unknown layout '3-up'".

Les alias sont acceptes partout et NORMALISES a l'ecriture: un project.json ne
contient que des noms canoniques, et layout_names() n'en expose qu'un par gabarit.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import c2c_script
import cz_comic


def test_aliases_resolve_to_the_same_cells():
    for alias, canon in cz_comic.LAYOUT_ALIASES.items():
        assert canon in cz_comic.LAYOUTS, f"{alias} pointe sur un gabarit inexistant"
        assert cz_comic.canonical_layout(alias) == canon, alias
        assert cz_comic.layout_cells(alias) == cz_comic.layout_cells(canon), alias
    print("OK test_aliases_resolve_to_the_same_cells")


def test_3_up_is_3_strip():
    assert cz_comic.canonical_layout("3-up") == "3-strip"
    assert len(cz_comic.layout_cells("3-up")) == 3
    print("OK test_3_up_is_3_strip")


def test_canonical_names_stay_canonical():
    for name in cz_comic.layout_names():
        assert cz_comic.canonical_layout(name) == name, name
    # un alias ne doit PAS polluer la liste exposee
    for alias in cz_comic.LAYOUT_ALIASES:
        assert alias not in cz_comic.layout_names(), alias
    print("OK test_canonical_names_stay_canonical")


def test_unknown_is_still_refused():
    assert cz_comic.canonical_layout("bidon") is None
    assert cz_comic.canonical_layout("") is None
    assert cz_comic.canonical_layout(None) is None
    try:
        cz_comic.layout_cells("bidon")
    except ValueError as e:
        assert "unknown layout" in str(e)
    else:
        raise AssertionError("un gabarit inconnu doit toujours lever")
    print("OK test_unknown_is_still_refused")


def test_script_parser_accepts_and_normalizes():
    src = ("=== chapter Test ===\n"
           "--- page 3-up\n[pn1]\na\n[pn2]\nb\n[pn3]\nc\n"
           "--- page full\n[pn1]\nd\n")
    out = c2c_script.parse_script(src)
    layouts = [p["layout"] for ch in out["chapters"] for p in ch["pages"]]
    assert layouts == ["3-strip", "splash"], layouts
    print("OK test_script_parser_accepts_and_normalizes")


def test_script_parser_still_refuses_a_real_typo():
    src = "=== chapter T ===\n--- page 7-up\n[pn1]\na\n"
    try:
        c2c_script.parse_script(src)
    except c2c_script.ScriptError as e:
        assert "unknown layout '7-up'" in str(e), str(e)
    else:
        raise AssertionError("'7-up' n'existe pas, meme comme alias")
    print("OK test_script_parser_still_refuses_a_real_typo")


if __name__ == "__main__":
    for fn in (test_aliases_resolve_to_the_same_cells, test_3_up_is_3_strip,
               test_canonical_names_stay_canonical, test_unknown_is_still_refused,
               test_script_parser_accepts_and_normalizes,
               test_script_parser_still_refuses_a_real_typo):
        fn()
    print("All layout alias tests passed.")
