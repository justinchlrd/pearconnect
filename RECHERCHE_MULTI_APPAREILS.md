# Lecture partagée entre plusieurs appareils (PC et mobile) — recherche

*10 octobre 2026 — suite de RECHERCHE_PLAYBACK.md. Objectif : une lecture « qui suit » l'utilisateur entre les 2 PC (Pear Desktop 3.12) et le téléphone Android (YT Music Morphe), à la manière de Spotify Connect.*

## En bref

1. **YouTube Music synchronise déjà en partie, de façon native.** Depuis juillet 2025, la progression d'écoute est synchronisée, avec une option « Reprendre » dans l'accueil. Depuis janvier 2026, la **file « En écoute » est synchronisée entre Android, iOS et le web**, et c'est la session la plus récente qui l'emporte. Pear Desktop embarque le client web, il devrait donc en profiter. **Mais** Pear rouvre par défaut, au démarrage, son propre dernier titre (option « Reprendre la dernière chanson quand l'application démarre »). Ce PC devient alors la session la plus récente et écrase la file synchronisée, ce qui pourrait expliquer à lui seul le « PC hors synchro » du départ. **À tester en premier, c'est gratuit.**
2. **Le scrobbling n'est utile que comme signal.** Last.fm est trop pauvre : titre et artiste seulement, interrogation périodique, ni position ni pause. **ListenBrainz est nettement meilleur** : Pear y envoie l'URL YouTube (donc le videoId) et la durée, et le serveur pousse les « playing now » en temps réel. Il ne transmet toutefois ni pause, ni seek, ni position : c'est un déclencheur, pas une source de vérité.
3. **« Une appli mobile qui se calque sur Pear » existe presque** : **Metrolist**, un client YouTube Music open source pour Android sous GPL-3.0. Il a une fonction « Listen Together » complète (salons, transfert d'hôte, synchro de file, horloge serveur, attente de mise en mémoire tampon) avec un serveur **auto-hébergeable** (`metroserver`, en Go, avec Dockerfile). Il manque seulement un pont côté Pear.
4. **Autres voies** :
   - un compagnon Android maison qui pilote Morphe via la session média d'Android ;
   - un récepteur Cast YouTube (`yt-cast-receiver`) qui ferait de chaque PC une cible du bouton Cast de l'appli ;
   - Music Assistant, solution serveur complète mais fragile côté YouTube Music.

## 1. Ce que YouTube Music fait déjà (natif)

| Date | Fonction | Ce qui est synchronisé | Limites rapportées |
|---|---|---|---|
| Juillet 2025 | Synchro de progression | Barres de progression des autres appareils dans l'accueil, bouton « Reprendre » | Pas un équivalent de Spotify Connect, déploiement progressif |
| Janvier 2026 | Synchro de la file « En écoute » | File et dernier titre, affichés dans le mini-lecteur (« Depuis votre navigateur », « Depuis votre iPhone »…) sur Android, iOS et web | Position exacte non confirmée ; la session la plus récente écrase les autres ; vraisemblablement réservé à Premium (constat d'Android Police sur la version web) ; probablement côté serveur |

**Pourquoi Pear peut casser ça.** Dans le code de la v3.12, `options.resumeOnStart` vaut `true` par défaut. Au lancement, Pear charge l'URL `watch?v=…` mémorisée au lieu de l'accueil, ce qui relance l'ancien titre de *ce* PC.

**Test proposé (10 minutes)** :
1. Sur les deux PC : menu Options → décocher « Reprendre la dernière chanson quand l'application démarre ».
2. Écouter sur le PC A, puis le fermer.
3. Ouvrir le téléphone : le mini-lecteur devrait proposer « Depuis votre navigateur ».
4. Écouter un peu sur le téléphone, puis ouvrir le PC B : le mini-lecteur web devrait reprendre la file du téléphone.
5. Noter si la position est conservée, ou si le titre repart du début.

Pour Morphe : la version de YouTube Music ciblée par les patches (9.17.x) est postérieure à janvier 2026, la fonction devrait donc être présente. Les patches ne la désactivent pas *a priori*, mais je ne l'ai pas vérifié.

## 2. Scrobbling comme source de synchro

### Last.fm
- `user.getRecentTracks` marque le titre en cours avec `nowplaying="true"`. Il n'y a **aucune position**, et seulement titre et artiste, sans videoId.
- Pas de push : il faut interroger régulièrement, donc la latence correspond à l'intervalle choisi (raisonnablement 10 à 30 s).
- Pear n'envoie le « now playing » qu'au **changement de titre et à la reprise après une pause** (jamais à la pause, au seek ou à la progression). La position déduite (« maintenant − moment de détection ») est donc fausse dès qu'on fait pause ou qu'on avance dans le morceau.
- Il faut retrouver le videoId à partir de « titre + artiste », par une recherche, avec un risque d'ambiguïté (versions live, remasters, clips).

**Verdict : insuffisant pour une reprise fiable.**

### ListenBrainz (nettement mieux)
- Le scrobbler ListenBrainz de Pear envoie dans `additional_info` l'**`origin_url`** (URL YouTube, donc le videoId) et la **`duration`**.
- Le serveur ListenBrainz **pousse en temps réel** les événements `playing_now` par socket.io (`wss://listenbrainz.org/socket.io/`, émission `json` avec `{"user": "<pseudo>"}`). Il suffit de connaître le pseudo, sans jeton. C'est pratique, mais cela rappelle que les écoutes ListenBrainz sont publiques.
- Le « playing now » expire au bout de la durée du titre. **Un même titre n'est pas réémis** : une reprise après pause sur le même morceau est invisible.
- Côté téléphone, le scrobbler Android (appli Last.fm, Pano Scrobbler…) lit la notification média et n'a pas le videoId : il faut retrouver le titre par une recherche.

**Verdict : bon signal « qu'est-ce qui joue, où, depuis quand », avec une latence d'environ 1 s.** Utile pour un mode « suivre automatiquement » approximatif, ou pour réveiller un agent. Insuffisant seul pour la position et la pause.

## 3. Une appli mobile qui se calque sur Pear

### 3a. Metrolist + metroserver (le plus proche d'un « Spotify Connect » YouTube Music)

- **Metrolist** : client YouTube Music Android open source (GPL-3.0), très actif (dernier commit le 8 octobre 2026). Connexion au compte YouTube Music, synchro de la bibliothèque, scrobbling Last.fm, et **Listen Together**.
- **Protocole Listen Together** (`listentogether.proto`, 240 lignes) : protobuf compressé en gzip sur WebSocket.
  - Salons, demandes d'accès et approbation.
  - `playback_action` avec play, pause, seek, change_track, opérations sur la file et `sync_queue`.
  - Horodatage serveur (`server_time`) pour compenser la latence, `buffer_ready` et `buffer_wait` pour démarrer ensemble.
  - **`transfer_host`**, qui correspond exactement au transfert de lecture d'un appareil à un autre.
  - Reconnexion avec reprise d'état.
- **metroserver** : serveur Go (GPL-3.0) avec Dockerfile, URL du serveur configurable dans l'appli, donc **auto-hébergeable sur le homelab**. Une politique par User-Agent décide quels clients peuvent héberger un salon ; sur une instance personnelle, on l'ouvre au pont Pear.
- **Ce qu'il faudrait écrire** : un pont « Pear ⇄ Listen Together », c'est-à-dire un nouveau type de nœud pour `pearsync`. Il parle le protobuf vers metroserver et l'API Pear en local, en réutilisant la logique de capture et d'application déjà écrite. Les deux PC et le téléphone rejoignent alors le même salon, et le transfert d'hôte devient le handoff.
- **Compromis** : sur le téléphone, Metrolist remplacerait Morphe. C'est un client non officiel, susceptible de casser quand YouTube change quelque chose (comme Morphe d'ailleurs). Le transfert de position et de file y est nativement prévu.

### 3b. Compagnon Android qui pilote Morphe (garder l'appli officielle patchée)

Android permet à une appli autorisée à lire les notifications (`NotificationListenerService` puis `MediaSessionManager.getActiveSessions`) d'**obtenir et de piloter la session média** de n'importe quel lecteur :
- lecture : titre, artiste, durée, **position**, lecture ou pause ;
- contrôle : play, pause, **seekTo** ;
- ouverture d'un titre précis : intent `VIEW` sur `https://music.youtube.com/watch?v=<id>` adressé au package de Morphe.

Ce compagnon serait un **agent pearsync côté Android**, branché sur le relais existant : publication de l'état, réception des commandes de reprise.

Point dur : **le videoId**. Je n'ai pas pu confirmer que YouTube Music l'expose dans les métadonnées de sa session média. À vérifier en installant l'appli de test « Media Controller Test » de Google, qui affiche tout ce qu'une session expose. À défaut, on retrouve le titre par une recherche (titre, artiste et durée) via l'endpoint `/search` de l'API Pear, avec le même risque d'ambiguïté que pour Last.fm, atténué par la durée.

Alternatives sans code et pourquoi je les écarte :
- Home Assistant Companion : capteur de session média rafraîchi par défaut toutes les 15 minutes, et commande média sans seek.
- KDE Connect : télécommande croisée, sans transfert.

### 3c. Le PC comme cible « Cast » du téléphone (`yt-cast-receiver`)

- Bibliothèque Node (v2.1.1, mars 2026) qui implémente un **récepteur Cast YouTube et YouTube Music**. L'appli du téléphone voit le PC dans son menu Cast.
- Il suffit d'implémenter un `Player` : `doPlay(vidéo, position)`, pause, seek, position, volume. Ces méthodes se branchent directement sur l'API Pear et sur `apply_state`.
- Couplage via DIAL sur le LAN. L'appairage par code TV, qui marche à travers les réseaux, est documenté pour l'appli YouTube ; d'après l'auteur, **pas pour YouTube Music**.
- Sens unique : téléphone vers PC uniquement. Avec Morphe et GmsCore (microG), la disponibilité du bouton Cast vers un appareil DIAL reste **à tester**.
- Bibliothèque marquée « toujours en cours de développement », licence non commerciale pour une dépendance (`peer-dial`), ce qui convient à un usage perso.

## 4. Approche serveur : Music Assistant

- Une file unique côté serveur et des lecteurs partout : appli desktop compagnon (alpha) avec Sendspin, appli mobile officielle (Android 8+, **pas encore publiée dans les stores**, APK sur GitHub). **Le transfert de file vers un autre lecteur est natif.**
- Fournisseur YouTube Music en mode « best-effort » (doc 2.10.5) : comptes gratuits non pris en charge, authentification par cookies qui expirent, **jeton PO obligatoire** (générateur bgutil à héberger), 3 flux simultanés maximum, AAC 256 kb/s.
- Remplace à la fois Pear et Morphe. C'est l'architecture la plus propre conceptuellement, mais la plus fragile pour la source YouTube Music et la plus lourde à maintenir.

## 5. Comparatif

| Option | Appareils | Sens | Position | Latence | Effort | Risques principaux |
|---|---|---|---|---|---|---|
| Synchro native YT Music (+ `resumeOnStart` désactivé) | PC, mobile | Tous | Non garantie | À l'ouverture | Aucun | Comportement opaque, probablement Premium |
| pearsync (livré) | PC ⇄ PC | Tous | Oui (±2 s) | 3 à 6 s | Fait | — |
| Last.fm « now playing » | Tous | Lecture seule | Non | 10 à 30 s | Faible | Ambiguïté titre → vidéo |
| ListenBrainz socket | Tous (PC avec videoId) | Lecture seule | Déduite | ~1 s | Faible | Pas de pause/seek, données publiques |
| **Metrolist + metroserver + pont Pear** | PC ⇄ PC ⇄ mobile | Tous | **Oui, horloge serveur** | < 1 s | Moyen-élevé (pont) | Changer d'appli mobile |
| Compagnon Android pour Morphe | PC ⇄ mobile | Tous | Oui | 3 à 6 s | Moyen (Kotlin) | videoId à retrouver |
| Récepteur Cast (`yt-cast-receiver`) | Mobile → PC | Un seul | Oui | ~1 s | Moyen (Node) | LAN seulement pour YT Music, Cast sous microG à tester |
| Music Assistant | Tous | Tous | Oui | ~1 s | Élevé | Fournisseur YT Music fragile, remplace les clients |

## 6. Recommandation

1. **D'abord le test natif** de la section 1. S'il couvre le titre et la file entre PC et téléphone, il peut suffire, avec `pearsync` pour la position exacte entre PC.
2. Pour un vrai « Spotify Connect » multi-appareils :
   - **Si changer d'appli mobile est acceptable**, Metrolist + metroserver auto-hébergé + un pont Pear. C'est l'option qui fait le moins de travail neuf pour le meilleur résultat, puisque le protocole de synchro existe et est conçu pour ça.
   - **Si tu tiens à Morphe**, le compagnon Android qui rejoint le relais pearsync. Commencer par vérifier, avec « Media Controller Test », si le videoId est exposé.
3. ListenBrainz en complément optionnel : mode « suivre automatiquement » ou réveil des agents, jamais comme source de vérité.

## Sources

- Pear Desktop v3.12.0 — [plugin scrobbler (ListenBrainz)](https://github.com/pear-devs/pear-desktop/tree/v3.12.0/src/plugins/scrobbler), [option resumeOnStart (`src/index.ts`)](https://github.com/pear-devs/pear-desktop/blob/v3.12.0/src/index.ts)
- [9to5Google — synchro de la file YouTube Music (25/01/2026)](https://9to5google.com/2026/01/25/youtube-music-queue-sync/)
- [Android Authority — synchro de lecture entre appareils (26/01/2026)](https://www.androidauthority.com/youtube-music-cross-device-playback-sync-3635250/)
- [Android Authority — reprise sur un autre appareil (18/07/2025)](https://www.androidauthority.com/youtube-music-playback-sync-3578668/)
- [Android Police — reprise mobile → web](https://www.androidpolice.com/youtube-music-mobile-desktop-queue-sync/)
- [ListenBrainz — API core (playing-now)](https://listenbrainz.readthedocs.io/en/latest/users/api/core.html), [architecture (websockets, expiration)](https://listenbrainz.readthedocs.io/en/latest/developers/architecture.html), [code serveur](https://github.com/metabrainz/listenbrainz-server)
- [Last.fm — user.getRecentTracks](https://www.last.fm/api/show/user.getRecentTracks)
- [Metrolist](https://github.com/MetrolistGroup/Metrolist), [metroserver](https://github.com/MetrolistGroup/metroserver)
- [yt-cast-receiver](https://github.com/patrickkfkan/yt-cast-receiver), [fil Volumio du plugin (appairage par code)](https://community.volumio.com/t/plugin-youtube-cast-receiver/46522/173)
- [Home Assistant Companion — capteurs](https://companion.home-assistant.io/docs/core/sensors/), [commandes de notification](https://companion.home-assistant.io/docs/notifications/notification-commands/)
- [Music Assistant — fournisseur YouTube Music](https://www.music-assistant.io/music-providers/youtube-music/), [transfert de file](https://music-assistant.io/faq/masstransfer), [appli mobile](https://github.com/music-assistant/mobile-app), [appli desktop](https://www.music-assistant.io/companion-app/)
- [Media Controller Test (outil d'inspection des sessions média Android)](https://github.com/dgiannakop/android-media-controller)
