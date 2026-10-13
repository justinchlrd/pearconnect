# Outil de test uniquement (pilotage du Pear de test via DevTools)
import base64, hashlib, json, os, socket, struct, threading, urllib.parse, urllib.request
class CDPError(Exception): pass
class CDPSession:
    def __init__(self, url):
        u = urllib.parse.urlsplit(url)
        self.sock = socket.create_connection((u.hostname, u.port), timeout=5)
        self.rf = self.sock.makefile('rb')
        key = base64.b64encode(os.urandom(16)).decode()
        self.sock.sendall((f"GET {u.path} HTTP/1.1\r\nHost: {u.hostname}:{u.port}\r\nUpgrade: websocket\r\nConnection: Upgrade\r\nSec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n\r\n").encode())
        while self.rf.readline().strip(): pass
        self.sock.settimeout(None)
        self.n = 0; self.pending = {}; self.lock = threading.Lock(); self.closed = False
        threading.Thread(target=self._read, daemon=True).start()
    def _send(self, data):
        mask = os.urandom(4); n = len(data); h = bytearray([0x81])
        if n < 126: h.append(0x80 | n)
        elif n < 65536: h.append(0x80 | 126); h += struct.pack('!H', n)
        else: h.append(0x80 | 127); h += struct.pack('!Q', n)
        self.sock.sendall(bytes(h) + mask + bytes(b ^ mask[i % 4] for i, b in enumerate(data)))
    def _read(self):
        try:
            buf = b''
            while True:
                b1, b2 = self.rf.read(2); n = b2 & 0x7f
                if n == 126: n = struct.unpack('!H', self.rf.read(2))[0]
                elif n == 127: n = struct.unpack('!Q', self.rf.read(8))[0]
                p = self.rf.read(n); buf += p
                if not b1 & 0x80: continue
                msg = json.loads(buf); buf = b''
                if 'id' in msg and msg['id'] in self.pending:
                    ev, box = self.pending.pop(msg['id']); box.append(msg); ev.set()
        except Exception:
            self.closed = True
    def call(self, method, params=None, timeout=10):
        with self.lock: self.n += 1; i = self.n
        ev = threading.Event(); box = []; self.pending[i] = (ev, box)
        self._send(json.dumps({'id': i, 'method': method, 'params': params or {}}).encode())
        if not ev.wait(timeout): raise CDPError(method + ' : pas de réponse')
        if 'error' in box[0]: raise CDPError(str(box[0]['error']))
        return box[0].get('result', {})
    def close(self):
        try: self.sock.shutdown(socket.SHUT_RDWR)
        except OSError: pass
        self.sock.close()
def list_targets(port):
    o = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    return json.loads(o.open(f'http://127.0.0.1:{port}/json/list', timeout=3).read())
