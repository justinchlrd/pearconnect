#!/bin/bash
/tmp/claude-0/scripts/pear2.sh stop
python3 - <<'PY'
import json
p='/tmp/claude-0/pearhome2/.config/YouTube Music/config.json'
c=json.load(open(p)); c['url']='https://music.youtube.com/'; json.dump(c,open(p,'w'))
PY
/tmp/claude-0/scripts/pear2.sh start
sleep 30
