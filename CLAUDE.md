# Pear Connect — passation (état au 13/10/2026)

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
- **Homelab** : Proxmox « the-nexus » 192.168.1.10. Accès : `ssh proxmox` depuis la VM du pont d'appareils, avec la clé `claude_pearconnect` (**à révoquer**, voir tâches).

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
  - Commandes : pause/play/next/previous/seek/volume/jump/enqueue/resume.
- **Fichiers publics** servis depuis `/opt/pearconnect/dist/` :
  - `/install.ps1`, `/uninstall.ps1` ;
  - `/dl/app-asar.zip`, `/dl/pearconnect-ca.crt`, `/dl/PearConnect.apk`.
  - Générés par `windows/build_dist.py` dans `server/dist/`.
- **Jetons** : un par appareil (`the-kernel`, `the_core`, `telephone`, `telecommande`).
  - Liste en clair chez l'utilisateur : `Documents\pearconnect\jetons.txt` sur the-kernel.
  - Ajout ou révocation : `connect_server.py --data-dir /var/lib/pearconnect add-device|remove-device <nom>` (pris en compte à chaud).

### Plugin Pear Connect — fork de Pear 3.12.0

- **Source** : `/home/claude/pear-fork`, branche `pear-connect`, plugin dans `src/plugins/pear-connect/`.
  - Copie dans `forks/pear-desktop/plugin-src/`.
  - Commits exportés dans `forks/pear-desktop/*.patch`, à appliquer sur le tag `v3.12.0` de pear-devs/pear-desktop.
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
  - **Exécution** des commandes via l'API du lecteur `#movie_player` (`playVideo`/`pauseVideo`/`nextVideo`/`seekTo`/`setVolume`).
  - **Reprise** : navigation interne `yt-navigate` + `watchEndpoint {videoId, playlistId, startTimeSeconds}`, sans redémarrer Pear. Ensuite, recalage de la position, puis remplacement des titres à venir par ceux de la source : `REMOVE_ITEM` + `/music/get_queue` + `ADD_ITEMS`.
- **protocol.ts** : types et fonctions pures.

### Appli Android « Pear Connect » — `android/`

- **Version** : Java, AGP 8.9, minSdk 26, v0.4.0 (versionCode 8).
- **Paquet** : `app.morphe.jam.companion`, **imposé** : c'est le seul que le manifeste patché de YTM rend visible.
- **Signature** : `android/signing/pearconnect.jks` (hors archive), SHA-256 `d6d95c84…`. Copie chez l'utilisateur : `Documents\pearconnect\android-signing`.
- **Pont Jam** :
  - l'appli est le service compagnon `IJamCompanion` lié par YTM, et elle lie le `JamBridgeService` de YTM ;
  - nom du service découvert par suffixe ; appairage `app.morphe.jam.PAIR` avec la `cap` de 64 hex.
- **Agent** : publie l'état depuis le SNAPSHOT du pont (clock en ms, items/autoplay), exécute les commandes et reprend une session via un intent VIEW. Il faut la permission « par-dessus les autres applis », sinon une notification.
- **Vue « Lecture sur … » dans YTM** : le compagnon répond `role: Participant` à l'opération `VIEW` quand un autre appareil joue (file, horloge, `playbackControl`). Les opérations PLAY/SKIP/SEEK/ADD/PLAY_NEXT de YTM deviennent des `/v1/control` ; `END` correspond à « Quitter et lire ici ».
- **Sélecteur d'appareils** : `DevicesActivity`, accessible depuis la notification, la tuile des réglages rapides `DevicesTile` et l'appli.
- **Sécurité réseau** : `res/raw/pearconnect_ca.crt` comme ancre de confiance (`network_security_config`).
- **Tests** : 15 tests JVM dans `MappingTest`.

### Patch Morphe (facultatif, non testé sur appareil)

- **Fork** : `/tmp/mph/morphe-patches`, branche `pear-connect`. Patch dans `forks/morphe-patches/`, plus `JamContext.java`.
- **Effet** : un hook « player response » mémorise `(videoId, playlistId)` ; le pont ajoute `clock.playlistId` ; le compagnon le publie comme `playlist_id`. Ainsi le mix suit du téléphone vers un PC.
- **Bundle** : `patches-1.47.0-pearconnect.1.mpp`. Construction sans GitHub Packages grâce à des builds composites de morphe-patcher, morphe-patches-library et du plugin Gradle 1.3.4 (publishToMavenLocal) et à l'init script miroir Maven Central (`maven-central.storage-download.googleapis.com`) ; signature des plugins désactivée.

### PKI du homelab

