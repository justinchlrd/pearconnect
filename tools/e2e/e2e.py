import sys, json, time, base64
sys.path[:0] = ['/home/claude/pearsync']
from cdp import CDPSession, list_targets
from pearsync.relay_client import RelayClient
from pearsync.handoff import PlaybackState
tok = json.load(open('/tmp/claude-0/e2e/tokens.json'))
URL = 'http://127.0.0.1:8790'
def page():
    t = [x for x in list_targets(26541) if x['type'] == 'page' and 'music.youtube.com' in x['url']][0]
    return CDPSession(t['webSocketDebuggerUrl'])
def shot(name):
    s = page()
    try:
        s.call('Page.bringToFront')
        r = s.call('Page.captureScreenshot', {'format': 'png'}, timeout=20)
        open(f'/tmp/claude-0/e2e/{name}.png', 'wb').write(base64.b64decode(r['data']))
    finally:
        s.close()
def ev(expr):
    s = page()
    try:
        r = s.call('Runtime.evaluate', {'expression': expr, 'returnByValue': True, 'awaitPromise': True}, timeout=60)
        return r.get('result', {}).get('value', r.get('exceptionDetails'))
    finally:
        s.close()
web = RelayClient(URL, tok['web'])
phone = RelayClient(URL, tok['telephone'])
def phone_plays(pos=42, paused=False, vid='dQw4w9WgXcQ', volume=60):
    st = PlaybackState(vid, pos, paused, duration=213, title='Never Gonna Give You Up', artist='Rick Astley',
       upcoming=['yBwD4iYcWC4','fJ9rUzIMcZQ'], queue=[{'video_id':'yBwD4iYcWC4','title':'She Wants To Dance','artist':'Rick Astley'},{'video_id':'fJ9rUzIMcZQ','title':'Bohemian Rhapsody','artist':'Queen'}], volume=volume)
    phone.put_state('telephone', st, kind='android')
def click(x, y):
    s = page()
    try:
        for t in ('mousePressed', 'mouseReleased'):
            s.call('Input.dispatchMouseEvent', {'type': t, 'x': x, 'y': y, 'button': 'left', 'clickCount': 1})
    finally:
        s.close()
def player():
    r = ev("(() => { const p = document.querySelector('#movie_player'); const d = p.getVideoData(); return {id: d.video_id, t: Math.round(p.getCurrentTime()), st: p.getPlayerState(), url: location.href, q: document.querySelector('#queue').queue.getItems().length}; })()")
    return r if isinstance(r, dict) and 'id' in r else {'id': None, 'st': None, 'url': '', 'err': str(r)[:200]}
