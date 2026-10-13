# Reprise de lecture entre instances Pear Desktop — compte rendu de recherche

*10 octobre 2026 — analyse du code de Pear Desktop **v3.12.0** (tag `v3.12.0`), version installée sur les deux PC.*

## Résumé

- **Rien de natif ne fait du « handoff »** (reprendre sur un PC ce qui jouait sur l'autre) : ni YouTube Music, ni Last.fm (envoi à sens unique), ni le champ `url` de `config.json` (simple mémoire locale de la dernière page ouverte).
- Pear Desktop embarque deux plugins exploitables :
  - **API Server** : API HTTP + WebSocket locale qui expose le titre en cours, la position, la file et des commandes (lecture, pause, seek, file). C'est la brique utilisée par l'outil livré (`pearsync`).
  - **Music Together** : écoute synchronisée hôte → invités en pair à pair (WebRTC). Fonctionne déjà entre réseaux différents, sans rien coder, mais c'est un *miroir en direct* plutôt qu'un transfert, et il dépend de serveurs publics tiers.
- L'API n'a **pas d'endpoint « jouer tel videoId »**. Le transfert passe donc par la file : on insère le titre après le morceau courant, on bascule l'index, puis on recale la position.
- **YT Music Morphe (Android)** n'offre ni API, ni télécommande, ni synchro. Côté téléphone, le seul levier est d'ouvrir un lien `music.youtube.com/watch?v=…&t=…`.
- Solution livrée : `pearsync`, en deux modes. Le **mode direct** convient au LAN ou au VPN. Le **mode relais** fonctionne quel que soit le réseau : chaque PC ne fait que des requêtes sortantes, l'API Pear reste sur `127.0.0.1`.

## 1. Pourquoi ça ne marche pas aujourd'hui

| Mécanisme | Ce qu'il fait réellement | Utile pour la reprise ? |
|---|---|---|
| Last.fm (plugin `scrobbler`) | Envoie « en écoute » et les scrobbles vers Last.fm | Non : aucun client ne relit l'état depuis Last.fm |
| `url` dans `%APPDATA%\YouTube Music\config.json` | Dernière URL ouverte, rouverte au lancement | Non : local à chaque PC |
| Historique du compte Google | Liste des titres écoutés, partagée entre appareils | Partiellement : le titre seulement, ni position ni file |
| Protocole `youtubemusic://` | Relaie les mêmes commandes que les contrôles (`next`, `pause`…) vers une instance déjà lancée | Non : aucune commande d'ouverture d'URL ou de vidéo |

## 2. Plugin API Server (v3.12)

Source : `src/plugins/api-server/` (ajouté en 3.6). Serveur Hono, documentation OpenAPI intégrée (`/doc`, interface Swagger sur `/swagger`).

### Configuration (`config.ts`)

| Clé | Défaut | Remarque |
|---|---|---|
| `hostname` | **`0.0.0.0`** | Écoute sur toutes les interfaces : l'API est joignable depuis le LAN |
| `port` | `26538` | |
| `authStrategy` | `AUTH_AT_FIRST` | Le premier appel à `POST /auth/{id}` ouvre une boîte de dialogue « Autoriser / Refuser » sur la machine |
| `secret` | `Date.now().toString(36)` | Clé HMAC des jetons. **Prévisible** (dérivée de l'heure de création) et **non modifiable depuis le menu** |
| `authorizedClients` | `[]` | Identifiants autorisés ; un jeton n'est valide que si son `id` y figure |
| `useHttps`, `certPath`, `keyPath` | désactivé | HTTPS natif possible avec un certificat fourni |

### Authentification

- `POST /auth/{id}` renvoie `{"accessToken": "<JWT HS256>"}`. Le jeton contient `id` et `iat`, **sans expiration**. Pour le révoquer, il faut retirer l'id de `authorizedClients` ou changer `secret`.
- Toutes les routes `/api/*` exigent `Authorization: Bearer <jeton>`, sauf en `authStrategy: NONE`.
- WebSocket `GET /api/v1/ws?token=<jeton>` : le garde JWT HTTP l'exclut, mais la route vérifie elle-même le jeton à l'ouverture (fermeture 1008 sinon).

### Endpoints utiles au transfert (`backend/routes/control.ts`)

| Besoin | Endpoint | Détail |
|---|---|---|
| Titre, position, pause | `GET /api/v1/song` | `videoId`, `title`, `artist`, `elapsedSeconds`, `isPaused`, `songDuration`, `url`, `playlistId`… ; 204 si rien n'est chargé |
| File d'attente | `GET /api/v1/queue` | `items[]` au format interne YouTube : `playlistPanelVideoRenderer` ou `playlistPanelVideoWrapperRenderer.primaryRenderer…` (version audio et clip), avec `selected` sur l'élément courant |
| Ajouter à la file | `POST /api/v1/queue` `{videoId, insertPosition}` | `INSERT_AT_END` ou `INSERT_AFTER_CURRENT_VIDEO`. **Asynchrone** : le renderer appelle `/music/get_queue` puis insère. Nécessite un contexte de file existant |
| Sauter à un élément | `PATCH /api/v1/queue` `{index}` | |
| Retirer / déplacer / vider | `DELETE /queue/{i}`, `PATCH /queue/{i}`, `DELETE /queue` | |
| Position | `POST /api/v1/seek-to` `{seconds}` | Aussi `go-back` et `go-forward` |
| Lecture | `POST /play`, `/pause`, `/toggle-play`, `/next`, `/previous` | |
| Divers | `volume`, `shuffle`, `repeat-mode`, `like`, `search`… | |

**Pas de « play videoId »** : pour lancer un titre précis, il faut l'ajouter à la file puis sauter à son index. Si l'instance n'a encore rien joué depuis son lancement, la file n'a pas de contexte et l'insertion peut échouer silencieusement. Il faut alors lancer n'importe quel titre au préalable.

### WebSocket temps réel (`backend/routes/websocket.ts`)

Événements poussés : `PLAYER_INFO` (état complet à la connexion), `VIDEO_CHANGED`, `PLAYER_STATE_CHANGED`, `POSITION_CHANGED`, `VOLUME_CHANGED`, `REPEAT_CHANGED`, `SHUFFLE_CHANGED`. Ce canal convient pour une synchro continue façon « suivre l'autre PC ». Pour un transfert ponctuel, des lectures HTTP suffisent, et c'est le choix fait dans `pearsync`.

## 3. Plugin Music Together (v3.12)

Source : `src/plugins/music-together/`. Conçu pour écouter à plusieurs, il peut servir de solution sans code.

- **Transport** : PeerJS (WebRTC DataChannel). Signalisation par le broker public PeerJS ; traversée NAT via STUN Google et TURN publics (`turn:eu-0.turn.peerjs.com`, `turn:freestun.net`, identifiants partagés). **Il fonctionne donc entre deux réseaux différents** sans ouvrir de port.
- **Fonctionnement** : un PC « héberge » et obtient un Host ID à copier ; l'autre « rejoint » avec cet ID. À la connexion, la file de l'invité est vidée et remplacée par celle de l'hôte. L'hôte diffuse ensuite chaque seconde la position, l'état lecture/pause et l'index (`SYNC_PROGRESS`). Trois niveaux de permission (`host-only`, `playlist`, `all`) règlent ce que l'invité peut modifier.
- **Pour un handoff** : héberger sur le PC A, rejoindre depuis B (B se cale sur A), couper le son de A puis fermer la session. C'est utilisable, mais il faut deux manipulations manuelles et l'échange d'ID à chaque fois.
- **Réserves** : dépendance à des services publics gratuits (disponibilité non garantie), métadonnées de session transmises à ces tiers (le flux applicatif est chiffré par DTLS), et aucune automatisation possible depuis l'API.

## 4. YT Music Morphe (Android)

D'après le dépôt des patches Morphe (v1.26.0, 5 mai 2026), les 25 patches YouTube Music portent sur l'interface, les publicités, la lecture en arrière-plan, le thème, le fondu enchaîné, etc. **Aucun n'apporte d'API, de télécommande, de cast entrant ou de synchronisation.** Ce qui reste possible :

- **PC → téléphone** : ouvrir sur le téléphone un lien `https://music.youtube.com/watch?v=<id>&t=<s>`. La commande `pearsync link <pc>` le génère ; en mode relais, il est accessible depuis n'importe où. *À vérifier* : je n'ai pas pu confirmer que l'appli Android respecte le paramètre `t=`. Le titre s'ouvre de façon certaine, la position est à valider.
- **Téléphone → PC** : rien d'automatisable depuis l'appli. Le Last.fm du téléphone donne le titre en cours (pas la position), via l'API Last.fm `user.getRecentTracks` (`nowplaying`). C'est une extension possible, qui retrouverait le titre seulement.

## 5. Options d'architecture

| Option | Réseaux différents | Effort | Sécurité | Verdict |
|---|---|---|---|---|
| A. Music Together | Oui (P2P + TURN public) | Aucun code, manipulation manuelle | Dépend de tiers | Dépannage immédiat |
| B. `pearsync` direct, LAN | Non | Faible | API exposée sur le LAN : restreindre par pare-feu | Bien à la maison |
| C. `pearsync` direct + VPN (WireGuard/Tailscale) | Oui | Moyen (VPN sur chaque PC) | API joignable seulement dans le tunnel | Solide si le VPN existe déjà |
| **D. `pearsync` relais** | **Oui** | Moyen (un petit service sur le homelab) | **API en 127.0.0.1, flux sortants HTTPS seulement** | **Recommandé** |

Ne jamais rediriger le port 26538 sur la box : jeton sans expiration, secret prévisible par défaut, et aucune limitation de tentatives.

## 6. Ce qui est livré (`pearsync`)

- `pearsync/` : client de l'API, logique de transfert, agent et CLI (bibliothèque standard Python ≥ 3.11 uniquement).
- `relay/relay.py` : relais (bibliothèque standard), jeton Bearer comparé en temps constant, taille des corps et nombre de nœuds bornés, commandes expirées au bout de 2 min, journaux sans en-têtes ni paramètres d'URL. Unité systemd durcie et exemple Caddy pour le TLS.
- Algorithme de transfert : capture de la source → pause de la source et relecture de la position exacte → insertion du titre après le courant sur la cible → attente de l'insertion asynchrone → bascule d'index → attente du changement de titre → remplacement de la suite de la file, dans l'ordre → seek vérifié (réessayé si le lecteur l'ignore juste après le changement de titre) → lecture ou pause selon l'état d'origine.
- En mode relais, une position « en lecture » est extrapolée avec l'âge de l'état. Si l'agent source ne publie plus depuis plus de 45 s (PC en veille), la position est figée à sa dernière publication au lieu de continuer à avancer.

**Vérifié ici** : 13 tests contre un faux serveur Pear qui reproduit les comportements relevés dans le code (insertion asynchrone, bascule différée, seek ignoré juste après un changement, éléments « wrapper »), plus un test de bout en bout avec les vrais processus (relais, agent, CLI). Lint `ruff`, typage `mypy`, unité systemd validée.

**Non vérifié ici** : le fonctionnement contre un vrai Pear Desktop. Je n'ai pas d'instance Windows avec l'appli et un compte YouTube Music. La procédure de validation est dans le README.

## 7. Limites connues

- La cible doit avoir déjà joué un titre depuis son lancement (contexte de file requis par l'insertion).
- **Mix, playlist, album** : le contexte est rechargé en redémarrant Pear sur la cible, avec `watch?v=…&list=…` placé dans sa config. Cela exige l'option « Reprendre la dernière chanson » et que pearsync tourne sur la cible (`pull` ou relais). Sans rechargement possible, seuls `queue_limit` titres (25 par défaut) sont recopiés, puis la cible part sur sa propre radio automatique.
- Un mix rechargé est reconstruit par YouTube à partir de son identifiant : les titres suivants peuvent légèrement différer de ceux de la source, mais c'est le même mix, qui continue à l'infini.
- Shuffle, repeat et volume ne sont pas transférés.
- Précision d'environ 1 à 2 s (granularité de `elapsedSeconds` et délai de bascule).
- Dépendance au format interne de la file YouTube Music : une mise à jour de Pear ou de YouTube peut le casser. Le point d'entrée à surveiller est `parse_queue()`.

## Sources

- [Pear Desktop v3.12.0 — plugin api-server](https://github.com/pear-devs/pear-desktop/tree/v3.12.0/src/plugins/api-server)
- [Pear Desktop v3.12.0 — plugin music-together](https://github.com/pear-devs/pear-desktop/tree/v3.12.0/src/plugins/music-together)
- [Pear Desktop v3.12.0 — gestion de la file côté renderer (`src/renderer.ts`)](https://github.com/pear-devs/pear-desktop/blob/v3.12.0/src/renderer.ts)
- [Pear Desktop v3.12.0 — protocole `youtubemusic://` (`src/providers/protocol-handler.ts`)](https://github.com/pear-devs/pear-desktop/blob/v3.12.0/src/providers/protocol-handler.ts)
- [Morphe Patches — liste des patches YouTube Music](https://upd.dev/MorpheApp/morphe-patches)