- **AC « Pear Connect CA 2026 »** : EC P-256, 10 ans, `pathlen:0`. Contraintes de nom **DNS uniquement** : `pearconnect`, `.lan`, `.home.arpa`. Les contraintes IP sont refusées par BoringSSL (Electron/Pear) : « unsupported name constraint type ».
- **Clé de l'AC** : chez l'utilisateur, `Documents\pearconnect\pki\` (avec `LISEZMOI.txt` pour le renouvellement). Elle n'est plus dans l'espace de travail.
- **Certificat serveur** : 825 jours ; SAN `IP:192.168.1.168`, `pearconnect`, `pearconnect.lan`.

### Outil `pearsync` (Python) — historique, toujours fonctionnel

CLI `pearsync` (`status/pull/push/transfer/link/auth/agent`) et agent qui passe par le plugin API Server de Pear. Il est **remplacé par le plugin sur les PC** (la tâche planifiée `pearsync-agent` y est désactivée). Utile en Linux, en mode direct et pour le dépannage. Tests : `python -m unittest tests/test_pearsync.py` (38 tests, faux Pear + serveur réel).

## 3. Installation / déploiement

- **PC** (installer ou mettre à jour) : `irm http://192.168.1.168:8787/install.ps1 | iex`.
  - Ce que fait le script : détection de Pear, contrôle de version, reprise du nom et du jeton (plugin ou ancien `config.toml` de l'agent), vérification du jeton, téléchargement avec contrôle SHA-256, sauvegarde, configuration, relance de Pear ; élévation automatique si nécessaire.
  - Désinstallation : `/uninstall.ps1`.
- **Téléphone** : `http://192.168.1.168:8787/dl/PearConnect.apk`, à installer par-dessus (même signature).
- **Publier une version** :
  1. `tools/e2e/rebuild.sh` : build du fork et repack de l'asar.
  2. Zipper l'asar.
  3. `python3 windows/build_dist.py <app-asar.zip> <ca.crt> <apk>`.
  4. Copier `server/dist/*` dans `/opt/pearconnect/dist/` : via `Documents\pearconnect\server\vN` puis `scp proxmox` + `pct push`.
  - Le serveur s'installe de la même façon (`pct push` + `systemctl restart pearconnect`).
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
12. **v11 (dernière, 12/10 ~22h)** :
    - état publié même si la page ne rapporte rien ;
    - journal ;
    - panneau « À suivre » de l'appareil actif sur PC ;
    - erreurs TLS silencieuses côté serveur.

## 5. Tâches restantes (priorisées)

### P0 — à valider sur le matériel réel (rien de ceci n'a été vérifié chez l'utilisateur)
1. **Transfert de file v11 entre the-kernel et the_core.** L'utilisateur signalait « la file ne suit pas ». Diagnostic : the-kernel publiait un état `null` (rapport de la page absent), donc le transfert partait d'une session périmée. Vérifier :
   - que les deux PC ont réinstallé v11 (`/install.ps1` dans `journalctl -u pearconnect`) ;
   - `pear-connect.log` sur la cible ;
   - le snapshot (`/v1/snapshot`, jeton `telecommande`) : `devices.<pc>.state.upcoming` non vide.
2. **Cause racine du rapport absent sur the-kernel** : `onPlayerApiReady` jamais appelé ? Le journal (lignes « page : … ») doit le dire.
3. **Appli 0.4.0** : vue participant dans YTM, sélecteur d'appareils, tuile, reprise sur le téléphone avec et sans permission de superposition.
4. **Installeur sous Windows PowerShell 5.1** : testé seulement sous pwsh 7 + shims. Points à surveiller : `irm | iex`, ConvertTo-Json et Remove-TypeData, élévation, ACL icacls sur `config.json`.

### P1
5. **Révoquer la clé SSH `claude_pearconnect`** dans `/root/.ssh/authorized_keys` du Proxmox quand le travail est fini.
6. **Fermer l'HTTP `:8787`** (`--port 0`) après migration. Pour cela, servir l'installeur en HTTPS (installer d'abord l'AC, ou fournir un amorçage qui épingle l'empreinte). Aujourd'hui le script transite en HTTP : acceptable seulement sur le LAN ou le VPN.
7. **Tester le bundle Morphe** sur le téléphone (repatcher YTM avec ce bundle comme source) et vérifier que `playlist_id` du téléphone n'est pas vide.
8. **File distribuée** :
   - « Ajouter à la file » / « Lire ensuite » de YTM sur un appareil **inactif** doit aller vers l'appareil actif : intercepter côté plugin, sans confondre avec les ajouts automatiques de YTM ;
   - supprimer et réordonner à distance : nouvelles commandes `remove` et `move` côté serveur, plugin et compagnon (les ops Jam REMOVE/MOVE existent déjà côté YTM).
9. **Tests automatisés du plugin** : pas de tests unitaires TS ; `protocol.ts` s'y prête. Le harnais e2e `tools/e2e/` dépend du conteneur (Xvfb, proxy dont le port change, YouTube qui bloque parfois l'IP).

### P2
10. **Nouvelle version de Pear** : rebaser `forks/pear-desktop/*.patch` sur le nouveau tag, reconstruire l'asar, mettre à jour `PEAR_VERSION` dans `build_dist.py`. L'installeur refuse une version différente.
11. **Nettoyage sur les PC** : tâche `pearsync-agent` désactivée, `%APPDATA%\pearsync\config.toml` (contient un jeton), plugin API Server de Pear (plus nécessaire, à désactiver).
12. **Durcissement** :
    - jetons statiques sans expiration ;
    - pas de limitation de débit sur le serveur ;
    - `config.json` de Pear contient le jeton (ACL utilisateur seulement).
13. **Mix au-delà de 50 titres** : YouTube régénère la suite, et chaque appareil peut diverger ensuite (seul l'appareil actif compte).

### P3
14. Sélecteur d'appareils **dans** YTM Android : patch Morphe supplémentaire, ou fournisseur MediaRoute2 pour le sélecteur de sortie d'Android.
15. Renommage côté UI Jam : YTM affiche « Écouter ensemble / connecté en Wi-Fi » en mode participant (chaînes du patch).

## 6. Pièges connus

- **Pear** : les plugins sont compilés dans l'appli (pas de chargement externe).
- **Navigation `yt-navigate`** : elle doit partir de `ytmusic-app` avec `bubbles`/`composed` ; `app.navigate()` ne marche pas.
- **Transfert entre PC** : la file ne se synchronise pas toute seule ; c'est le transfert qui la recopie.
- **BoringSSL** (Electron) : refuse les contraintes de nom IP.
- **Commandes `pkill -f <motif>`** dans ce conteneur : elles tuent le shell si le motif apparaît dans la commande ; passer par un script.
- **Classifieur de sécurité de l'agent** : il refuse ce qui ressemble à de la persistance (raccourcis, clés Run, port de débogage). Ne pas contourner.
