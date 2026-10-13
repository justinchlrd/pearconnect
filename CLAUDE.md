# Pear Connect — passation (état au 13/10/2026, v12)

« Spotify Connect » fait maison pour YouTube Music : voir sur chaque appareil ce qui joue
ailleurs, transférer la lecture (titre, position, mix, file) et piloter l'appareil actif.
Propriétaire : Bridouche (admin réseau/sécurité, homelab Proxmox). Échanges en français.

Documents de fond : `RECHERCHE_PLAYBACK.md`, `RECHERCHE_MULTI_APPAREILS.md`, `README.md`.

## 1. Contexte

- **PC** : 2 Windows avec **Pear Desktop 3.12.0** (ex-th-ch/youtube-music, exe « YouTube Music.exe »).
  - `the-kernel` : PC fixe, 192.168.1.69. Pear n'y est **pas** dans `%LOCALAPPDATA%\Programs\youtube-music` (l'installeur le détecte).
  - `the_core` : portable, 192.168.1.67 en LAN, 192.168.27.66 via le VPN WireGuard de la Freebox.
- **Téléphone** : Android, **YouTube Music Morphe**, paquet `app.morphe.android.apps.youtube.music`, avec le patch « Jam queue sharing » (Morphe patches ≥ 1.47). IP 192.168.1.17 en LAN, 192.168.27.65 en VPN.
- **Scrobbling** Last.fm sur les trois (hors périmètre).
- **Homelab** : Proxmox « the-nexus » 192.168.1.10. Accès : `ssh root@192.168.1.10` par clé depuis the_core (fonctionne aussi via le VPN) ; l'ancienne clé `claude_pearconnect` de la VM du pont d'appareils est **à révoquer** (voir tâches).
- **Dépôt git** : `pearsync/` est un dépôt git depuis le 13/10 (fins de ligne LF forcées par `.gitattributes`), remote `origin` = `git@github.com:justinchlrd/pearconnect.git` (privé). `server/dist/` est **ignoré** (sorties de build : asar, installeurs, `versions.json`, APK), seul `pearconnect-ca.crt` y est suivi ; les versions publiées vont dans les Releases GitHub. Le dossier de build (`../build/`, hors dépôt) contient le fork Pear et ses `node_modules`.

## 2. Architecture actuelle

```
 Pear (the-kernel)  ─┐                                   ┌─ Pear (the_core)
  plugin Pear Connect │  HTTPS :8443 (AC du homelab)     │  plugin Pear Connect
                      ├──────►  Serveur Connect  ◄──────┤
 Téléphone ───────────┘      LXC 101 192.168.1.168       └─ Télécommande web (navigateur)
  appli Pear Connect (compagnon Jam)  ⇅ Binder ⇅  YouTube Music Morphe (pont Jam)
```

### Serveur Connect — `server/connect_server.py` (Python stdlib, sans dépendance)

- **Déploiement** : LXC 101 `pearconnect` (Debian 12, non privilégié), unité `server/pearconnect.service`.
  - Code : `/opt/pearconnect/` ; données : `/var/lib/pearconnect/` (`tokens.json` haché sha256, `session.json`).
  - Certificats : `/etc/pearconnect/` (`server.key` généré dans le conteneur, `server-chain.crt`).
