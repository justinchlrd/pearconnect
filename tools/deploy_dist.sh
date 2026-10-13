#!/bin/bash
# Publie server/dist/ (installeur, app-asar.zip, AC, APK) dans /opt/pearconnect/dist/ du LXC 101.
# À lancer après « python tools/build_asar.py --dist », dans Git Bash : bash tools/deploy_dist.sh
set -euo pipefail
cd "$(dirname "$0")/.."
PVE=${PVE:-root@192.168.1.10}
CT=${CT:-101}
STAMP=$(date +%Y%m%d-%H%M%S)

FILES="install.ps1 uninstall.ps1 install.sh uninstall.sh versions.json app-asar.zip pearconnect-ca.crt PearConnect.apk"
for f in $FILES; do
  [ -f "server/dist/$f" ] || { echo "manque server/dist/$f" >&2; exit 1; }
done
echo "→ copie vers $PVE"
ssh -o BatchMode=yes "$PVE" "mkdir -p /tmp/pearconnect-dist-$STAMP"
scp -q -o BatchMode=yes server/dist/* "$PVE:/tmp/pearconnect-dist-$STAMP/"
echo "→ installation dans le conteneur $CT"
ssh -o BatchMode=yes "$PVE" "set -e
  for f in $FILES; do
    pct push $CT /tmp/pearconnect-dist-$STAMP/\$f /opt/pearconnect/dist/\$f --perms 0644
  done
  pct exec $CT -- ls -la /opt/pearconnect/dist/
  rm -rf /tmp/pearconnect-dist-$STAMP"
echo "→ vérification de l'empreinte servie"
curl -sS -m 10 http://192.168.1.168:8787/dl/app-asar.zip | sha256sum
sha256sum server/dist/app-asar.zip
curl -sS -m 10 http://192.168.1.168:8787/versions.json
echo "fichiers publiés ; sur chaque PC : irm http://192.168.1.168:8787/install.ps1 | iex  (Linux : curl -fsSL http://192.168.1.168:8787/install.sh | bash)"
