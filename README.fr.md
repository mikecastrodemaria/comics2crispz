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

## Ce que ça produit

📖 **[Four Billion Reasons — lire le PDF](docs/four-billion-reasons.pdf)** — 6 pages,
dessinées de bout en bout par la famille crispz à travers le protocole CLI. Aucune case
retouchée à la main.

| | |
|---|---|
| <img src="docs/img/four-billion-reasons-cover.jpg" width="330" alt="Couverture : un développeur à son clavier, un petit robot rond posé sur le bureau, une énorme machine rouillée dressée derrière lui"> | <img src="docs/img/four-billion-reasons-bugs.jpg" width="420" alt="Une page de neuf cases : le développeur tourne une molette de guidance, compare deux portraits identiques, lit une trace d'erreur"> |
| La couverture. | Page 3 — neuf cases, personnage récurrent, bulles placées par l'app. |

L'histoire est ce qui s'est réellement passé en construisant les moteurs que cette app
pilote : une molette de guidance qui n'est que décorative sur un modèle distillé,
`expected 3072, got 4096` quand une LoRA 9B rencontre une base 4B, onze checkpoints
écartés par un filtre resté réglé sur le modèle précédent. Le livre est le rapport de
bug.

Projet source : `books/klein-genesis/`, construit par
[`tools/make_klein_genesis.py`](tools/make_klein_genesis.py) — le script écrit le
scénario, donc le livre se refait d'une seule commande. Le plus ancien
📖 **[Making of comics2crispz](docs/the-making-of.pdf)** suit pas à pas le tutoriel.

---

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

Un second livre construit pareil, avec un personnage récurrent sur les six pages :
📖 **[Four Billion Reasons](docs/four-billion-reasons.pdf)** (projet
`books/klein-genesis/`).

### Travaux de fond — progression, pause, stop

La génération du livre entier, « Redraw its panels » et « Redraw the whole
book » tournent en fond. Une barre sous l'en-tête les suit partout : quoi,
page/case x/y, cases dessinées, temps écoulé et estimation du reste, les
derniers éléments avec durée et seed, avertissements à la fin. **⏸ Pause**
prend effet après l'image en cours (rien n'est interrompu à mi-rendu),
**▶ Resume** reprend là où ça s'est arrêté, **⏹ Stop** termine après
l'élément en cours. Les anciens dessins restent dans l'historique.

Clavier en vue Page : **← →** (ou ↑ ↓, PageUp/PageDown) planche précédente /
suivante dans l'ordre du livre (en manga, ← → suivent le sens de lecture) ;
**Esc** retourne au chemin de fer.

### Le format du livre

