#!/bin/bash
# Déploie le serveur Connect (code + télécommande web) dans le LXC 101 du Proxmox, puis le relance.
# À lancer depuis la racine du dépôt, dans Git Bash : bash tools/deploy_server.sh
# Prérequis : ssh root@192.168.1.10 par clé (le Proxmox fait « pct push » vers le conteneur).
set -euo pipefail
cd "$(dirname "$0")/.."
PVE=${PVE:-root@192.168.1.10}
CT=${CT:-101}
STAMP=$(date +%Y%m%d-%H%M%S)

python -c "import ast; ast.parse(open('server/connect_server.py', encoding='utf-8').read())"
echo "→ copie vers $PVE"
ssh -o BatchMode=yes "$PVE" "mkdir -p /tmp/pearconnect-$STAMP"
scp -q -o BatchMode=yes server/connect_server.py server/web/index.html "$PVE:/tmp/pearconnect-$STAMP/"
echo "→ installation dans le conteneur $CT (sauvegarde en *.bak-$STAMP) et redémarrage"
ssh -o BatchMode=yes "$PVE" "set -e
  pct exec $CT -- cp /opt/pearconnect/connect_server.py /opt/pearconnect/connect_server.py.bak-$STAMP
  pct exec $CT -- cp /opt/pearconnect/web/index.html /opt/pearconnect/web/index.html.bak-$STAMP
  pct push $CT /tmp/pearconnect-$STAMP/connect_server.py /opt/pearconnect/connect_server.py --perms 0755
  pct push $CT /tmp/pearconnect-$STAMP/index.html /opt/pearconnect/web/index.html --perms 0644
  pct exec $CT -- systemctl restart pearconnect
  sleep 2
  pct exec $CT -- systemctl is-active pearconnect
  pct exec $CT -- journalctl -u pearconnect --no-pager -n 5 -o short-iso
  rm -rf /tmp/pearconnect-$STAMP"
echo "→ vérification"
curl -sS -m 5 http://192.168.1.168:8787/healthz; echo
echo "serveur déployé"
