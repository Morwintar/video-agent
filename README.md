# Waiting Studio — plugin du dashboard Hermes

Waiting Studio met les vidéos de `Waiting` (y compris ses sous-dossiers, avec leur chemin relatif) au premier plan; la liste `Finish` complète reste accessible dans une section repliée. Chaque rediffusion Waiting affiche une miniature produite à la demande par FFmpeg, sans modifier ni enregistrer de dérivé sur disque. Choisir une rediffusion ouvre son espace de contenu pour lire la source, retrouver ses repères et brouillons, et prévisualiser les clips MP4 associés déjà créés dans `Finish`. Les sections **Skills personnalisés** et **Références pour les shorts** permettent d’ajouter des skills locaux et des liens TikTok/YouTube avec notes et points à observer. Chaque nouveau brouillon conserve un instantané des références/consignes utilisées à sa création. Les liens ne sont pas visités ni téléchargés; aucun fichier source n’est modifié et rien n’est publié. Cette tranche enregistre des brouillons éditoriaux — elle ne génère ni ne rend encore de vidéo.

## Emplacement et source

Le dossier autonome du plugin est `$HERMES_HOME/plugins/waiting-studio/`. Il contient `plugin.json` pour le format portable Agent Plugins et `dashboard/manifest.json` pour l’onglet Hermes Dashboard. L’API vidéo cherche les dossiers sous `$HERMES_HOME/inbox/stream-test/` :

- `Waiting/`
- `Finish/`

Les skills créés par l’interface sont enregistrés dans `skills/<nom>/SKILL.md` sous la racine du plugin; ils voyagent donc avec une copie du dossier. Les références, brouillons et repères de moments sont conservés séparément dans `$HERMES_HOME/state/waiting-studio/references.json`, `content.json` et `moments.json` (fichiers privés en mode `0600`) pour qu’ils ne se retrouvent pas dans le dépôt portable du plugin. Les copies média dérivées facultatives sont placées à part dans `$HERMES_HOME/state/waiting-studio/compat/`; elles ne sont jamais traitées comme des sources `Waiting`. Dans l’environnement où ce plugin a été créé, `$HERMES_HOME` vaut `/opt/data`.

## API

- `GET /api/plugins/waiting-studio/videos` renvoie les noms (chemins relatifs pour les sous-dossiers de `Waiting`), tailles et états de disponibilité des deux dossiers, sans chemin absolu.
- `GET /api/plugins/waiting-studio/videos/waiting/{filename}` sert uniquement un MP4 régulier directement dans `Waiting` ou dans un de ses sous-dossiers, avec requêtes HTTP Range; `?codec=auto` choisit une copie HVC1 dérivée si elle existe dans `state/waiting-studio/compat/`, sinon la source; `?codec=source` force l’original. Les chemins arbitraires et liens symboliques dans les composants du chemin sont refusés.
- `GET /api/plugins/waiting-studio/videos/waiting-thumbnails/{filename}` extrait à la demande une image JPEG 320×180 max depuis une vidéo régulière de `Waiting`; la génération est limitée en temps et en concurrence, le cache reste borné en mémoire et les erreurs n’exposent pas les diagnostics FFmpeg. Aucun dérivé n’est écrit sur disque; FFmpeg doit être dans le `PATH`.
- `GET /api/plugins/waiting-studio/videos/finish/{filename}` sert uniquement un MP4 régulier directement dans `Finish`, avec les requêtes HTTP Range nécessaires au seek; les chemins arbitraires et les liens symboliques sont refusés.
- `GET /api/plugins/waiting-studio/skills` liste les skills locaux sans exposer de chemins.
- `POST /api/plugins/waiting-studio/skills` crée un nouveau skill validé, sans écraser un skill existant. Le chargeur Agent Plugins de Hermes l’activera au prochain redémarrage du gateway.
- `GET /api/plugins/waiting-studio/references` liste les liens de référence enregistrés localement, avec leur checklist d’observation.
- `POST /api/plugins/waiting-studio/references` enregistre un lien HTTPS YouTube/TikTok autorisé, avec une note facultative et la checklist d’observation; `DELETE /api/plugins/waiting-studio/references/{id}` le retire.
- `GET /api/plugins/waiting-studio/content/{filename}` ouvre l’espace d’une VOD MP4 directement dans `Waiting`, liste ses brouillons et repères sauvegardés, renvoie les références actuelles et `clips` (MP4 directs dans `Finish` dont le nom commence par le nom de base de la source suivi de `_`). `clipsTruncated` signale si le scan borné de `Finish` a été interrompu; les clips sont associés par convention de nommage, pas par métadonnées de projet.
- `POST /api/plugins/waiting-studio/content/{filename}/moments` enregistre un repère horodaté (0–24 h), limité à 500 par rediffusion, avec une note (500 caractères max) et une cible `short`, `video` ou `both`.
- `POST /api/plugins/waiting-studio/content/{filename}/items` enregistre un brouillon Short ou Vidéo et copie les références, notes et consignes d’observation du moment dans son instantané de création.

