<div align="center">
  <h1>🎬 Agent Editor</h1>
  <p><strong>Un espace local pour retrouver, annoter et préparer vos clips vidéo.</strong></p>
  <p>Plugin pour Hermes Dashboard — conçu autour de vos enregistrements <code>Waiting</code> et de vos clips <code>Finish</code>.</p>
</div>

---

## En bref

**Agent Editor** rassemble vos rediffusions en attente, leurs repères de montage et les clips déjà exportés dans une interface locale. Il aide à organiser le travail éditorial sans modifier les enregistrements source ni envoyer vos vidéos à un service externe.

> [!IMPORTANT]
> Le plugin enregistre des repères et des brouillons; il **ne génère, ne rend, ne transcode et ne publie pas** de clips. Les vidéos affichées dans `Finish` sont des fichiers déjà présents dans votre espace média.

## Fonctionnalités

- **Bibliothèque `Waiting`** — inventaire récursif des MP4, avec miniatures générées à la demande par FFmpeg.
- **Lecture progressive** — lecture et recherche dans les vidéos au moyen de requêtes HTTP Range, sans charger la VOD complète en mémoire.
- **Espace de travail par rediffusion** — repères horodatés, notes, brouillons Short/Vidéo et aperçu des clips MP4 correspondants déjà dans `Finish`.
- **Références éditoriales** — liens HTTPS YouTube et TikTok, notes et liste de points d’observation (accroche, rythme, cadrage, sous-titres, audio et payoff). Les liens sont enregistrés, pas visités ni téléchargés.
- **Skills locaux** — création de skills personnalisés conservés dans le dossier du plugin.
- **Médias protégés** — les routes API utilisent l’authentification normale du Dashboard; les chemins de fichiers sont confinés aux dossiers prévus et les liens symboliques sont refusés.

## Installation

### Prérequis

- Une installation Hermes avec prise en charge des plugins et de Hermes Dashboard.
- Python du gateway avec les dépendances FastAPI/Starlette utilisées par le Dashboard.
- **FFmpeg** dans le `PATH` pour générer les miniatures `Waiting`.
- **Node.js** pour lancer les tests JavaScript.

### Installer le plugin

1. Copiez le dossier `video-agent` dans le répertoire des plugins de votre environnement Hermes :

   ```text
   $HERMES_HOME/plugins/video-agent/
   ```

   Si `HERMES_HOME` n’est pas défini, Hermes utilise normalement `~/.hermes`.
2. Placez vos enregistrements dans les dossiers suivants :

   ```text
   $HERMES_HOME/inbox/stream-test/Waiting/
   $HERMES_HOME/inbox/stream-test/Finish/
   ```

3. Redémarrez le gateway ou le service Hermes qui charge les plugins, puis actualisez le Dashboard.

`Waiting` est parcouru récursivement. `Finish` est présenté comme bibliothèque secondaire; les clips sont associés à une source par leur nom de fichier (nom de base suivi de `_`), et non par des métadonnées de projet.

## Données et confidentialité

- Les fichiers vidéo source restent dans `inbox/stream-test/`; le plugin ne les modifie pas.
- Les miniatures sont produites à la demande, conservées dans un cache mémoire borné et ne sont pas écrites sur disque.
- Les brouillons, repères et références sont enregistrés sous `$HERMES_HOME/state/waiting-studio/`. Ce nom de répertoire historique est conservé afin de retrouver les données existantes malgré le renommage du plugin.
- Les copies HVC1 facultatives utilisées pour la compatibilité de lecture se trouvent dans `$HERMES_HOME/state/waiting-studio/compat/`, séparées des originaux.
- Les skills créés par l’interface sont enregistrés dans `skills/<nom>/SKILL.md` sous le dossier `video-agent`.
- Les routes API et les flux média restent protégés par l’authentification du Dashboard. En mode local à jeton, le lecteur transmet le jeton en mémoire et le service worker l’ajoute uniquement aux requêtes média autorisées; il n’est pas placé dans l’URL ni écrit sur disque.

## Routes API

Les routes sont montées sous `/api/plugins/video-agent` et restent protégées par l’authentification du Dashboard.

| Méthode | Route | Rôle |
|---|---|---|
| `GET` | `/videos` | Inventaire des dossiers `Waiting` et `Finish`. |
| `GET` | `/videos/waiting/{filename}` | Lecture d’un MP4 de `Waiting`; prise en charge des requêtes Range et du choix de codec. |
| `GET` | `/videos/waiting-thumbnails/{filename}` | Miniature JPEG produite à la demande. |
| `GET` | `/videos/finish/{filename}` | Lecture d’un MP4 directement dans `Finish`. |
| `GET`, `POST` | `/skills` | Lister ou créer un skill local validé. |
| `GET` | `/references` | Lister les références YouTube/TikTok. |
| `POST` | `/references` | Ajouter une référence et ses notes d’observation. |
| `DELETE` | `/references/{reference_id}` | Supprimer une référence. |
| `GET` | `/content/{filename}` | Charger l’espace de travail d’une rediffusion et ses clips associés. |
| `POST` | `/content/{filename}/moments` | Enregistrer un repère horodaté. |
| `POST` | `/content/{filename}/items` | Enregistrer un brouillon Short ou Vidéo. |

Les chemins sont relatifs aux dossiers média autorisés; les chemins arbitraires et les liens symboliques sont rejetés. Les requêtes de lecture prennent en charge les plages d’octets pour le seek.

## Limites actuelles

- Les brouillons sont des notes éditoriales; aucun fichier vidéo n’est créé ou rendu.
- Les références YouTube/TikTok ne sont ni analysées ni téléchargées.
- La publication sur YouTube, TikTok ou une autre plateforme n’est pas effectuée.
- Les miniatures nécessitent FFmpeg. Sans FFmpeg, l’inventaire reste disponible, mais les aperçus peuvent échouer.

## Vérification locale

Depuis la racine du plugin, avec l’interpréteur Python du gateway (FastAPI/Starlette requis pour exécuter les tests HTTP complets) :

```bash
python -m unittest discover -s tests -v
python -m py_compile dashboard/plugin_api.py tests/test_plugin_api.py
node --check dashboard/dist/index.js
node --check dashboard/media-service-worker.js
node --check dashboard/media-player.js
node tests/test_dashboard_bundle.js
node tests/test_media_service_worker.js
node tests/test_media_player_bridge.js
python -m json.tool dashboard/manifest.json
python -m json.tool plugin.json
```

## Structure

```text
video-agent/
├── plugin.json                  # Manifeste du plugin portable
├── dashboard/
│   ├── manifest.json            # Onglet Agent Editor
│   ├── plugin_api.py            # Routes protégées et accès média
│   ├── dist/                    # Interface du Dashboard
│   └── media-player.*           # Lecteur progressif et service worker
└── tests/                       # Tests API, interface et lecteur
```
