# Changelog

Les changements notables du projet sont consignés ici. Les versions suivent
[SemVer](https://semver.org/).

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
