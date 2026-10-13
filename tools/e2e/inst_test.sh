#!/bin/bash
# Test de l'installation en une commande (pwsh + serveur local)
P=/tmp/claude-0/-home-claude/189c6e0b-39d4-52f3-b6ed-152d5f69e19b/scratchpad
T=/tmp/claude-0/pstest3
ACTION=${1:-install}
cd $T
sed -e "s#http://192.168.1.168:8787#$BASEURL#g" -e "s/if (-not \"\$version\".StartsWith(\$ExpectedVersion)) {/if (\$false) {/" -e 's/^& icacls .*$//' /home/claude/pearsync/server/dist/$ACTION.ps1 > served.ps1
LOCALAPPDATA=$T/local APPDATA=$T/roaming TEMP=$T/tmp $P/pwsh/pwsh -NoProfile -Command ". ./shims.ps1; Get-Content -Raw ./served.ps1 | iex" 2>&1
