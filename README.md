# pearsync / Pear Connect — un « Spotify Connect » pour YouTube Music

Transfère le titre en cours, la position, la file et le mix d'origine entre instances **Pear Desktop** (YouTube Music), et les pilote depuis une télécommande web, comme Spotify Connect. Le contexte et l'analyse sont dans [RECHERCHE_PLAYBACK.md](RECHERCHE_PLAYBACK.md) et [RECHERCHE_MULTI_APPAREILS.md](RECHERCHE_MULTI_APPAREILS.md).

## Pear Connect en bref

- **Serveur Connect** (`server/connect_server.py`, LXC `pearconnect` du Proxmox) : il fait autorité sur la session (appareils en ligne, appareil actif, titre, position, mix, file, volume) et la garde sur disque. HTTPS sur `https://192.168.1.168:8443` avec l'AC du homelab ; HTTP `:8787` reste ouvert le temps de migrer.
- **Plugin Pear Connect dans Pear Desktop** (fork de Pear 3.12.0, `forks/pear-desktop`) : bouton « appareils » dans la barre du lecteur, barre « Lecture sur … » quand un autre appareil joue (titre, progression en direct, lecture/pause, précédent/suivant, déplacement, volume, « Écouter ici »). Le mix se recharge par la navigation interne de YouTube Music, en 1 à 2 s, sans redémarrer Pear. Il remplace l'agent `pearsync` sur les PC.
- **Téléphone** : l'appli Android Pear Connect (≥ 0.3.0) se sert du mode « participant » du patch Morphe Jam : YouTube Music affiche lui-même ce qui joue sur un PC (file, barre de progression, commandes renvoyées au PC, « Quitter et lire ici »).
- **Patch Morphe « Pear Connect »** (`forks/morphe-patches`, bundle `patches-1.47.0-pearconnect.1.mpp`) : ajoute l'identifiant du mix au pont Jam, pour que le mix suive aussi du téléphone vers un PC.
- **Télécommande web** : `https://192.168.1.168:8443` (ou `http://…:8787`), avec volume et file « À suivre » cliquable, réordonnable (monter/descendre) et éditable (retirer).
- **File distribuée** : depuis n'importe quel appareil (PC, téléphone en mode participant, télécommande), on peut sauter à un titre, le retirer ou le déplacer dans la file de l'appareil qui joue (`jump`/`remove`/`move`).
- **Un seul appareil joue à la fois** : lancer la lecture ailleurs met l'ancien en pause.

### PC : installer ou mettre à jour le plugin

Dans PowerShell, Pear ouvert, une seule commande :

```
irm http://192.168.1.168:8787/install.ps1 | iex
```

Le script, servi par le serveur Connect, trouve Pear, vérifie sa version, reprend le nom et le jeton de l'ordinateur (plugin ou ancien agent, sinon il les demande et les vérifie), télécharge `app.asar` et l'AC en contrôlant leur SHA-256, sauvegarde l'original, configure le plugin, désactive l'ancien agent et relance Pear. Il se relance en administrateur si Pear est dans Program Files. Retour à Pear d'origine : `irm http://192.168.1.168:8787/uninstall.ps1 | iex`.

