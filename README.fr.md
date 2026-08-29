# comics2crispz (version française)

> La version de référence est [README.md](README.md) (anglais).

Atelier local de création de bandes dessinées, **agnostique du moteur** :
l'app ne génère rien elle-même, elle pilote les outils de la famille crispz
(crispz-studio, crispz-qwen-edit, crispz-krea…) via le
[protocole CLI v1](docs/CLI_PROTOCOL.md) — spec JSON en entrée, JSON en
sortie, routé vers l'instance qui tourne (modèle chaud, une seule file GPU).

Un projet = un dossier avec un `project.json` **compatible famille crispz**
(`cz_comic.py` vendoré) : l'accordéon 🎞 Comic de crispz-studio, son
🎬 Comic Studio, le CLI `--comic` et comics2crispz travaillent sur le même
fichier, chacun stateless.

## Installer / lancer

```bat
install.bat                       REM venv + Pillow + config.json + livre d'exemple
run.bat books\exemple             REM sert http://127.0.0.1:8770/
```

Copier `config-sample.json` en `config.json` (local, gitignoré) et pointer
les `engines` vers tes installations crispz.

## L'UI — quatre modes

- **Flatplan (chemin de fer)** : doubles pages en regard (la page de droite
  porte la chute), badges de rôle, folios, couleurs d'avancement, drag &
  drop pour réordonner (glisser sur une page = insérer avant ; sur un titre
  de chapitre = déplacer en fin), Compose page/book.
- **Page** : aperçu + **🎨 Generate missing** — les cases sans image partent
  au moteur configuré (prompt résolu par le casting @Name, ratio exact de la
  case, sauvegarde après chaque case, case vide sautée avec warning).
- **Script** (étape 2) et **Production** (étape 3) : à venir.

Règles maison héritées de la famille : rien ne se perd en silence, les ids
de pages sont stables (le folio fait foi à l'affichage), écriture atomique
de `project.json`.

## Tutoriel

Le tutoriel complet, étape par étape et avec captures (installation, écriture
du livre en script, flatplan, génération par le moteur, cohérence des
personnages par refs, export PDF), est dans le
[README anglais](README.md#tutorial--a-full-book-step-by-step). Le livre
d'exemple du tutoriel — *The Making of comics2crispz*, la genèse de cette
app racontée en BD — se construit avec :

```bat
.venv\Scripts\python.exe tools\make_making_of.py            REM le projet seul
.venv\Scripts\python.exe tools\make_making_of.py --generate REM + génération via le moteur
```

Le résultat fini de ce tutoriel :
📖 **[The Making of comics2crispz — PDF final](docs/the-making-of.pdf)**
(6 pages, 17 cases, un moteur, zéro retouche manuelle).

## Tests

```bat
.venv\Scripts\python.exe tools\run_tests.py
```

Sans GPU ni serveur.
