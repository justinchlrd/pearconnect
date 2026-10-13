#!/bin/bash
D=/tmp/claude-0/pear-test
case "$1" in
 stop) for p in $(pgrep -f "$D/youtube-music"); do kill $p 2>/dev/null; done; sleep 2;;
 start) cd $D; HOME=${PEARHOME:-/tmp/claude-0/pearhome2} nohup xvfb-run -a -s "-screen 0 1400x900x24" ./youtube-music --no-sandbox --remote-debugging-port=26541 --proxy-server=http://127.0.0.1:45681 --ignore-certificate-errors > /tmp/claude-0/e2e/pear.log 2>&1 &;;
esac
