#!/bin/bash
for p in $(pgrep -f "server/connect_server.py --data-dir /tmp/claude-0/e2e/data"); do kill $p; done
sleep 1
cd /tmp/claude-0/e2e
nohup python3 /home/claude/pearsync/server/connect_server.py --data-dir /tmp/claude-0/e2e/data --bind 127.0.0.1 --port 8790 --https-port 8791 --tls-cert tchain.crt --tls-key tsrv.key serve > /tmp/claude-0/e2e/server.log 2>&1 &