Le wizard demande le **format du livre** : Franco-Belge 24×32 cm, A4, comic
US 17×26 cm, manga 13×18 cm (coche le sens de lecture droite → gauche),
roman graphique 17×24 cm, album carré 21×21 cm, à l'italienne 29,7×21 cm
(tous en 300 dpi, marges d'impression et gouttières de 5 mm), ou Web /
Webtoon pour l'écran. Les cases sont dessinées à ~1 mégapixel quel que soit
le format (l'export print les agrandit) ; le format fixe les proportions de
la planche et la résolution du lettrage. Le texte des bulles est dimensionné
**par rapport à la planche**, même proportion à l'écran et à l'impression.
Gabarits de `splash` au gaufrier 4×3 `12-grid`.

### Le livre en texte — onglet Script (.czs)

**Script** (onglet d'en-tête) montre tout le livre, ou un chapitre, en texte
au format `.czs`, éditable sur place ou dans n'importe quel éditeur :
`=== ch01 : Nom ===` ouvre un chapitre (`> synopsis`, `~ mood: ambiance`),
`--- page 3-hero` une planche (rôles cover/title/story/back, `seed=N`),
`[pn1] description` une case, puis les répliques (`Nom: texte`, `CAP:`,
`SFX:`, `Nom (think):`). **✔ Check** simule l'import et fait un rapport ;
**⬇ Import** l'applique : chapitres par id, planches par position, cases par
id, les cases dont le texte n'a pas changé gardent leur dessin, les bulles
punaisées restent en place, tout ce qui est retiré est cité, une erreur de
syntaxe refuse tout l'import et pointe la ligne. **💾 .czs** télécharge le
texte ; une copie `script.czs` est gardée dans le dossier du livre.

### Le livre en un fichier — le bundle .md

Dans l'onglet Script, la portée **📦 full bundle (.md)** montre tout ce qui
définit le livre dans un seul Markdown : `## Settings` (nom, page, moteur et
modèle), `## Style` (suffixe, négatif, LoRA, mood, police des bulles),
`## Story bible` (concept, langue), `## Visual bible` (une carte
`### @Nom (character|setting)` par fiche : description, chemins des
références, LoRA, négatif) et `## Script` (le .czs). Lisible comme un
document, exact comme donnée (blocs JSON) : export → import → export est
identique. **💾 Download** l'enregistre, **📂 Open file…** charge un `.md` (ou
`.czs`) dans l'éditeur, **✔ Check** simule l'import section par section,
**⬇ Import** l'applique : une section absente du fichier n'est pas touchée,
les fiches/planches/cases retirées sont citées, une image de référence
absente est signalée par son chemin (les images sont listées, pas incluses :
le bundle est la recette, `panels/` et `pages/` sont le résultat). Une copie
`book.md` est gardée dans le dossier du livre après chaque import.

### Formes de case — bords obliques, coins arrondis, chevauchement

Une case est par défaut le rectangle de sa cellule. Dans le panneau de la
case, **✎ Edit corners** affiche des poignées sur la planche : glisser un
coin, cliquer le point bleu au milieu d'un côté pour en ajouter un,
double-clic sur un coin pour le retirer (3 minimum), arrondir le coin
sélectionné avec le curseur ; **⬆ Front / ⬇ Back** empile la case au-dessus
ou au-dessous de ses voisines (une case large peut passer sous deux petites,
avec un halo de gouttière). Le dessin est généré au ratio de la boîte de la
forme puis découpé à la composition ; le contour suit le polygone.
**▭ Reset** revient au rectangle. Dans le script : `shape=fx,fy[,r] …`
(fractions de page, `r` = arrondi 0–1) et `z=N` ; `shape=rect` remet le
rectangle.
**🧍 Break the frame** (panneau de la case) : le sujet de la case est détouré
de son fond (rembg, dépendance optionnelle, quelques secondes sur CPU, modèle
téléchargé une fois) et dessiné PAR-DESSUS les cases voisines pendant que le
fond reste dans le cadre. *Zoom* cadre le dessin plus serré pour que le sujet
déborde davantage, *Outline* ajoute un liseré couleur de page. Le détourage
se recalcule quand le dessin change ; s'il prend trop ou pas assez, peins la
zone avec l'outil Paint puis ➕ add painted / ➖ remove painted. Les bulles
restent au-dessus.

**Gabarits obliques** (fenêtre Layout › ✂) : diagonale, trio oblique,
escalier, éclat manga, case large sous deux inserts, insert arrondi, grille
penchée ; appliqués sur une planche du même nombre de cases, puis ajustables
coin par coin.

### Storyboard d'abord, rendu final ensuite

**Production › 🎞 Storyboard missing** croque chaque case manquante en
quelques secondes : 4 steps, demi-taille, style crayonné gris à la place du
style du livre, sans LoRA, références ni détaillers. Les bulles sont
lettrées comme dans le final : on vérifie découpage, cadrage, sens de
lecture et place du texte sur tout le livre avant de payer le vrai rendu.
Les cases croquées sont marquées « storyboard sketch » ; **Production ›
storyboard sketches › 🎨 Final render these** les redessine avec le vrai
style (les croquis restent dans l'historique). Un croquis valide la
structure, pas l'image exacte : le rendu final est un nouveau dessin.


### Bulles — ajouter, masquer, police

Sous la zone de dialogue d'une case, **➕ balloon** ajoute une ligne
(`Nom: texte` ; `CAP:` cartouche, `SFX:` bruit, `Nom (think):` pensée), 💾 Save
recompose. Un clic sur une bulle de la planche change son type, sa forme, sa
taille, sa **police** et sa **visibilité** : une bulle masquée reste dans le
scénario (affichée `Nom (hidden): …` dans la zone de dialogue) mais n'est pas
lettrée, pour un titre que le modèle a déjà dessiné dans l'image ou une case
muette. La police par défaut du livre est dans 📖 Bible › Style ; déposez des
polices BD `.ttf`/`.otf` dans un dossier `fonts/` à côté de l'app ou dans le
livre pour les choisir.
**Outline** règle l'épaisseur du contour (ou du trait noir des lettres SFX /
titre) ; tapez `
` dans le texte d'une bulle pour forcer un retour à la ligne
(`La fille des
ruines fleuries`).

### Corriger une case — Régénérer, Retoucher, Variation, Versions

Cliquer une case sur la planche ouvre son panneau :

- **🎲 Regenerate** redessine cette case seule depuis son texte (et sa seed).
- **✏️ Edit by instruction** garde le dessin et change ce qu'on demande
  (« add heavy rain, keep the ink style ») via le modèle d'édition du moteur
  (crispz-qwen-edit, ou crispz-studio avec un modèle omni). Un moteur sans
  modèle d'édition le dit, il ne devine pas.
- **🔁 Variation** redessine la case À PARTIR de son image avec une force
  (0,3 = même composition légèrement redessinée, 0,6 = vraie variation).
  Tous les moteurs dotés d'un pipeline img2img, donc pas Krea 2 (op `upscale`
  du protocole, facteur 1 = img2img).
- **🖌 Inpaint** redessine SEULEMENT la zone peinte sur la case (pinceau sur
  la planche, gomme, effacer), d'après une description LOCALE de ce qui doit
  y apparaître (« a black cat sitting » ; vide = remplissage cohérent),
  jamais le prompt de scène sur un fragment. Force 1,0 = zone entièrement
  redessinée. Tous les moteurs dotés d'un pipeline inpaint, donc pas Krea 2
  (op `inpaint`) ; le masque reste en `<case>.mask.png`.
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

### Production — le tableau de suivi

**Production** (onglet d'en-tête) compte ce qui manque au livre : cases
dessinées / à dessiner / sans texte / avec un `@Nom` inconnu / verrouillées,
planches composées / jamais composées / à recomposer, bulles posées sur un
visage, et cases dessinées avec un **style plus ancien** que le style courant
(chaque dessin garde une signature : suffixe, négatif, LoRA, moteur, mood).
Chaque compteur est un filtre : un clic liste les planches, on y saute, et on
agit sur exactement cette liste (dessiner, redessiner, composer). Rien n'est
régénéré automatiquement. En vue Page, **🔒 lock** sur une case la protège de
tout « generate missing » / « redraw all » ; seul son propre Regenerate y touche.

## Tests

```bat
.venv\Scripts\python.exe tools\run_tests.py
```

Sans GPU ni serveur.
