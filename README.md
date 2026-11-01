<div align="center">
  <h1>🎬 Agent Editor</h1>
  <p><strong>Un espace local pour retrouver, annoter et préparer vos clips vidéo.</strong></p>
  <p>Plugin pour Hermes Desktop et Dashboard — conçu autour de vos enregistrements <code>Waiting</code> et de vos clips <code>Finish</code>.</p>
</div>

---

## En bref

**Agent Editor** rassemble vos rediffusions en attente, leurs repères de montage et les clips déjà exportés dans une interface locale. Il aide à organiser le travail éditorial sans modifier les enregistrements source ni envoyer vos vidéos à un service externe.

> [!IMPORTANT]
> Le plugin enregistre des repères et des brouillons; il **ne génère, ne rend, ne transcode et ne publie pas** de clips. Les vidéos affichées dans `Finish` sont des fichiers déjà présents dans votre espace média.

## Fonctionnalités

- **Bibliothèque `Waiting`** — inventaire récursif des MP4, avec miniatures générées à la demande par FFmpeg.
- **Lecture progressive dans Hermes Desktop** — lecteur intégré dans `Waiting`, `References` et `Finish`; la vidéo garde les contrôles et le seek natifs.
- **Espace de travail par rediffusion** — repères horodatés, notes, brouillons Short/Vidéo et aperçu des clips MP4 correspondants déjà dans `Finish`.
- **Références éditoriales** — liens HTTPS YouTube et TikTok, notes et liste de points d’observation (accroche, rythme, cadrage, sous-titres, audio et payoff). À l’ajout, `yt-dlp` télécharge automatiquement une copie MP4 gérée sous `References/downloads/`; l’état et les erreurs sont affichés, les échecs peuvent être relancés, et retirer la référence supprime uniquement cette copie gérée.
- **Skills locaux** — création de skills personnalisés conservés dans le dossier du plugin.
- **Médias protégés** — le backend du plugin ne retourne qu’un chemin absolu MP4 résolu dans les dossiers allowlistés; l’URL `hermes-media://remote` utilise ensuite le protocole natif Hermes pour l’authentification Gateway et les requêtes Range/seek. Aucun jeton ou ticket n’est placé dans l’URL. Les chemins relatifs arbitraires et les liens symboliques sont refusés. Dans Waiting, le lecteur préfère le dérivé HVC1 existant quand il est valide, sinon il sert la source sans la modifier ni la transcoder.
- **Section Hermes Desktop « Agent Editor »** — bibliothèque `Waiting` / `References` / `Finish`, lecture MP4 intégrée, liens YouTube/TikTok, repères capturés depuis la position du lecteur et accessibles par seek.
- **Sources en lecture seule** — l’interface ne déplace, ne transcode et ne modifie aucun MP4 source.

## Installation

### Prérequis

- Une installation Hermes avec prise en charge des plugins et de Hermes Dashboard.
- Python du gateway avec les dépendances FastAPI/Starlette utilisées par le Dashboard.
- **FFmpeg** dans le `PATH` pour générer les miniatures `Waiting` et fusionner les pistes lorsque l’extracteur le requiert.
- `yt-dlp` est déclaré dans `plugin.yaml` (`yt-dlp==2026.10.26`) et installé par le gestionnaire de dépendances Hermes. Pour un clone manuel, installez-le dans le même environnement Python que le Gateway.
- **Node.js** pour lancer les tests JavaScript.

### Installer le plugin

1. Copiez le dossier `video-agent` dans le répertoire des plugins de votre environnement Hermes :

   ```text
   $HERMES_HOME/plugins/video-agent/
   ```

   Si `HERMES_HOME` n’est pas défini, Hermes utilise normalement `~/.hermes`.
2. Préparez le point de montage média dans le dossier du plugin :

   ```text
   $HERMES_HOME/plugins/video-agent/workspace/
   ├── Waiting/       # rediffusions à traiter
   ├── References/    # MP4 locaux d’inspiration + links.json pour les liens/notes
   └── Finish/        # clips terminés
   ```

   Montez votre volume/dataset sur `workspace/` et assurez-vous que le processus du gateway peut lire les vidéos et écrire dans `References/links.json`. `Waiting`, `References` et `Finish` acceptent des sous-dossiers et sont parcourus récursivement.
3. Le plugin active ce workspace quand les trois dossiers existent. Jusqu’à ce qu’il soit prêt, il conserve l’ancien chemin `$HERMES_HOME/inbox/stream-test/` comme repli; **aucun fichier existant n’est déplacé**. Un chemin absolu différent peut être fourni par `VIDEO_AGENT_WORKSPACE`.
4. Démarrez ou rechargez le service Hermes qui héberge le plugin, puis actualisez le Dashboard.
5. Pour installer le paquet unifié depuis l’installateur de plugins Hermes Desktop, l’activation du paquet est incluse par défaut et son compagnon **Agent Editor** s’active avec lui. Une copie manuelle dans `desktop-plugins/` doit être activée dans **Settings → Plugins**. Si la page n’apparaît pas après l’installation ou une mise à jour, utilisez **Reload desktop plugins** dans la palette (`⌘K`).

