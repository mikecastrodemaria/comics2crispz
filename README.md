# comics2crispz

Atelier local de création de bandes dessinées, **agnostique du moteur de
génération** : l'app ne génère rien elle-même, elle pilote les outils de la
famille crispz (crispz-studio, crispz-qwen-edit, crispz-krea…) via le
[protocole CLI v1](docs/CLI_PROTOCOL.md) — spec JSON en entrée, JSON en sortie,
routage vers l'instance qui tourne.

Un projet = un dossier avec un `project.json` **compatible famille crispz**
(le moteur documentaire `cz_comic.py` est vendoré de crispz-studio) : le
🎞 Comic de crispz-studio, son 🎬 Comic Studio, le CLI `--comic` et
comics2crispz travaillent sur le même fichier, chacun stateless.

## Installer / lancer

```bat
install.bat                       REM venv + Pillow + config.json + livre d'exemple
run.bat books\exemple             REM sert http://127.0.0.1:8770/
```

ou :

```bat
python c2c_server.py chemin\du\projet --port 8770
```

Pas de projet sous la main ? `python tools\make_example.py` crée
`books\exemple\` (2 chapitres, 14 planches, placeholders composés) et
`--pages 100` un livre de stress pour le chemin de fer.

Dépendances : Python 3.10+, Pillow (`pip install -r requirements.txt`).
Le serveur est en stdlib pure (http.server), aucun framework.

## L'UI (voir [docs/UI_PLAN.md](docs/UI_PLAN.md) pour la cible complète)

Quatre modes sur un navigateur chapitres/pages permanent :

| Mode | État | Contenu |
|---|---|---|
| **Chemin de fer** | ✅ étape 1 | doubles pages en regard (la page de droite porte la chute), badges de rôle, folios, code couleur d'avancement (vide / partiel / généré / composé), **drag & drop** pour réordonner ou changer de chapitre (glisser sur une page = insérer avant ; sur un titre de chapitre = déplacer en fin), Compose page/book |
| **Planche** | aperçu + génération | la planche composée + rects des cases + bulles, **🎨 Generate missing** : les cases sans image partent au moteur configuré via le protocole CLI (prompt résolu par le casting @Name, taille au ratio exact de la case, `project.json` sauvé après chaque case, case au texte vide sautée avec warning — jamais de prompt vide envoyé au moteur), puis la planche se recompose ; l'édition fine (drag des bulles, dialogues) = étape suivante — en attendant, le Comic Studio de crispz-studio édite le même projet |
| **Scénario** | étape 2 | tout le livre en texte (format `.czs`, spécifié dans UI_PLAN) |
| **Production** | étape 3 | compteurs-filtres, batch via le protocole CLI |

Principes hérités de la famille : rien n'est perdu en silence (un déplacement
impossible est une erreur expliquée, jamais un clamp muet), les **ids de pages
sont stables** (les chemins `panels/` et `pages/` ne bougent pas quand on
réordonne — le folio fait foi à l'affichage), écriture atomique de
`project.json`.

## Architecture

```
c2c_server.py     serveur stdlib : SPA + API JSON (/api/index, /api/chapter,
                  /api/move_page, /api/compose…), vignettes cachées (/thumb/)
c2c_state.py      couche pure : index maigre + état par chapitre (pattern
                  manifests de l'Asset Browser), move_page, spreads (doubles
                  pages), composition + sidecar de placements
c2c_engines.py    client du protocole CLI famille : caps/gen par moteur,
                  route instance (modèle chaud) puis czp (chemin froid)
cz_comic.py       VENDORÉ de crispz-studio (verbatim, commit 92deee8) —
                  géométrie, casting @Name, lettrage ; resynchroniser en
                  recopiant le fichier depuis l'upstream
assets/studio.html  la SPA (vanilla HTML/JS, zéro build)
docs/             CLI_PROTOCOL.md (contrat famille) + UI_PLAN.md (cible UI)
```

Config : copier `config-sample.json` en `config.json` (local, gitignoré) et
renseigner les moteurs (`url` de l'instance + chemin `czp` de l'outil).

## Tests

```bat
python tools\run_tests.py
```

Sans GPU ni serveur : la couche `c2c_state` est pure, l'API est testée via un
serveur éphémère, les moteurs contre un mock du protocole.