- **Écoute** : HTTP `:8787` (gardé pour la migration et l'installation en une commande) et HTTPS `:8443`.
- **Rôle** : il fait autorité sur l'appareil actif et la session.
  - Un seul appareil joue : quand un autre se met à jouer, l'ancien reçoit `pause`.
  - Commandes relevées en long-poll ; flux SSE `/v1/events` ; transfert `/v1/transfer` ; pilotage `/v1/control`.
  - Commandes : pause/play/next/previous/seek/volume/jump/enqueue/remove/move/resume.
  - `remove`/`move` (v12) : `video_id` + `index` (rang dans « à suivre », 0 = juste après le titre en cours, départage les doublons) ; `move` ajoute `to` (rang final). Chaque appareil retombe sur la première occurrence du titre si le rang est périmé.
- **Fichiers publics** servis depuis `/opt/pearconnect/dist/` :
  - `/install.ps1`, `/uninstall.ps1` (Windows) et `/install.sh`, `/uninstall.sh` (Linux, `curl … | bash`, gabarit `linux/install.sh.in`) ;
  - `/dl/app-asar.zip`, `/dl/pearconnect-ca.crt`, `/dl/PearConnect.apk` ;
  - `/versions.json` : versions publiées (`plugin.version`, `android.version_code/version_name/url`, commandes d'installation, SHA-256). Le plugin et l'appli le comparent à leur version pour signaler une mise à jour.
  - Générés par `windows/build_dist.py` dans `server/dist/`. La version de l'APK vient de `build.gradle.kts` sauf 4e argument `<code>:<nom>` (quand l'APK servi est plus ancien que les sources : aujourd'hui `8:0.4.0`).
- **Jetons** : un par appareil (`the-kernel`, `the_core`, `telephone`, `telecommande`).
  - Liste en clair chez l'utilisateur : `Documents\pearconnect\jetons.txt` sur the-kernel.
  - Ajout ou révocation : `connect_server.py --data-dir /var/lib/pearconnect add-device|remove-device <nom>` (pris en compte à chaud).

### Plugin Pear Connect — fork de Pear 3.12.0

- **Source de référence** : `forks/pear-desktop/plugin-src/` (copiée dans le dépôt de build par `tools/build_asar.py`).
  - Dépôt de build : `../build/pear-desktop/` sur the_core (archive du tag `v3.12.0` + les 4 patches, `git init` local). L'ancien `/home/claude/pear-fork` du conteneur n'existe plus.
  - Commits exportés dans `forks/pear-desktop/*.patch` (4), à appliquer sur le tag `v3.12.0` de pear-devs/pear-desktop.
  - **Tests unitaires** : `node --experimental-strip-types --test forks/pear-desktop/tests/protocol.test.ts` (21 tests, sans dépendance, Node ≥ 22.6).
- **Livraison** : seul `resources\app.asar` est remplacé, l'original est sauvegardé en `app.asar.original`.
  - L'asar est indépendant de la plateforme : construit sous Linux à partir de l'asar officiel 3.12.0 extrait, en remplaçant `dist/`.
- **backend.ts** (processus principal, Node) :
  - publie l'état toutes les 2 s si changement, sinon toutes les 15 s ;
  - long-poll des commandes ; SSE vers la page ;
  - repli sur `registerCallback` (song-info de Pear) si la page ne rapporte rien ;
  - journal `%APPDATA%\YouTube Music\pear-connect.log`.
- **client.ts** : `node:https` avec `caPath`. Il n'utilise pas fetch, pour pouvoir faire confiance à l'AC sans toucher au magasin Windows.
- **renderer.ts** (page YouTube Music) :
  - **UI** : barre « Lecture sur … », liste des appareils, panneau « À suivre » de l'appareil actif ; bouton dans `ytmusic-player-bar .right-controls-buttons` **ou** dans le nouveau `ytmusic-miniplayer .ytMusicMiniPlayerRightSection`.
  - **Rapport local** toutes les 1 s : position, file (50 titres), volume, mix lu dans `location`.
  - **Exécution** des commandes via l'API du lecteur `#movie_player` (`playVideo`/`pauseVideo`/`nextVideo`/`seekTo`/`setVolume`) ; `jump`/`remove`/`move` via le store de la file (`SET_INDEX`/`REMOVE_ITEM`/`MOVE_ITEM {fromIndex,toIndex}`, index final).
  - **Panneau « À suivre »** : boutons monter/descendre/retirer par ligne (v12).
  - **Reprise** : navigation interne `yt-navigate` + `watchEndpoint {videoId, playlistId, startTimeSeconds}`, sans redémarrer Pear. Ensuite, recalage de la position, puis remplacement des titres à venir par ceux de la source : `REMOVE_ITEM` + `/music/get_queue` + `ADD_ITEMS`.
- **protocol.ts** : types et fonctions pures (dont `isNewerVersion`, `pluginUpdate`).
- **version.ts** : `PLUGIN_VERSION` (« 12 »), à incrémenter à chaque livraison ; lue par `build_dist.py`.
- **menu.ts** : première ligne « Version : v12 (à jour) / — mise à jour vN disponible », sous-menu « Rechercher une mise à jour… » (boîte de dialogue, commande d'installation copiable selon la plateforme). Le menu lit l'état du backend (`backend.update`).
- **Mise à jour** : `backend.checkUpdate()` 15 s après le démarrage puis toutes les 6 h ; toast dans la page une fois par version proposée ; journalisé.

### Appli Android « Pear Connect » — `android/`

- **Version** : Java, AGP 8.9, minSdk 26, v0.5.0 (versionCode 9) dans les sources ; **APK 0.5.0 pas encore construit** (ni SDK Android ni keystore sur the_core) : `server/dist/PearConnect.apk` est toujours la 0.4.0.
- **Paquet** : `app.morphe.jam.companion`, **imposé** : c'est le seul que le manifeste patché de YTM rend visible.
- **Signature** : `android/signing/pearconnect.jks` (hors archive), SHA-256 `d6d95c84…`. Copie chez l'utilisateur : `Documents\pearconnect\android-signing`.
- **Pont Jam** :
  - l'appli est le service compagnon `IJamCompanion` lié par YTM, et elle lie le `JamBridgeService` de YTM ;
  - nom du service découvert par suffixe ; appairage `app.morphe.jam.PAIR` avec la `cap` de 64 hex.
- **Agent** : publie l'état depuis le SNAPSHOT du pont (clock en ms, items/autoplay), exécute les commandes et reprend une session via un intent VIEW. Il faut la permission « par-dessus les autres applis », sinon une notification.
- **Vue « Lecture sur … » dans YTM** : le compagnon répond `role: Participant` à l'opération `VIEW` quand un autre appareil joue (file, horloge, `playbackControl`). Les opérations PLAY/SKIP/SEEK/ADD/PLAY_NEXT/REMOVE/MOVE de YTM deviennent des `/v1/control` ; `END` correspond à « Quitter et lire ici ».
  - REMOVE/MOVE (0.5.0) : YTM désigne les lignes par les `id` de notre vue participant (`item`, `target`) → rangs de « à suivre ». Côté téléphone actif, `remove`/`move` du serveur deviennent des ops REMOVE/MOVE du pont avec `revision`, `lane` (0 file, 1 suggestions) et `item`/`target` ; le pont refuse un déplacement entre voies, on borne la cible dans la voie de départ ; une seconde tentative est faite si « Queue changed ». Sémantique de l'index final à vérifier sur appareil pour un déplacement vers le bas.
- **Sélecteur d'appareils** : `DevicesActivity`, accessible depuis la notification, la tuile des réglages rapides `DevicesTile` et l'appli.
- **Mise à jour** (0.5.0) : `Agent.checkUpdate` une fois par jour (`versions.json`, `Mapping.updateInfo` compare `version_code` à `BuildConfig.VERSION_CODE`) ; notification canal « update » une fois par version, qui ouvre l'URL de l'APK ; écran principal : ligne « Version x.y.z » et bouton « Rechercher une mise à jour ».
- **Sécurité réseau** : `res/raw/pearconnect_ca.crt` comme ancre de confiance (`network_security_config`).
- **Tests** : 18 tests JVM dans `MappingTest`. Sans Gradle : `javac` + junit 4.13.2 + hamcrest 1.3 + org.json 20240303 (jars Maven Central) suffisent pour `Mapping.java` + `MappingTest.java`.

### Patch Morphe (facultatif, non testé sur appareil)

- **Fork** : `/tmp/mph/morphe-patches`, branche `pear-connect`. Patch dans `forks/morphe-patches/`, plus `JamContext.java`.
- **Effet** : un hook « player response » mémorise `(videoId, playlistId)` ; le pont ajoute `clock.playlistId` ; le compagnon le publie comme `playlist_id`. Ainsi le mix suit du téléphone vers un PC.
- **Bundle** : `patches-1.47.0-pearconnect.1.mpp`. Construction sans GitHub Packages grâce à des builds composites de morphe-patcher, morphe-patches-library et du plugin Gradle 1.3.4 (publishToMavenLocal) et à l'init script miroir Maven Central (`maven-central.storage-download.googleapis.com`) ; signature des plugins désactivée.

### PKI du homelab

- **AC « Pear Connect CA 2026 »** : EC P-256, 10 ans, `pathlen:0`. Contraintes de nom **DNS uniquement** : `pearconnect`, `.lan`, `.home.arpa`. Les contraintes IP sont refusées par BoringSSL (Electron/Pear) : « unsupported name constraint type ».
- **Clé de l'AC** : chez l'utilisateur, `Documents\pearconnect\pki\` (avec `LISEZMOI.txt` pour le renouvellement). Elle n'est plus dans l'espace de travail.
- **Certificat serveur** : 825 jours ; SAN `IP:192.168.1.168`, `pearconnect`, `pearconnect.lan`.

### Outil `pearsync` (Python) — historique, toujours fonctionnel

CLI `pearsync` (`status/pull/push/transfer/link/auth/agent`) et agent qui passe par le plugin API Server de Pear. Il est **remplacé par le plugin sur les PC** (la tâche planifiée `pearsync-agent` y est désactivée). Utile en Linux, en mode direct et pour le dépannage. Tests : `python -m unittest tests/test_pearsync.py` (39 tests, faux Pear + serveur réel ; `test_tls_listener_with_private_ca` échoue sous Windows à cause de l'openssl de Git Bash, indépendamment du code).

## 3. Installation / déploiement

- **PC Windows** (installer ou mettre à jour) : `irm http://192.168.1.168:8787/install.ps1 | iex`.
- **PC Linux** : `curl -fsSL http://192.168.1.168:8787/install.sh | bash` (deb/rpm/archive ; AppImage à extraire d'abord). Même logique : version, SHA-256, jeton repris de `~/.config/YouTube Music/config.json` ou `~/.config/pearsync/config.toml`, sauvegarde `app.asar.original`, `sudo` seulement si `/opt/...` n'est pas inscriptible. **Pas encore testé sur une vraie machine Linux.**
  - Ce que fait le script : détection de Pear, contrôle de version, reprise du nom et du jeton (plugin ou ancien `config.toml` de l'agent), vérification du jeton, téléchargement avec contrôle SHA-256, sauvegarde, configuration, relance de Pear ; élévation automatique si nécessaire.
  - Désinstallation : `/uninstall.ps1`.
- **Téléphone** : `http://192.168.1.168:8787/dl/PearConnect.apk`, à installer par-dessus (même signature).
- **Publier une version** (depuis the_core, Windows ou Linux) :
  1. `python tools/build_asar.py --dist` : copie `plugin-src` dans `../build/pear-desktop`, oxlint + tsc, `electron-vite build`, extraction de `app.asar.original` (installation locale de Pear), remplacement de `dist/`, repack (`--unpack-dir assets`), zip, puis `windows/build_dist.py` (installeurs Windows + Linux avec SHA-256). Sortie dans `../build/out/` et `server/dist/`.
  2. `bash tools/deploy_dist.sh` : `scp` vers le Proxmox puis `pct push` dans `/opt/pearconnect/dist/`.
  3. Serveur : `bash tools/deploy_server.sh` (sauvegarde `*.bak-<date>`, `pct push`, `systemctl restart`, `healthz`).
  4. Mettre à jour les patches : commit dans `../build/pear-desktop` puis `git format-patch -N -o forks/pear-desktop/` (supprimer les anciens `.patch` avant).
  - Prérequis du dépôt de build (déjà faits sur the_core) : archive `codeload.github.com/pear-devs/pear-desktop/tar.gz/refs/tags/v3.12.0`, `git init` + `git am` des patches, `npm i -g pnpm@11`, `ELECTRON_SKIP_BINARY_DOWNLOAD=1 pnpm install --frozen-lockfile --ignore-scripts --config.engine-strict=false`, `npm i @electron/asar@3` dans `../build/tools`.
  - `tools/e2e/rebuild.sh` est l'ancien équivalent Linux du conteneur (chemins `/home/claude`, `/tmp/claude-0`) : obsolète.
- **Pont d'appareils** : `device_commit_files` refuse les fichiers de plus de 30 Mo ; passer par un zip. Un `stagedPath` réutilisé peut servir une version en cache : changer de nom.

## 4. Modifications déjà effectuées (chronologie courte)

1. pearsync CLI + agent + serveur Connect + télécommande web ; déploiement LXC.
2. Appli Android 0.1 → 0.2.2 :
   - nom de paquet imposé, `<queries>` explicites ;
   - découverte du pont par suffixe ;
   - permission de superposition.
3. Timecode dans l'URL de reprise (`&t=`) puis recalage.
4. Plugin Pear (le classifieur a refusé l'injection par `--remote-debugging-port`, d'où le choix du fork/plugin) :
   - navigation interne pour le mix (1-2 s) ;
   - prise en charge du nouveau mini-lecteur YTM ;
   - commandes via l'API du lecteur.
5. Volume, `jump`, `enqueue`, file avec titres (`queue`), HTTPS natif, télécommande avec volume et « À suivre ».
6. Appli 0.3.x :
   - mode participant Jam (vue « Lecture sur… » dans YTM) ;
   - AC embarquée ;
   - volume du flux média ;
   - `playlist_id` lu depuis le pont patché.
7. Patch Morphe « contexte de lecture » (bundle construit).
8. **Bug « titre intercalé »** : l'ancien `_load_track` insérait le titre après le morceau courant à chaque transfert. Corrigé (réutilise l'occurrence existante) et test de régression ajouté. Les files déjà abîmées se nettoient en lançant un nouveau mix.
9. AC remplacée (contraintes IP → DNS) ; appli 0.3.1.
10. Installation en une commande servie par le serveur ; appli 0.4.0 (sélecteur d'appareils, tuile).
11. Transfert = même file que la source (YouTube régénère les mix) ; reprise sur un Pear sans lecteur encore créé.
12. **v11 (12/10 ~22h)** :
    - état publié même si la page ne rapporte rien ;
    - journal ;
    - panneau « À suivre » de l'appareil actif sur PC ;
    - erreurs TLS silencieuses côté serveur.
13. **v12 (13/10, déployée : serveur + dist à 11:52, the_core réinstallé par `install.ps1` sous PowerShell 5.1 à 11:55)** :
    - file distribuée : commandes `remove`/`move` (serveur, plugin, télécommande web, compagnon Android 0.5.0 en source) ;
    - tests unitaires du plugin (`protocol.ts`, 18) ; test serveur remove/move ; MappingTest 17 ;
    - installeur Linux (`install.sh`/`uninstall.sh`) servi par le serveur ;
    - chaîne de build Windows/Linux sans conteneur (`tools/build_asar.py`, `tools/deploy_*.sh`) ;
    - dépôt git initialisé ; patches régénérés (4) ;
    - version du plugin affichée en tête du menu ; vérification de mise à jour plugin + Android via `versions.json` ;
    - dépôt GitHub privé `justinchlrd/pearconnect` (historique réécrit sans binaires), **Release `v12`** avec asar, installeurs, `versions.json`, AC et APK 0.4.0 ; README réécrit pour un lecteur extérieur (le dépôt va être partagé à un collègue).
    - Constat P0 : **the_core était resté en v10** (asar du 12/10 19:41 UTC, sans journal) ; v12 y a été installée à la main le 13/10 vers 11:25 (sauvegarde `app.asar.v10`).

## 5. Tâches restantes (priorisées)

### P0 — à valider sur le matériel réel (rien de ceci n'a été vérifié chez l'utilisateur)
0. **Finir le déploiement v12** (serveur, dist et the_core : faits le 13/10 ; l'utilisateur a autorisé explicitement le déploiement sur le Proxmox) :
   - `irm http://192.168.1.168:8787/install.ps1 | iex` sur **the-kernel** (toujours en v11 au mieux ; son plugin v11 ne sait pas encore signaler les mises à jour) ;
   - construire et signer l'APK 0.5.0 sur la machine qui a le keystore (the-kernel, `Documents\pearconnect\android-signing`), le copier dans `server/dist/`, relancer `python windows/build_dist.py … ` **sans** forcer `8:0.4.0`, puis `deploy_dist.sh` : le téléphone recevra alors la notification de mise à jour (une fois l'appli 0.5.0 installée… la 0.4.0 ne vérifie rien : première installation à la main).
1. **Transfert de file v11 entre the-kernel et the_core.** L'utilisateur signalait « la file ne suit pas ». Diagnostic : the-kernel publiait un état `null` (rapport de la page absent), donc le transfert partait d'une session périmée. Vérifier :
   - que les deux PC ont réinstallé v11 (`/install.ps1` dans `journalctl -u pearconnect`) ;
   - `pear-connect.log` sur la cible ;
   - le snapshot (`/v1/snapshot`, jeton `telecommande`) : `devices.<pc>.state.upcoming` non vide.
2. **Cause racine du rapport absent sur the-kernel** : `onPlayerApiReady` jamais appelé ? Le journal (lignes « page : … ») doit le dire.
3. **Appli 0.4.0** : vue participant dans YTM, sélecteur d'appareils, tuile, reprise sur le téléphone avec et sans permission de superposition.
4. ~~**Installeur sous Windows PowerShell 5.1**~~ : validé le 13/10 sur the_core (PS 5.1.26100, Pear dans `%LOCALAPPDATA%`, jeton repris du plugin, SHA-256 OK, Pear relancé). Reste à voir le cas Program Files + élévation sur the-kernel.

### P1
5. **Révoquer la clé SSH `claude_pearconnect`** dans `/root/.ssh/authorized_keys` du Proxmox quand le travail est fini.
6. **Fermer l'HTTP `:8787`** (`--port 0`) après migration. Pour cela, servir l'installeur en HTTPS (installer d'abord l'AC, ou fournir un amorçage qui épingle l'empreinte). Aujourd'hui le script transite en HTTP : acceptable seulement sur le LAN ou le VPN.
7. **Tester le bundle Morphe** sur le téléphone (repatcher YTM avec ce bundle comme source) et vérifier que `playlist_id` du téléphone n'est pas vide.
8. **File distribuée** :
   - « Ajouter à la file » / « Lire ensuite » de YTM sur un appareil **inactif** doit aller vers l'appareil actif : intercepter côté plugin, sans confondre avec les ajouts automatiques de YTM (**reste à faire**) ;
   - ~~supprimer et réordonner à distance~~ : fait en v12 (`remove`/`move`), à valider sur le matériel (notamment le téléphone actif : sémantique de `target` du pont pour un déplacement vers le bas).
9. **Tests automatisés du plugin** : ~~pas de tests unitaires TS~~ `protocol.ts` est couvert (18 tests, `node --test`). `renderer.ts`/`backend.ts` restent sans test ; le harnais e2e `tools/e2e/` dépendait du conteneur et n'a pas été porté sous Windows.

### P2
10. **Nouvelle version de Pear** : rebaser `forks/pear-desktop/*.patch` sur le nouveau tag, reconstruire l'asar, mettre à jour `PEAR_VERSION` dans `build_dist.py`. L'installeur refuse une version différente.
11. **Nettoyage sur les PC** : tâche `pearsync-agent` désactivée, `%APPDATA%\pearsync\config.toml` (contient un jeton), plugin API Server de Pear (plus nécessaire, à désactiver).
12. **Durcissement** :
    - jetons statiques sans expiration ;
    - pas de limitation de débit sur le serveur ;
    - `config.json` de Pear contient le jeton (ACL utilisateur seulement).
13. **Mix au-delà de 50 titres** : YouTube régénère la suite, et chaque appareil peut diverger ensuite (seul l'appareil actif compte).

### P3
14. **Patch Morphe « Pear Connect natif »** (demande du 13/10) : bouton de sélection d'appareil **dans** YTM Android, et **ne plus passer par Jam** (le mode participant monopolise la session Jam : impossible d'écouter ensemble avec des amis tout en transférant sa lecture). Pistes, par coût croissant :
    - court terme sans patch : fournisseur `MediaRoute2ProviderService` dans l'appli compagnon → les appareils Connect apparaissent dans le sélecteur de sortie d'Android (icône « cast » de YTM et des réglages rapides) ; YTM n'est pas modifié, mais le transfert reste piloté par le compagnon ;
    - patch Morphe « bouton Pear Connect » : réutiliser `JamUIInstaller`/`JamBar` (déjà des hooks sur la barre du lecteur) pour un bouton qui ouvre `DevicesActivity` via intent explicite ; faible coût, dépend du compagnon ;
    - patch Morphe « Pear Connect sans Jam » : nouveau service compagnon (`IPearConnect`) ne touchant pas à `JamBridgeService`, avec son propre `SNAPSHOT`/`PLAY`/`SEEK` (réutiliser `YouTubeMusicBridge.QueueAccess`, `JamClock`, `JamPlayback`) et une barre « Lecture sur … » séparée de la barre Jam. C'est le vrai découplage ; la base Morphe (fork `forks/morphe-patches`, bundle composite) est déjà en place mais le patch 1.47 « contexte de lecture » n'a pas encore été testé sur l'appareil.
15. Renommage côté UI Jam : YTM affiche « Écouter ensemble / connecté en Wi-Fi » en mode participant (chaînes du patch).

## 6. Pièges connus

- **Pear** : les plugins sont compilés dans l'appli (pas de chargement externe).
- **Navigation `yt-navigate`** : elle doit partir de `ytmusic-app` avec `bubbles`/`composed` ; `app.navigate()` ne marche pas.
- **Transfert entre PC** : la file ne se synchronise pas toute seule ; c'est le transfert qui la recopie.
- **BoringSSL** (Electron) : refuse les contraintes de nom IP.
- **Commandes `pkill -f <motif>`** dans ce conteneur : elles tuent le shell si le motif apparaît dans la commande ; passer par un script.
- **Classifieur de sécurité de l'agent** : il refuse ce qui ressemble à de la persistance (raccourcis, clés Run, port de débogage) et le **déploiement en production** (`scp`/`pct push`/`systemctl restart` vers le Proxmox). Ne pas contourner : laisser l'utilisateur lancer `tools/deploy_*.sh`.
- **Build sous Windows** : corepack de Node 22.13 refuse la signature de pnpm (« Cannot find matching keyid ») → `npm i -g pnpm@11` ; Pear exige pnpm ≥ 11 et node-gyp exige Node ≥ 22.22 → `--config.engine-strict=false` ; `--ignore-scripts` + `ELECTRON_SKIP_BINARY_DOWNLOAD=1` suffisent pour `electron-vite build`.
- **asar extract** cherche `<archive>.unpacked/` à côté de l'archive : extraire une copie de `app.asar.original` avec son `app.asar.unpacked` recopié sous le même nom (`tools/build_asar.py` le fait).
- **oxlint** (`camelcase`) refuse une propriété raccourcie `video_id` : écrire `video_id: videoId`.
- **`rm -f "$VAR"/*`** est bloqué par le garde-fou de l'agent : écrire `"${VAR:?}"/*`.
