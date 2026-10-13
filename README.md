# Pear Connect — un « Spotify Connect » pour YouTube Music

Pear Connect fait communiquer plusieurs lecteurs YouTube Music (PC sous **Pear Desktop**, téléphone Android sous **YouTube Music Morphe**) à travers un petit serveur auto-hébergé : chaque appareil voit ce qui joue ailleurs, peut **reprendre la lecture ici** (titre, position, mix d'origine, file « à suivre ») et **piloter l'appareil qui joue** (lecture/pause, suivant, position, volume, file). Comme Spotify Connect, un seul appareil joue à la fois.

Projet personnel pour un homelab, mais chaque composant est générique : les adresses du homelab (`192.168.1.168`, ports `8787`/`8443`) sont des constantes faciles à changer (voir [Adapter à un autre réseau](#adapter-à-un-autre-réseau)).

## Sommaire

1. [Fonctionnalités](#fonctionnalités)
2. [Architecture](#architecture)
3. [Prérequis](#prérequis)
4. [Installation](#installation)
5. [Utilisation](#utilisation)
6. [Construire et publier une version](#construire-et-publier-une-version)
7. [Développement et tests](#développement-et-tests)
8. [API du serveur Connect](#api-du-serveur-connect)
9. [Sécurité](#sécurité)
10. [Dépannage](#dépannage)
11. [Outil historique `pearsync`](#outil-historique-pearsync)
12. [Documents](#documents)

## Fonctionnalités

- **Voir** sur chaque appareil ce qui joue ailleurs : barre « Lecture sur … » dans Pear Desktop, barre « Écouter ensemble » dans YouTube Music Android (mode participant du patch Jam), télécommande web.
- **Transférer** la lecture (« Écouter ici ») : le titre s'ouvre **dans son mix ou sa playlist d'origine**, à la même position, et les titres « à suivre » sont recopiés ; la source se met en pause.
- **Piloter** l'appareil actif : lecture/pause, précédent/suivant, déplacement dans le titre, volume, sauter à un titre de la file.
- **File distribuée** : retirer ou réordonner les titres « à suivre » de l'appareil actif depuis n'importe quel autre appareil (v12).
- **Mises à jour** : le serveur publie `versions.json` ; le plugin Pear (menu Plugins › Pear Connect, ligne « Version ») et l'appli Android signalent une version plus récente et donnent la commande ou le lien d'installation (v12).
- **Installation en une commande** sur PC, Windows (`irm … | iex`) ou Linux (`curl … | bash`), servie par le serveur lui-même, avec contrôle SHA-256.

## Architecture

```
 PC Windows / Linux                                          PC Windows / Linux
 Pear Desktop 3.12 + plugin ─┐   HTTPS :8443 (AC privée)   ┌─ Pear Desktop 3.12 + plugin
                             ├─────►  Serveur Connect  ◄────┤
 Téléphone Android ──────────┘   Python stdlib, LXC/VM      └─ Télécommande web (navigateur)
  appli compagnon Pear Connect  ⇅ Binder ⇅  YouTube Music Morphe (patch « Jam queue sharing »)
```

| Composant | Dossier | Technologie | Rôle |
|---|---|---|---|
| Serveur Connect | `server/` | Python 3.11, bibliothèque standard uniquement | Autorité sur la session : appareils, appareil actif, état (titre, position, mix, file, volume), commandes en long-poll, flux SSE, fichiers d'installation, `versions.json`. Un jeton par appareil, stocké haché. |
| Télécommande web | `server/web/index.html` | HTML/JS sans dépendance | Servie par le serveur à `/`. Lecture en cours, commandes, volume, file « à suivre » éditable, transfert. |
| Plugin Pear Desktop | `forks/pear-desktop/` | TypeScript, plugin du fork [pear-devs/pear-desktop](https://github.com/pear-devs/pear-desktop) 3.12.0 | `plugin-src/` est la source de référence, `*.patch` la série à appliquer sur le tag `v3.12.0`. Livré sous forme d'`app.asar` de remplacement, identique sur Windows et Linux. |
| Appli Android | `android/` | Java, AGP 8.9, minSdk 26 | Service compagnon du patch Morphe « Jam queue sharing » : publie l'état de YouTube Music, exécute les commandes, reprend une session, affiche « Lecture sur … » dans YouTube Music. Paquet imposé `app.morphe.jam.companion`. |
| Patch Morphe (facultatif) | `forks/morphe-patches/` | Kotlin/Java | Ajoute l'identifiant du mix au pont Jam, pour que le mix suive aussi du téléphone vers un PC. Non testé sur appareil. |
| Installeurs | `windows/`, `linux/` | PowerShell 5.1+, bash | Gabarits `install.ps1.in` / `install.sh.in`, instanciés par `windows/build_dist.py`. |
| Chaîne de build | `tools/` | Python, bash | `build_asar.py` (asar + `server/dist/`), `deploy_server.sh`, `deploy_dist.sh`. |
| Outil historique | `pearsync/`, `tests/` | Python | CLI + agent passant par le plugin API Server de Pear ; remplacé par le plugin sur les PC, encore utile pour le dépannage. |

Principe de fonctionnement : chaque appareil **publie** son état toutes les 2 s quand il change (`PUT /v1/devices/<nom>/state`) et **relève** ses commandes en long-poll. Quand un appareil se met à jouer, le serveur le déclare actif et envoie `pause` à l'ancien. « Écouter ici » (`POST /v1/transfer`) envoie à la cible une commande `resume` contenant l'état complet, position extrapolée au moment de la remise.

## Prérequis

- **PC** : Pear Desktop **3.12.0** exactement (l'installeur refuse une autre version ; voir [Nouvelle version de Pear](#nouvelle-version-de-pear)). Windows 10/11 ou Linux (paquet deb/rpm, archive, AppImage extraite).
- **Téléphone** : Android 8+ avec YouTube Music **Morphe** et le patch « Jam queue sharing » (Morphe patches ≥ 1.47).
- **Serveur** : une machine Linux avec Python ≥ 3.11 (ici un conteneur LXC Debian 12 sur Proxmox), joignable des PC et du téléphone (LAN ou VPN).
- **TLS** : une autorité de certification privée. Son certificat public est distribué par l'installeur (PC) et embarqué dans l'APK (`android/app/src/main/res/raw/pearconnect_ca.crt`). Contraintes de nom **DNS uniquement** : BoringSSL (Electron) refuse les contraintes IP.

## Installation

### Serveur Connect

1. Copier `server/connect_server.py` et `server/web/index.html` ; créer l'utilisateur, le dossier de données et l'unité systemd : les commandes sont en tête de [`server/pearconnect.service`](server/pearconnect.service).
2. Certificat serveur signé par l'AC privée dans `/etc/pearconnect/` (chaîne serveur + AC, clé lisible par l'utilisateur `pearconnect`).
3. Créer un jeton par appareil (affiché une seule fois) :

   ```
   runuser -u pearconnect -- python3 /opt/pearconnect/connect_server.py --data-dir /var/lib/pearconnect add-device the-kernel
   ```

   `remove-device <nom>` révoque, `list-devices` liste ; les changements sont pris en compte sans redémarrage.
4. Déposer les fichiers publics dans `/opt/pearconnect/dist/` (voir [Construire et publier](#construire-et-publier-une-version)).

Le serveur écoute en HTTPS `:8443` et, pour l'installation en une commande, en HTTP `:8787` (`--port 0` pour le fermer). Exposition hors du réseau local : VPN, ou reverse proxy TLS (`server/Caddyfile.example`).

### PC Windows

Dans PowerShell (5.1 ou 7), Pear Desktop ouvert :

```
irm http://192.168.1.168:8787/install.ps1 | iex
```

Le script trouve Pear, vérifie sa version, reprend le nom et le jeton de l'ordinateur s'il les trouve (plugin déjà installé ou ancien agent), sinon les demande et les vérifie auprès du serveur ; télécharge `app.asar` et l'AC en contrôlant leur SHA-256 ; sauvegarde l'original en `app.asar.original` ; configure le plugin (`%APPDATA%\YouTube Music\config.json`) ; relance Pear. Il se relance en administrateur si Pear est dans Program Files. Retour à Pear d'origine : `irm http://192.168.1.168:8787/uninstall.ps1 | iex`.

### PC Linux

```
curl -fsSL http://192.168.1.168:8787/install.sh | bash
```

Même logique (deb/rpm, archive ou AppImage extraite avec `--appimage-extract`), configuration dans `~/.config/YouTube Music/config.json`, `sudo` seulement si le dossier de Pear n'est pas inscriptible. Retour : `uninstall.sh`. Pas encore validé sur une vraie machine Linux.

### Téléphone Android

1. Installer l'APK : `http://192.168.1.168:8787/dl/PearConnect.apk` (par-dessus l'ancienne version, même signature).
2. Dans YouTube Music Morphe : activer « Activer le partage de la file d'attente Jam », vérifier que « Paquet Jam Layer » vaut `app.morphe.jam.companion`, redémarrer YouTube Music.
3. Dans le lecteur : « Écouter ensemble » → « Configurer Jam Layer », accepter l'appairage dans Pear Connect.
4. Dans Pear Connect : adresse du serveur (`https://192.168.1.168:8443`), nom de l'appareil, jeton, « Enregistrer et démarrer ». Autorisations recommandées : « par-dessus les autres applis » (ouverture automatique de YouTube Music lors d'un transfert) et batterie non restreinte.
5. Facultatif, pour que le mix suive du téléphone vers un PC : repatcher YouTube Music avec le bundle `patches-1.47.0-pearconnect.1.mpp` construit depuis `forks/morphe-patches/`.

### Télécommande web

`https://192.168.1.168:8443` (AC à faire accepter par le navigateur) ou `http://192.168.1.168:8787` : coller le jeton d'un appareil (par exemple un jeton `telecommande` dédié).

## Utilisation

- **PC** : bouton « appareils » dans la barre du lecteur de YouTube Music (classique ou nouveau mini-lecteur). Quand un autre appareil joue, une barre « Lecture sur … » apparaît en bas : titre, progression en direct, commandes, volume, file « À suivre » (sauter, monter, descendre, retirer), « Écouter ici ». Menu Plugins › Pear Connect : version, recherche de mise à jour, serveur, nom, jeton, AC, télécommande.
- **Téléphone** : quand un PC joue, YouTube Music affiche la barre « Écouter ensemble » avec la file, la progression et les commandes, renvoyées au PC ; « Quitter et lire ici » reprend la lecture sur le téléphone. Le sélecteur d'appareils est accessible depuis la notification, la tuile des réglages rapides et l'appli.
- **Mises à jour** : le plugin vérifie `versions.json` au démarrage puis toutes les 6 h et affiche un message une fois par nouvelle version ; l'appli Android vérifie une fois par jour et notifie. L'installation reste manuelle (commande ou APK) : rien ne se met à jour tout seul.

## Construire et publier une version

Tout se fait depuis un PC Windows ou Linux, sans conteneur. Chemins relatifs à la racine du dépôt ; le dossier de build `../build/` est hors dépôt.

### Préparer l'environnement (une fois)

```
mkdir ../build && cd ../build
curl -L -o pear.tgz https://codeload.github.com/pear-devs/pear-desktop/tar.gz/refs/tags/v3.12.0
tar xzf pear.tgz && mv pear-desktop-3.12.0 pear-desktop && cd pear-desktop
git init && git add -A && git commit -qm "pear-desktop v3.12.0" && git am ../../pearsync/forks/pear-desktop/*.patch
npm i -g pnpm@11
ELECTRON_SKIP_BINARY_DOWNLOAD=1 pnpm install --frozen-lockfile --ignore-scripts --config.engine-strict=false
mkdir ../tools && cd ../tools && npm init -y && npm i @electron/asar@3
```

Il faut aussi une installation locale de Pear 3.12.0 : `build_asar.py` repart de son `app.asar.original` (ou `--original <chemin>`).

### Construire

```
python tools/build_asar.py --dist [--apk-version <code>:<nom>]
```

Copie `forks/pear-desktop/plugin-src/` dans le dépôt de build, passe oxlint et tsc, `electron-vite build`, extrait l'asar officiel, remplace `dist/`, réempaquette (`--unpack-dir assets`), zippe, puis `windows/build_dist.py` régénère `server/dist/` : installeurs Windows et Linux avec les SHA-256, `versions.json`. `--apk-version` sert quand l'APK servi est plus ancien que les sources Android.

Avant de construire une version : incrémenter `PLUGIN_VERSION` dans `forks/pear-desktop/plugin-src/version.ts` (et `versionCode`/`versionName` dans `android/app/build.gradle.kts` pour l'appli).

### Publier

```
bash tools/deploy_server.sh      # serveur + télécommande : sauvegarde, pct push, redémarrage, healthz
bash tools/deploy_dist.sh        # server/dist/ → /opt/pearconnect/dist/ (installeurs, asar, versions.json, APK)
```

Les deux scripts passent par `ssh root@<proxmox>` puis `pct push` dans le conteneur (`PVE` et `CT` surchargeables). Puis sur chaque PC : relancer l'installeur. Archiver aussi la version en Release GitHub (tag `vN`, mêmes fichiers).

Mettre à jour les patches après un changement du plugin : commit dans `../build/pear-desktop`, puis `git format-patch -<n> -o forks/pear-desktop/` (supprimer les anciens `.patch` avant).

### Appli Android

Gradle avec le SDK Android (compileSdk 35) : `./gradlew assembleRelease`. La signature vient d'un `android/signing.properties` (hors dépôt) pointant sur le keystore ; **garder la même clé**, sinon l'appli ne s'installe plus par-dessus et YouTube Music refuse l'appairage. Copier l'APK dans `server/dist/` puis relancer `build_dist.py` et `deploy_dist.sh`.

### Nouvelle version de Pear

Rebaser `forks/pear-desktop/*.patch` sur le nouveau tag, reconstruire, mettre à jour `PEAR_VERSION` dans `windows/build_dist.py` et `tools/build_asar.py` (l'installeur refuse une version différente).

### Adapter à un autre réseau

Adresses du serveur : `BASE` et `CONNECT` dans `windows/build_dist.py` (installeurs, `versions.json`), `DEFAULT_SERVER` dans `android/app/src/main/java/app/morphe/jam/companion/Prefs.java`, `PVE`/`CT` des scripts de déploiement. Remplacer l'AC : `android/app/src/main/res/raw/pearconnect_ca.crt` et `server/dist/pearconnect-ca.crt`.

## Développement et tests

```
python -m unittest tests/test_pearsync.py                                          # 39 tests : serveur Connect réel + faux Pear, agent, CLI
node --experimental-strip-types --test forks/pear-desktop/tests/protocol.test.ts   # 21 tests : fonctions pures du plugin (Node ≥ 22.6)
ruff check . && mypy pearsync server/connect_server.py                             # qualité Python
```

Tests Android (JVM, sans appareil) : `./gradlew test`, ou sans Gradle avec `javac` + junit 4.13.2, hamcrest 1.3 et org.json sur `Mapping.java` et `MappingTest.java` (18 tests). Sous Windows, `test_tls_listener_with_private_ca` échoue à cause de l'openssl de Git Bash ; ce n'est pas lié au code.

Le plugin n'a pas de test d'interface automatisé : `tools/e2e/` est l'ancien harnais (Xvfb + Pear + CDP) qui tournait dans un conteneur Linux, il n'a pas été porté.

Conventions : commits en français, type conventionnel (`feat:`, `fix:`, `chore:`, `docs:`, `build:`), fins de ligne LF (`.gitattributes`). `CLAUDE.md` est le document de passation détaillé (état, pièges, tâches).

## API du serveur Connect

Tout exige `Authorization: Bearer <jeton>`, sauf `/`, `/healthz`, les installeurs et `/versions.json`.

| Méthode et chemin | Rôle |
|---|---|
| `GET /healthz` | `{"ok": true}` |
| `GET /v1/snapshot` | État complet : appareils (en ligne, état, file), appareil actif, session |
| `GET /v1/events` | Flux `text/event-stream` : un snapshot à chaque changement, keepalive toutes les 20 s |
| `PUT /v1/devices/<nom>/state` | Publier son état (`{"kind","state"}`), jeton de `<nom>` uniquement |
| `POST /v1/devices/<nom>/commands/take?wait=N` | Relever ses commandes (long-poll ≤ 25 s) |
| `POST /v1/devices/<nom>/commands` | Envoyer une commande à un appareil (`pause`, `play`, `next`, `previous`, `seek`, `volume`, `jump`, `enqueue`, `remove`, `move`, `resume`) |
| `POST /v1/control` | Même chose vers l'appareil actif (`{"action": …}`, sans `resume`) |
| `POST /v1/transfer` | `{"target", "source"?, "with_queue"?}` : faire reprendre la session sur `target` |
| `GET /versions.json`, `/install.ps1`, `/install.sh`, `/dl/…` | Fichiers publics de `server/dist/` |

`remove`/`move` prennent `video_id` et `index` (rang dans « à suivre », 0 = juste après le titre en cours, pour départager les doublons) ; `move` ajoute `to` (rang final). Les positions sont en secondes ; le serveur extrapole la position des états en lecture.

## Sécurité

- Un jeton par appareil, haché côté serveur ; tout jeton permet de piloter la lecture, mais un appareil ne peut publier que son propre état. Révocation immédiate avec `remove-device`.
- HTTPS avec une AC privée ; le plugin passe par `node:https` avec `caPath` pour ne pas toucher au magasin du système ; l'appli Android embarque l'AC dans sa configuration réseau.
- Le port HTTP `8787` ne sert qu'à l'amorçage (installeurs servis en clair, avec SHA-256 inscrits dans le script) : à réserver au LAN ou au VPN, puis à fermer (`--port 0`).
- Durcissement de l'unité systemd (`ProtectSystem=strict`, `NoNewPrivileges`, pas de capacités). Points restants : jetons sans expiration, pas de limitation de débit, jeton en clair dans `config.json` de Pear (ACL utilisateur seulement) et dans les préférences de l'appli.
- Ne jamais exposer le port 26538 de l'API Server de Pear sur Internet.

## Dépannage

| Symptôme | Piste |
|---|---|
| Un appareil est « hors ligne » | Le plugin ou l'appli ne tourne pas, ou le jeton est faux : `pear-connect.log` (dossier de configuration de Pear), notification de l'appli, `journalctl -u pearconnect` |
| « La file ne suit pas » au transfert | La source publiait un état sans file : vérifier dans `/v1/snapshot` que `devices.<source>.state.upcoming` n'est pas nul ; le journal du plugin indique si la page rapporte son état |
| « jeton refusé par le serveur » | Jeton révoqué ou mal recopié : `add-device` puis relancer l'installeur |
| L'installeur refuse la version | Pear n'est pas en 3.12.0 : voir [Nouvelle version de Pear](#nouvelle-version-de-pear) |
| Pas de barre « Écouter ensemble » sur le téléphone | Appairage Jam à refaire (Morphe), ou case « Afficher dans YouTube Music… » décochée dans l'appli |
| Erreur TLS côté plugin | AC absente ou périmée : menu Plugins › Pear Connect › Autorité de certification ; contraintes de nom DNS uniquement |
| Rien ne se passe après « Écouter ici » | Les commandes expirent au bout de 2 min : la cible doit être en ligne au moment du transfert |

## Outil historique `pearsync`

Avant le plugin, un outil Python faisait le même travail depuis l'extérieur de Pear, via son plugin **API Server**. Il reste fonctionnel pour le dépannage, les PC Linux sans plugin ou un transfert direct de PC à PC sans serveur.

```
py -m pip install --user .            # installe la commande pearsync (et pearsync-agent sans console)
py -m pearsync auth bureau            # autoriser auprès de l'API de Pear (cliquer « Autoriser »)
py -m pearsync status                 # état de toutes les instances
py -m pearsync pull bureau            # reprendre ici ce qui joue sur « bureau »
py -m pearsync push portable          # envoyer ce qui joue ici vers « portable »
py -m pearsync transfer bureau portable
py -m pearsync link bureau            # lien music.youtube.com (titre, mix, position)
py -m pearsync agent -v               # publier l'état local sur le serveur Connect et exécuter ses commandes
```

Configuration : `config.example.toml` à copier dans `%APPDATA%\pearsync\config.toml` (ou `-c`). Deux modes combinables : **direct** (API Pear de l'autre PC joignable, LAN ou VPN) et **serveur Connect** (agent sur chaque PC, aucun port ouvert sur les PC). Le mix d'origine est rechargé en redémarrant Pear avec `watch?v=…&list=…` dans sa configuration (option « Reprendre la dernière chanson quand l'application démarre » requise). Les scripts d'installation de l'agent sont dans `windows/legacy/` ; sur les PC équipés du plugin, la tâche planifiée `pearsync-agent` est désactivée par l'installeur.

## Documents

- [`CLAUDE.md`](CLAUDE.md) : passation détaillée (contexte, architecture, chronologie, tâches restantes, pièges).
- [`RECHERCHE_PLAYBACK.md`](RECHERCHE_PLAYBACK.md) : comment reprendre un titre dans son mix avec Pear (API, navigation interne, limites).
- [`RECHERCHE_MULTI_APPAREILS.md`](RECHERCHE_MULTI_APPAREILS.md) : analyse des options multi-appareils (Jam, Cast, API) qui ont conduit à cette architecture.
- Releases GitHub : asar, installeurs, `versions.json` et APK de chaque version publiée.