Sous Linux (paquet deb/rpm ou archive ; AppImage à extraire d'abord) : `curl -fsSL http://192.168.1.168:8787/install.sh | bash` (retour : `uninstall.sh`).

Publier une nouvelle version : `python tools/build_asar.py --dist` (construit l'asar et régénère `server/dist/` avec les installeurs Windows et Linux), puis `bash tools/deploy_dist.sh` ; le serveur se déploie avec `bash tools/deploy_server.sh`. Détails dans `CLAUDE.md`.

### Téléphone

1. Ouvrir `http://192.168.1.168:8787/dl/PearConnect.apk` dans le navigateur du téléphone et installer par-dessus l'ancienne version (même signature), puis remplacer l'adresse du serveur par `https://192.168.1.168:8443` (l'AC est intégrée à l'appli).
2. Garder cochée « Afficher dans YouTube Music ce qui joue sur mes autres appareils ».
3. Pour le mix du téléphone vers un PC : dans Morphe Manager, ajouter le bundle `patches-1.47.0-pearconnect.1.mpp` comme source de patchs à la place des patchs officiels, et repatcher YouTube Music avec les mêmes options.

### Ajouter un appareil côté serveur (sur le Proxmox)

```
pct exec 101 -- runuser -u pearconnect -- python3 /opt/pearconnect/connect_server.py --data-dir /var/lib/pearconnect add-device <nom>
```

L'AC et sa clé privée sont dans `pki/` (voir `pki/LISEZMOI.txt`, renouvellement du certificat compris).

La suite décrit l'outil en ligne de commande `pearsync` et son agent, qui restent utiles sans le plugin (Linux, mode direct entre PC, dépannage). Avec le plugin, l'agent n'est plus nécessaire sur les PC.

```
py -m pearsync pull bureau        # reprendre ici ce qui joue sur « bureau »
py -m pearsync push portable      # envoyer ce qui joue ici vers « portable »
py -m pearsync transfer bureau portable
py -m pearsync status             # état de toutes les instances
py -m pearsync link bureau        # lien music.youtube.com avec position (téléphone)
```

La source est mise en pause, sauf avec `--keep-source`. `--no-queue` transfère le titre seul.

## Le mix d'origine est conservé

Si la lecture vient d'un mix (« Mix de … »), d'une playlist ou d'un album, la cible ne reçoit pas seulement une liste de titres : elle **rouvre le mix lui-même**, qui continue ensuite à se générer comme sur la source.

L'API de Pear ne sait pas charger un mix. pearsync passe donc par la reprise au démarrage de Pear : sur le PC cible, il ferme Pear, inscrit `watch?v=<titre>&list=<mix>&t=<secondes>` dans sa configuration, le relance, puis recale la position par l'API au cas où YouTube Music ignorerait `t=`. Comptez 5 à 15 s de transfert au lieu de 3 à 6.

Conditions :
- L'option Pear **« Reprendre la dernière chanson quand l'application démarre » doit rester activée** (c'est le défaut) sur chaque PC.
- Le redémarrage n'est possible que sur le PC où tourne pearsync. C'est le cas avec `pull`, et avec le serveur Connect, où l'agent de la cible s'en charge. Un `push` en mode direct vers un autre PC ne recopie que les titres, et un avertissement le signale. Préférez `pull` sur la cible, ou le serveur Connect.
- Si le rechargement échoue (option désactivée, Pear introuvable…), pearsync retombe sur le mode « file » et l'écrit dans le journal.
- Pear est fermé de force s'il ne se ferme pas proprement, par exemple quand il reste dans la zone de notification. Une copie de sa configuration est conservée dans `config.json.pearsync.bak`.

Vers le téléphone, `pearsync link` produit aussi un lien qui rouvre le titre dans son mix.

## Deux modes

- **Direct** : ce PC appelle directement l'API Pear de l'autre. Il faut que celle-ci soit joignable, donc même LAN ou VPN (WireGuard, Tailscale).
- **Serveur Connect** (recommandé) : chaque PC fait tourner `pearsync agent`, qui publie son état et relève ses commandes sur le serveur hébergé sur le homelab. L'API Pear reste en `127.0.0.1` et aucun port n'est ouvert sur les PC. Hors du réseau local, exposer le serveur en HTTPS (`server/Caddyfile.example`) ou via un VPN.

```
  PC bureau                         homelab                        PC portable
  Pear ⇄ agent ──sortant──▶  serveur Connect  ◀──sortant── agent ⇄ Pear
        (127.0.0.1)          (LXC, :8787)                (127.0.0.1)
                                   ▲
                       télécommande web (navigateur)
```

Les deux modes se combinent : une instance listée dans `[instances]` est jointe en direct, toute autre via le serveur.

## Installation

Prérequis : Python 3.11 ou plus (`py --version`), Pear Desktop 3.12.

### 1. Activer l'API sur chaque PC

1. Dans Pear Desktop : **Plugins → API Server** → activer.
2. Dans le sous-menu du plugin :
   - **Hostname** : `127.0.0.1` avec le serveur Connect. Le défaut `0.0.0.0` expose l'API sur tout le réseau local.
   - **Auth strategy** : laisser *Auth at first*.
3. Dans le menu Options de Pear, laisser cochée « Reprendre la dernière chanson quand l'application démarre ». pearsync s'en sert pour rouvrir le mix d'origine.
4. **Recommandé** : remplacer la clé de signature des jetons. Par défaut elle est dérivée de l'heure de création, donc prévisible, et le menu ne permet pas de la changer.
   1. Fermer Pear Desktop.
   2. Ouvrir `%APPDATA%\YouTube Music\config.json` et repérer la section du plugin `api-server`.
   3. Remplacer la valeur de `"secret"` par le résultat de `py -c "import secrets;print(secrets.token_urlsafe(32))"`.
   4. Relancer Pear. Les jetons existants deviennent invalides, il faudra refaire l'autorisation (étape 4 de l'installation).

### 2. Installer pearsync

Depuis le dossier du projet :

```
py -m pip install --user .
```

Cela installe aussi `pearsync-agent.exe`, une version sans console de l'agent. Les commandes passent par `py -m pearsync …`, ce qui évite d'avoir le dossier `Scripts` dans le PATH.

### 3. Configurer

Copier `config.example.toml` vers `%APPDATA%\pearsync\config.toml` et l'adapter : `client_id`, `local`, `[instances]`, `[relay]`. Chaque PC a sa propre config, avec son propre `local` et son propre `node`.

### 4. Autoriser pearsync auprès de Pear

```
py -m pearsync auth bureau
```

Une boîte de dialogue s'ouvre dans Pear Desktop : cliquer **Autoriser**. Le jeton est stocké dans `%APPDATA%\pearsync\tokens.json`. Il faut le faire une fois par instance, depuis chaque PC qui l'appelle en direct.

### 5. Serveur Connect et agent

Le serveur tourne dans le LXC `pearconnect` (voir `server/pearconnect.service`). Sur chaque PC, `install-agent.ps1` fait tout : installation, configuration, autorisation auprès de Pear et lancement de l'agent à l'ouverture de session (tâche planifiée `pearsync-agent`). Le journal de l'agent se trouve dans `%APPDATA%\pearsync\agent.log`.

## Valider pas à pas

Chaque étape isole une couche : si l'une échoue, le problème est dans cette couche.

1. **API locale** : avec un titre en lecture, ouvrir `http://127.0.0.1:26538/swagger` dans un navigateur. La page doit s'afficher. Sinon, le plugin n'est pas actif ou le port diffère.
2. **Jeton** : `py -m pearsync status` doit afficher le titre en cours pour l'instance locale. Un 401 signifie qu'il faut relancer `auth`.
3. **Serveur seul** : `curl http://192.168.1.168:8787/healthz` doit renvoyer `{"ok": true}`. Ensuite, `curl -H "Authorization: Bearer <jeton>" http://192.168.1.168:8787/v1/snapshot` doit répondre 200. Un 401 signifie que le jeton est faux.
4. **Agents** : lancer `py -m pearsync agent -v` dans une console sur chaque PC (avant de passer par la tâche planifiée). `py -m pearsync status` doit alors lister les appareils « (serveur) », et la télécommande web les afficher.
5. **Transfert** : jouer un titre sur le bureau, puis lancer sur le portable `py -m pearsync pull bureau`. Le bureau passe en pause et le portable reprend au même endroit en 3 à 6 s, le temps du cycle de l'agent.

## Dépannage

| Symptôme | Cause probable |
|---|---|
| « le titre n'est pas apparu dans la file de la cible » | La cible n'a rien joué depuis son lancement : lancer n'importe quel titre puis réessayer |
| 401 non autorisé | Jeton absent ou invalidé (changement de `secret`, client retiré) : `pearsync auth <instance>` |
| L'instance est injoignable | Pear fermé, plugin désactivé, mauvais port ou hostname |
| Un appareil est « hors ligne » sur la télécommande | L'agent de ce PC ne tourne pas ou n'a pas le bon jeton : voir `agent.log` |
| Décalage de quelques secondes | Normal (granularité de la position, délai de bascule) |
| Avertissement « mix … non rechargeable » | Transfert vers un PC distant en direct, ou option de reprise désactivée : utiliser `pull` sur la cible ou le serveur Connect |
| Rien ne se passe après « Écouter ici » | Les commandes expirent au bout de 2 min : l'agent cible doit tourner au moment du transfert |

## Sécurité

- Ne jamais rediriger le port 26538 sur la box. Les jetons de l'API Pear n'expirent pas et rien ne limite les tentatives.
- En mode direct sur le LAN, restreindre le port 26538 dans le pare-feu Windows à l'IP de l'autre PC.
- Chaque appareil a son propre jeton, et tout jeton permet de piloter la lecture : les traiter comme des mots de passe. Un jeton qui fuit se révoque avec `remove-device <nom>`, puis se recrée avec `add-device`.
- Sur le réseau local, le serveur parle en HTTP simple. Avant de l'ouvrir à l'extérieur, passer par HTTPS (Caddy) ou un VPN.

## Développement

```
python -m unittest tests/test_pearsync.py   # faux Pear + serveur Connect, sans réseau externe
node --experimental-strip-types --test forks/pear-desktop/tests/protocol.test.ts   # fonctions pures du plugin
ruff check . && mypy pearsync server/connect_server.py
```
