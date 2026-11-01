# Changelog

Les changements notables du projet sont consignés ici. Les versions suivent
[SemVer](https://semver.org/).

## [Unreleased]

## [0.5.0] - 2026-11-01

### Added
- Références enregistrées en état `not_requested` avec une action UI explicite **Télécharger**; possibilité de relancer les références plus anciennes qui n’ont pas de copie locale.
- Téléchargement asynchrone automatique lors de l’ajout d’une référence via la dépendance déclarée `yt-dlp==2026.10.26`; copie MP4 gérée, statut en cours/terminé/échec et suppression limitée à cette copie.

### Changed
- Quand Agent Editor sélectionne un MP4 plus grand que la limite courante, le backend du plugin relève en mémoire le plafond privé du Gateway jusqu’à la taille de ce fichier, sans dépasser 30 Gio; le changement s’applique aux autres opérations de fichiers gérés jusqu’au redémarrage du Gateway.
- Ce contournement exige l’isolation `in_process` et dépend d’un détail interne du Gateway; une lecture complète via `/api/files/read` peut consommer beaucoup de mémoire. Retester après chaque mise à jour Hermes.

## [0.4.2] - 2026-11-01

### Fixed
- Agent Editor lit maintenant les MP4 par le protocole natif `hermes-media://remote` : le Desktop authentifie `/api/files/stream` et transmet Range/seek, sans ticket dans l’URL ni jeton de session exposé au plugin.
- Le backend du plugin ne retourne au lecteur qu’un chemin absolu MP4 déjà validé dans `Waiting`, `References` ou `Finish`; le chemin est encodé et contrôlé avant de construire l’URL native.
- La lecture fonctionne sans l’API expérimentale `ctx.media.resolveUrl`; aucun changement au cœur du Desktop ou du Gateway n’est requis.

## [0.4.1] - 2026-11-01

### Fixed
- Agent Editor s’enregistre encore sur les builds Desktop antérieurs sans `ctx.media`; l’erreur de lecture invite à mettre à jour Desktop au lieu de faire échouer le chargement du plugin.
- L’installation du paquet unifié via l’installateur Hermes Desktop active aussi son compagnon Agent Editor.

## [0.4.0] - 2026-11-01

### Added
- Le lecteur Desktop utilise le transport Hermes authentifié `ctx.media.resolveUrl`, qui préserve les requêtes Range/seek sans exposer le jeton de session dans l’URL.
- L’API de tickets fournit un `streamPath` limité au plugin pour que le transport natif joigne le flux protégé dans Agent Editor.

### Changed
- Le lecteur requiert une version Desktop qui expose le transport média authentifié; les fichiers sources et pistes audio ne sont pas modifiés.

## [0.3.2] - 2026-11-01

### Fixed
- Les tickets Desktop de Waiting privilégient le dérivé HVC1 existant lorsqu’il est valide; la source HEV1 est conservée et les requêtes Range restent prises en charge.

## [0.3.1] - 2026-11-01

### Fixed
- Le lecteur Waiting reste visible si le chargement des repères est en cours ou échoue.

## [0.3.0] - 2026-11-01

### Added
- Lecteur MP4 intégré dans les trois sections Desktop; contrôles natifs, seek et insertion du timecode courant dans un repère.
- Tickets de lecture temporaires, limités à un seul fichier et renouvelés/révoqués par l’interface.

### Changed
- Les repères cliquables repositionnent la vidéo; boutons et états du lecteur utilisent le UI kit Hermes Desktop.
- Documentation et versions synchronisées à `0.3.0`.

### Security
- Ticket aléatoire limité à 15 minutes, stocké sous forme de hash côté serveur; flux limité au fichier autorisé, avec Range et réponses `no-store`/`no-referrer`.
- Renouvellement/révocation acceptent le ticket dans le corps JSON plutôt que dans le chemin d’URL.

## [0.2.0] - 2026-11-01

### Added
- Page Hermes Desktop **Agent Editor** pour parcourir Waiting, References et Finish.
- Gestion des liens de référence et création de repères à timecode manuel dans Desktop.
- Workspace configurable `Waiting/`, `References/`, `Finish/`, avec repli vers l’ancien inventaire sans déplacer les médias.
- Prévisualisation Dashboard des vidéos locales de référence et routes Range pour les MP4 de References.

### Changed
- Synchronisation des versions Agent Plugin, plugin portable et Dashboard à `0.2.0`.
- Bibliothèque de références, inventaire récursif et documentation du flux Desktop.

### Security
- Les vidéos restent en lecture seule; le workspace refuse les liens symboliques aux frontières média.
- Aucun lecteur vidéo Desktop ni route publique ajoutés; le streaming Desktop authentifié demeure à venir.