Les références n’acceptent que des hôtes YouTube/TikTok autorisés, jusqu’à 200 liens; URL limitée à 2048 caractères et note à 2000. Chaque référence reçoit six repères (accroche, rythme, cadrage vertical/HUD, sous-titres vérifiés, audio, payoff). À la création d’un brouillon, le plugin copie les liens, notes et checklists actuels dans le contexte conservé avec le brouillon; supprimer ou modifier une référence ensuite ne change pas cet instantané. Ces éléments servent de guide de travail, mais aucune analyse automatique des vidéos de référence ni génération/rendu de contenu n’est encore exécuté. Un Short affiche la cible 1080×1920, 30 fps et environ 15 s. Le plugin n’effectue aucun appel vers YouTube/TikTok et ne récupère aucun média.

Les routes API du plugin restent protégées par l’authentification normale du dashboard. Les fichiers HTML/JS statiques du lecteur sont servis comme assets du plugin pour que le navigateur puisse enregistrer son service worker; ils ne contiennent aucun secret, et le flux MP4 continue de passer par l’API protégée. Pour les VOD `Waiting`, le lecteur utilise des requêtes HTTP Range : le navigateur ne réclame que les plages nécessaires à la lecture et au seek, sans charger le fichier complet en mémoire. En mode session protégée, la balise vidéo utilise la session/cookie du Dashboard. En mode local à jeton, un iframe statique de même origine est contrôlé par un service worker à portée limitée au dossier du plugin; le service worker ajoute l’en-tête de session aux seules requêtes de lecture et préserve `Range`/`If-Range`. Le jeton n’est pas placé dans l’URL ni écrit sur disque : il est transmis en mémoire à l’iframe, puis redemandé par MessageChannel si le service worker redémarre; le flux cesse d’être autorisé quand l’iframe est fermée. Ce mode exige un contexte navigateur sécurisé (localhost ou HTTPS) et un jeton de session disponible. Si le navigateur ne le permet pas, le lecteur affiche un avis au lieu de tenter de télécharger 10–16 Gio. Le lecteur `Finish` existant conserve son mécanisme `SDK.authedFetch`. Les timecodes peuvent être ajoutés aux notes d’un brouillon; il faut ensuite enregistrer ce brouillon pour conserver la sélection dans son texte.

## Vérification locale

Depuis le dossier du plugin :

```bash
python3 -m unittest discover -s tests -v
python3 -m py_compile dashboard/plugin_api.py tests/test_plugin_api.py
node --check dashboard/dist/index.js
node --check dashboard/media-service-worker.js
node --check dashboard/media-player.js
node tests/test_dashboard_bundle.js
node tests/test_media_service_worker.js
node tests/test_media_player_bridge.js
python3 -m json.tool dashboard/manifest.json
python3 -m json.tool plugin.json
```

Le test de requêtes Range s’exécute lorsque FastAPI/Starlette est disponible; pour le valider dans l’environnement Hermes, utiliser son interpréteur Python. Après découverte du plugin par Hermes, actualiser le dashboard. Un redémarrage du gateway/dashboard est nécessaire pour charger les nouvelles routes et activer les skills enregistrés.