Seul le marqueur `workspace/.gitkeep` est versionné; le reste de `workspace/` est ignoré, donc ni les MP4 ni les données montées ne seront ajoutés au dépôt public. Les liens/notes sont enregistrés dans `References/links.json` quand le workspace est actif; les références existantes dans `$HERMES_HOME/state/waiting-studio/references.json` restent lisibles jusqu’à la première modification dans le workspace. Les brouillons, repères et dérivés de compatibilité restent pour l’instant sous `$HERMES_HOME/state/waiting-studio/` afin d’éviter une migration implicite.

## Versionnement et mises à jour

Le plugin porte actuellement la version `0.5.0` dans ses manifestes :

- `plugin.yaml` — identité du paquet Agent Plugin et du compagnon Desktop.
- `plugin.json` — version du paquet portable Agent Plugins.
- `dashboard/manifest.json` — version facultative du tab Dashboard.

On gardera ces valeurs identiques et on suivra SemVer (`MAJOR.MINOR.PATCH`) : correction compatible = patch, nouvelle fonctionnalité compatible = minor, changement incompatible = major. Un tag Git `vX.Y.Z` servira à marquer une release humaine et à retrouver facilement son commit.

Le numéro de version **ne déclenche pas** la mise à jour. La révision Git est ce qui détermine le code installé : un push sur `main` publie le nouveau code, mais ne met pas à jour à lui seul une copie déjà installée. Pour une installation Git suivie par le gestionnaire Hermes, `hermes plugins check-updates` vérifie l’état sans modifier les fichiers, et `hermes plugins update video-agent` applique l’update. Les installs Git non épinglées sont comparées au `HEAD` distant; une install épinglée à un SHA reste épinglée jusqu’à ce qu’on installe explicitement un nouveau SHA. Un clone créé à la main doit d’abord être adopté par le gestionnaire (`hermes plugins adopt video-agent`) pour être suivi. Ces commandes dépendent de la version de Hermes installée; elles ne sont pas lancées depuis ce dépôt.

`video-agent` n’est pas présent dans le catalogue officiel Hermes : son mode d’update, si on utilise le gestionnaire, est celui d’un plugin Git personnalisé. Après une mise à jour, il faut rafraîchir le Dashboard; les changements backend dépendent du rechargement du processus qui héberge le plugin. La version `0.5.0` conserve le protocole média natif déjà consommé par le Desktop pour ses médias distants et ajoute le téléchargement asynchrone des références via `yt-dlp`; elle n’exige pas `ctx.media.resolveUrl` ni un changement du cœur du Desktop/Gateway. La lecture dépend du fait que le chemin soit autorisé par la politique normale de fichiers du Gateway.

## Données et confidentialité

- Les fichiers `Waiting`, `References` et `Finish` sont lus depuis `workspace/` quand les trois dossiers du workspace sont présents; sinon, l’ancien dossier `inbox/stream-test/` reste utilisé.
- Les MP4 sources sont en lecture seule pour le plugin. Les sous-dossiers sont inventoriés sans suivre les liens symboliques.
- Les liens et notes YouTube/TikTok sont enregistrés dans `workspace/References/links.json` lorsque le montage est actif; à l’ajout, `yt-dlp` télécharge une copie dans `References/downloads/`. Les fichiers sources ne sont ni modifiés ni déplacés; le plugin ne publie rien.
- Les miniatures sont produites à la demande, conservées dans un cache mémoire borné et ne sont pas écrites sur disque.
- Les brouillons, les repères et les copies HVC1 facultatives restent sous `$HERMES_HOME/state/waiting-studio/` (copies de compatibilité dans `compat/`); aucune donnée n’est déplacée lors de l’activation du mount.
- Les skills créés par l’interface sont enregistrés dans `skills/<nom>/SKILL.md` sous le dossier `video-agent`.
- Agent Editor appelle `/video-media-source` avec `ctx.rest`; cette route n’accepte qu’un dossier et un nom MP4 allowlistés et retourne le chemin canonique validé avec `no-store`/`no-referrer`. Le lecteur encode ce chemin dans `hermes-media://remote/...`; le processus principal du Desktop transmet l’authentification et Range à `/api/files/stream`. Le jeton de session n’est ni donné au JavaScript du plugin ni inclus dans l’URL. Les endpoints de tickets historiques restent disponibles pour compatibilité; le lecteur Agent Editor n’en dépend plus.
- Si le fichier sélectionné dépasse le plafond courant du Gateway, le backend du plugin relève sa constante privée jusqu’à la taille du MP4, au maximum 30 Gio. Ce changement est **global au processus** (autres lectures/téléversements gérés inclus) et reste actif jusqu’au redémarrage du Gateway. Il nécessite l’isolation `in_process`; une lecture complète par `/api/files/read` peut consommer jusqu’à plusieurs dizaines de Gio de mémoire. Cette intégration dépend d’un détail interne Hermes et doit être retestée après chaque mise à jour du Gateway.

