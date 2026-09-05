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
start.bat
```

C'est tout : le premier lancement installe ce qu'il faut (venv + Pillow +
config + un livre d'exemple), puis **ton livre le plus récent s'ouvre dans le
navigateur**. Dans l'app, le **menu 📚** change de livre et **➕ New book** en
crée un (couverture + une première planche 4 cases, prête à remplir) ; le tout
premier lancement affiche un écran d'accueil. Linux/macOS : `./start.sh`.
Avancé : `run.bat books\exemple` (livre précis, sans navigateur),
`start.bat books\autre --port 8771` (deux livres côte à côte).

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

### Travaux de fond — progression, pause, stop

La génération du livre entier, « Redraw its panels » et « Redraw the whole
book » tournent en fond. Une barre sous l'en-tête les suit partout : quoi,
page/case x/y, cases dessinées, temps écoulé et estimation du reste, les
derniers éléments avec durée et seed, avertissements à la fin. **⏸ Pause**
prend effet après l'image en cours (rien n'est interrompu à mi-rendu),
**▶ Resume** reprend là où ça s'est arrêté, **⏹ Stop** termine après
l'élément en cours. Les anciens dessins restent dans l'historique.

### Corriger une case — Régénérer, Retoucher, Variation, Versions

Cliquer une case sur la planche ouvre son panneau :

- **🎲 Regenerate** redessine cette case seule depuis son texte (et sa seed).
- **✏️ Edit by instruction** garde le dessin et change ce qu'on demande
  (« add heavy rain, keep the ink style ») via le modèle d'édition du moteur
  (crispz-qwen-edit, ou crispz-studio avec un modèle omni). Un moteur sans
  modèle d'édition le dit, il ne devine pas.
- **🔁 Variation** redessine la case À PARTIR de son image avec une force
  (0,3 = même composition légèrement redessinée, 0,6 = vraie variation).
  Marche sur tous les moteurs (op `upscale` du protocole, facteur 1 = img2img).
- **🖌 Inpaint** redessine SEULEMENT la zone peinte sur la case (pinceau sur
  la planche, gomme, effacer), d'après une description LOCALE de ce qui doit
  y apparaître (« a black cat sitting » ; vide = remplissage cohérent),
  jamais le prompt de scène sur un fragment. Force 1,0 = zone entièrement
  redessinée. Tous les moteurs (op `inpaint`) ; le masque reste en
  `<case>.mask.png`.
- **🕘 Versions** : chacune de ces actions garde la version remplacée dans
  `panels/<ch>/<page>/<case>.history/` (vignette + `index.json`, 10 par case,
  la plus vieille part). Le bandeau sous la case les montre ; un clic la
  remet, la version courante est archivée à son tour : rien n'est jamais
  perdu. **↩ Previous** = raccourci vers la plus récente.

### La Bible visuelle — personnages, décors, style et mood

**📖 Bible** (en-tête) est la seule source des prompts image :

- **Personnages** et **Décors** sont des fiches : `@Nom` (ce qu'on tape dans
  les cases), description visuelle en anglais (ce que le moteur dessine pour
  `@Nom`, l'apparence seulement, ✨ Improve via Ollama), LoRA de la fiche,
  négatif, et **images de référence** (import, ou « utiliser cette case » pour
  faire d'un dessin réussi la référence ; les refs partent aux moteurs omni).
  Chaque fiche liste les cases où elle apparaît ; **🎨 Redraw its panels** les
  redessine après un changement (les anciens dessins restent dans l'historique).
  Renommer une fiche réécrit tous les `@AncienNom` des cases et des locuteurs ;
  supprimer une fiche encore utilisée demande confirmation. Sous le texte de
  chaque case, des puces insèrent `@Nom` au curseur, et 📌 ajoute le dessin
  courant comme référence d'une fiche.
- **Style & mood** : suffixe de style du livre (presets ou texte libre),
  négatif global, LoRA du livre (recherche dans la liste du moteur), et le
  **mood** (palette, lumière, époque, météo) ajouté après le style, qu'un
  chapitre peut surcharger (chapitre 1 lumineux, chapitre 3 nocturne). Un
  exemple en direct montre ce que le moteur recevra ; **🎨 Redraw the whole
  book** applique un nouveau look.

L'histoire (synopsis, arcs) n'entre jamais dans les prompts image ; le mode
fun remplit le casting et un mood depuis le concept.

## Tests

```bat
.venv\Scripts\python.exe tools\run_tests.py
```

Sans GPU ni serveur.
