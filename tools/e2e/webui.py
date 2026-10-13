import sys, time, threading, secrets, tempfile
from pathlib import Path
R = Path('/home/claude/pearsync')
sys.path[:0] = [str(R), str(R/'server'), str(R/'tests')]
from connect_server import build_server, _hash
from fake_pear import FakePear
from pearsync.agent import Agent
from pearsync.pear_api import PearClient
from pearsync.relay_client import RelayClient
from playwright.sync_api import sync_playwright
tmp = tempfile.mkdtemp()
srv, hub = build_server('127.0.0.1', 0, Path(tmp))
tok = {n: secrets.token_urlsafe(8) for n in ('bureau', 'telephone', 'web')}
for n, t in tok.items(): hub.tokens[_hash(t)] = n
hub.save_tokens()
threading.Thread(target=srv.serve_forever, daemon=True).start()
url = f'http://127.0.0.1:{srv.server_address[1]}'
ids = ['dQw4w9WgXcQ', 'yBwD4iYcWC4', 'fJ9rUzIMcZQ', 'kJQP7kiw5Fk']
fp = FakePear(queue=ids, selected=0, position=50, playing=True); fp.auth = False
ag = Agent(PearClient(fp.url), RelayClient(url, tok['bureau']), 'bureau')
ag.tick()
RelayClient(url, tok['telephone']).put_state('telephone', None, kind='android')
with sync_playwright() as p:
    b = p.chromium.launch()
    pg = b.new_page(viewport={'width': 420, 'height': 1100})
    pg.goto(url); pg.fill('#token', tok['web']); pg.click('#save'); time.sleep(2)
    pg.screenshot(path='/tmp/claude-0/web1.png', full_page=True)
    pg.click('#queue button:nth-child(2)'); time.sleep(0.5); ag.tick(); time.sleep(0.5); ag._queue_at = 0; ag.tick()
    print('current after jump:', fp.current)
    pg.locator('#volume').fill('25'); pg.locator('#volume').dispatch_event('change'); time.sleep(0.5); ag.tick()
    print('volume:', fp.volume)
    b.close()