## Routes API

Les routes sont montées sous `/api/plugins/video-agent`. Agent Editor résout un chemin via `ctx.rest` et le lit par le protocole natif Hermes, qui applique l’authentification et la politique de fichiers du Gateway et relaie Range. Les endpoints de tickets sont conservés pour compatibilité, mais ne servent plus au lecteur du Desktop.

| Méthode | Route | Rôle |
|---|---|---|
| `GET` | `/videos` | Inventaire des dossiers `Waiting`, `References` et `Finish`. |
| `GET` | `/videos/waiting/{filename}` | Lecture d’un MP4 de `Waiting`; prise en charge des requêtes Range et du choix de codec. |
| `GET` | `/videos/waiting-thumbnails/{filename}` | Miniature JPEG produite à la demande. |
| `GET` | `/videos/references/{filename}` | Lecture d’un MP4 local de référence; prise en charge des requêtes Range. |
| `GET` | `/videos/finish/{filename}` | Lecture d’un MP4 de `Finish`, y compris dans un sous-dossier. |
| `POST` | `/video-media-source` | Retourner le chemin d’un MP4 allowlisté à l’Agent Editor; le Desktop le lit par `hermes-media://remote`. |
| `POST` | `/video-tickets` | Émettre un ticket de lecture temporaire pour un MP4 allowlisté. |
| `POST` | `/video-tickets/renew` | Renouveler le ticket courant (`ticketId` dans le corps JSON). |
| `POST` | `/video-tickets/revoke` | Révoquer le ticket courant (`ticketId` dans le corps JSON). |
| `GET` | `/videos/stream?ticket=…` | Flux Range d’un seul MP4; la query exige le ticket temporaire, pas le jeton de session Hermes. |
| `GET`, `POST` | `/skills` | Lister ou créer un skill local validé. |
| `GET` | `/references` | Lister les références YouTube/TikTok. |
| `POST` | `/references` | Ajouter une référence et ses notes d’observation; démarrer son téléchargement en arrière-plan. |
| `POST` | `/references/{reference_id}/download` | (Re)mettre en file le téléchargement d’une copie locale. |
| `DELETE` | `/references/{reference_id}` | Supprimer la référence et uniquement sa copie locale gérée. |
| `GET` | `/content/{filename}` | Charger l’espace de travail d’une rediffusion et ses clips associés. |
| `POST` | `/content/{filename}/moments` | Enregistrer un repère horodaté. |
| `POST` | `/content/{filename}/items` | Enregistrer un brouillon Short ou Vidéo. |

Les chemins sont relatifs aux dossiers média autorisés; les chemins arbitraires et les liens symboliques sont rejetés. Les requêtes de lecture prennent en charge les plages d’octets pour le seek.

## Limites actuelles

- Les brouillons sont des notes éditoriales; aucun fichier vidéo n’est créé ou rendu.
- Les repères demandent toujours une note et une cible manuelles; le timecode est prérempli depuis la position du lecteur, puis on peut cliquer un repère pour s’y déplacer.
- Les téléchargements de référence exigent un lien pris en charge par `yt-dlp` et un accès réseau; les extracteurs de plateformes peuvent changer. Un échec est affiché et peut être relancé.
- La publication sur YouTube, TikTok ou une autre plateforme n’est pas effectuée.
- Les miniatures nécessitent FFmpeg. Sans FFmpeg, l’inventaire reste disponible, mais les aperçus peuvent échouer.

## Vérification locale

Depuis la racine du plugin, avec l’interpréteur Python du gateway (FastAPI/Starlette requis pour exécuter les tests HTTP complets) :

```bash
python -m unittest discover -s tests -v
python -m py_compile dashboard/plugin_api.py tests/test_plugin_api.py tests/test_video_tickets.py
node --check desktop/plugin.js
node tests/test_desktop_plugin.js
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
├── CHANGELOG.md                 # Historique des versions
├── plugin.yaml                  # Identité du paquet Agent Plugin
├── plugin.json                  # Manifeste du plugin portable
├── desktop/
│   └── plugin.js                # Page et navigation Hermes Desktop
├── workspace/                   # Point de montage local, exclu de Git
│   ├── Waiting/                 # VODs à traiter
│   ├── References/               # MP4 locaux et links.json
│   └── Finish/                  # Clips terminés
├── dashboard/
│   ├── manifest.json            # Onglet Agent Editor
│   ├── plugin_api.py             # Routes protégées et accès média
│   ├── dist/                    # Interface du Dashboard
│   └── media-player.*            # Lecteur progressif et service worker
└── tests/                        # Tests API, interface et lecteur
```
