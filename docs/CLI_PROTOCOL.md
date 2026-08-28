# Protocole CLI de la famille crispz (v1)

> 2026-08-28 — validé et implémenté dans crispz-studio (`cz_protocol.py` +
> `czp.bat`, endpoints `cli_caps`/`cli_gen`), porté aux forks. Client de
> référence : `c2c_engines.py` (comics2crispz). Écarts d'implémentation par
> rapport au plan initial : voir §9.
> Objectif : un contrat unique pour que n'importe quelle interface (Comic Studio,
> accordéons Gradio, un agent, un script de nuit) dialogue avec n'importe quel
> outil de la famille — crispz (upscale), crispz-studio (Z-Image), crispz-qwen-edit
> (Qwen-Image-Edit) — sans savoir qui génère.

## 1. Décisions de principe

1. **Le CLI est le contrat, pas le processus.** L'unité d'échange est un spec JSON
   en entrée et un JSON en sortie. Un appel CLI ne recharge JAMAIS un modèle si une
   instance tourne déjà : il **route** vers elle (gradio_client, 127.0.0.1:7860) et
   la queue Gradio sérialise le GPU. Exécution directe (chemin froid) seulement si
   aucune instance ne répond — batch de nuit, CI.
2. **Un seul GPU, une seule file.** Deux interfaces ne parlent jamais au GPU en
   parallèle : tout passe par l'instance qui tourne, ou par UN processus batch.
3. **Dégradation annoncée, jamais silencieuse** (règle maison). Un outil qui ne
   sait pas faire (pas de LoRA, pas de refs) le dit dans `caps` et l'appelant
   décide ; il ne tronque pas en silence.
