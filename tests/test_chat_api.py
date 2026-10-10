import json
import threading
import uuid
from http.server import ThreadingHTTPServer
from urllib.request import Request, urlopen

import server


def test_chat_endpoints_require_confirmed_valid_payload_and_return_contract(monkeypatch):
    monkeypatch.setattr(server, 'CHAT_BRIDGE', _FakeBridge())
    httpd = ThreadingHTTPServer(('127.0.0.1', 0), server.Handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        port = httpd.server_address[1]
        payload = {'message': 'hello', 'confirmed': True, 'client_id': str(uuid.uuid4()), 'request_id': str(uuid.uuid4())}
        req = Request(f'http://127.0.0.1:{port}/api/run', data=json.dumps(payload).encode(), method='POST',
                      headers={'Host': f'127.0.0.1:{port}', 'Origin': f'http://127.0.0.1:{port}',
                               'X-Jarvis-Token': server.API_TOKEN, 'Content-Type': 'application/json'})
        with urlopen(req) as response:
            data = json.load(response)
        assert data == {'reply': 'ok', 'session_id': 'test-session', 'request_id': payload['request_id']}
    finally:
        httpd.shutdown()
        httpd.server_close()


class _FakeBridge:
    ready = True
    def run(self, message, client_id, request_id):
        return {'reply': 'ok', 'session_id': 'test-session', 'request_id': request_id}
    def cancel(self, client_id, request_id):
        return {'stopped': False}
    def new(self, client_id):
        return {'ok': True}
