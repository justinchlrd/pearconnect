#!/bin/bash
set -e
cd /home/claude/pear-fork
./node_modules/.bin/oxfmt --write src/plugins/pear-connect >/dev/null
./node_modules/.bin/oxlint --type-aware src/plugins/pear-connect | tail -3
./node_modules/.bin/tsc -p tsconfig.json --noEmit 2>&1 | grep "pear-connect" && exit 1 || true
./node_modules/.bin/electron-vite build > /tmp/claude-0/build.log 2>&1
W=/tmp/claude-0/asar-work
rm -rf $W/app/dist && cp -r dist $W/app/dist
cd $W && rm -f app.asar && rm -rf app.asar.unpacked && /tmp/claude-0/tools/node_modules/.bin/asar pack app app.asar --unpack-dir assets
T=/tmp/claude-0/pear-test
cp $W/app.asar $T/resources/app.asar
echo rebuilt