4. **Le protocole vit dans crispz-studio** (l'upstream) et redescend dans
   crispz-qwen-edit par merge, comme le reste. crispz (upscaler) l'adopte à son
   rythme sur son périmètre (upscale only).

## 2. Commandes

Toutes prennent `--json` (sortie machine sur stdout, une seule ligne JSON finale)
et `--spec <fichier|->` (`-` = stdin). Sans `--json` : sortie humaine actuelle,
inchangée (compat totale).

| Commande | Rôle | Outils qui l'exposent |
|---|---|---|
| `caps` | annonce les capacités de l'outil | tous |
| `gen` | txt2img depuis un spec | studio, qwen-edit |
| `edit` | édition d'image (img+prompt→img) | qwen-edit (studio si Omni configuré) |
| `upscale` | upscale/refine d'une image | crispz, studio |
| `comic-render` | rend les cases d'un projet BD via `gen` | studio (existe : `--comic-render`) |

Le nom d'entrée reste `cz.bat` / `cz_cli.py` par outil ; pas de dispatcheur
global en v1 (chaque repo garde son CLI, même dialecte).

## 3. Le spec (entrée)

Aligné sur la sortie de `cz_comic.resolve_panel` — c'est déjà le contrat interne.

```json
{
  "protocol": 1,
  "op": "gen",
  "prompt": "…",
  "negative": "…",
  "width": 1024, "height": 1344,
  "seed": -1,
  "steps": null,
  "guidance": null,
  "refs": ["refs/lea.png"],
  "loras": ["style-encre.safetensors:0.8"],
  "model": null,
  "input": null,
  "out_dir": "out/",
  "count": 1
}
```

Règles :
- `protocol` obligatoire. Version inconnue → erreur propre, code 3.
- Champs `null`/absents = réglages courants de l'outil (config/UI). Le spec ne
  redit que ce qu'il impose.
- `refs` et chemins relatifs : POSIX, résolus par rapport au dossier du spec
  (même règle que project.json).
- `input` : image d'entrée (`edit`, `upscale`).
- Un champ inconnu est **ignoré avec warning** dans la sortie (`warnings[]`),
  jamais une erreur : un spec écrit pour qwen-edit doit passer chez studio.

## 4. La sortie

```json
{
  "ok": true,
  "protocol": 1,
  "tool": "crispz-studio", "version": "1.11.2",
  "route": "remote",
  "images": ["out/2026-08-28/img_001.png"],
  "seed_used": 123456789,
  "timings": {"total_s": 14.2, "load_s": 0.0},
  "warnings": ["field 'foo' ignored"]
}
```

Erreur : `{"ok": false, "error": "…", "code": "no_model"}` + code retour.

Codes retour : `0` ok · `1` erreur d'exécution (génération) · `2` spec invalide ·
`3` protocole/op non supporté · `4` aucune route (pas d'instance ET pas de
modèle local chargeable).

`route` : `"remote"` (via l'instance) ou `"local"` (chargé dans ce processus) —
diagnostic indispensable quand les temps s'envolent.

## 5. `caps` — capacités

```json
{
  "ok": true, "protocol": 1, "tool": "crispz-qwen-edit",
  "ops": ["gen", "edit", "upscale", "caps"],
  "supports": {"loras": true, "refs": false, "seed": true,
               "negative": true, "arbitrary_size": true},
  "models": ["…"],
  "instance": {"running": true, "url": "http://127.0.0.1:7861"}
}
```

`caps` n'exige ni GPU ni modèle chargé : lecture de config + probe de l'instance,
< 2 s. C'est l'appel que Comic Studio fera pour peupler un futur menu « moteur ».

## 6. Routage

1. `--remote <url>` : force cette instance (erreur 4 si injoignable).
2. `--local` : force l'exécution dans ce processus (batch de nuit assumé).
3. Défaut : probe de l'URL de l'outil (config `server_port`, défaut 7860 studio /
   7861 qwen-edit — **à valider**) ; instance trouvée → gradio_client sur un
   endpoint `api_name="cli_gen"` (miroir exact du chemin local) ; sinon local.
4. Le GPU partagé est réglé par construction : la route remote passe dans la
   queue de l'instance, derrière les générations de l'utilisateur.

## 7. Plan d'implémentation (dans l'ordre)

1. **`--json` + `--spec` sur cz_cli** (studio) : parse, validation, sortie JSON,
   codes retour. Zéro nouveau chemin de génération — on emballe l'existant.
2. **`caps`** (studio) : trivial une fois 1 fait.
3. **Endpoint `cli_gen`** dans cz_ui + routage gradio_client dans le CLI.
4. Merge dans crispz-qwen-edit ; son adaptateur `edit` (le pipeline y est déjà).
5. crispz (upscaler) : `upscale` + `caps` sur son périmètre.

Chaque étape : tests dans tests/ (sans GPU : spec→spec, codes retour, probe
mockée), config documentée dans config-sample.txt, README.

## 8. Décisions actées (v1)

- **Une image par appel**, l'appelant boucle (`count>1` → warning + 1).
- **Pas de port par outil** : chaque outil lit `cli_protocol.instance_url`
  (défaut 7860) et la réponse de `caps`/`gen` porte `tool`/`version` — c'est
  la réponse qui identifie QUI tourne, pas le port. Un seul outil UI à la
  fois sur un port ; le champ `tool` permet à l'appelant de vérifier.
- Progression : v1 = appel bloquant. `--progress-file` viendra avec le
  dashboard Production de comics2crispz si besoin.
- Auth : hors périmètre v1 (tout est localhost).
- Sur la route **remote**, `spec.model` est **refusé avec warning** : on ne
  change jamais le modèle de l'instance de l'utilisateur sous ses pieds.
- `refs` : acceptées mais non appliquées en v1 (warning) ; `supports.refs`
  reste `false` tant que ce n'est pas branché (honnêteté avant tout).

## 9. Écarts d'implémentation vs plan initial

- Le protocole vit dans un module dédié **`cz_protocol.py`** + `czp.bat`
  (entrée TOUJOURS JSON, imports légers — pas de torch avant le chemin
  local) au lieu de flags `--json`/`--spec` greffés sur `cz_cli.py` :
  `python app.py` importe tout le stack dès le départ, un `caps` en <2 s y
  était impossible. `cz_cli` est inchangé, compat totale.
- Le routage remote parle aux endpoints Gradio en **HTTP direct (urllib)**,
  même mécanique que les SPA Asset Browser / Comic Studio — pas de
  dépendance gradio_client.
- `edit`/`upscale` : réservés dans la CLI (code retour 3 explicite),
  implémentation à venir (qwen-edit d'abord).
