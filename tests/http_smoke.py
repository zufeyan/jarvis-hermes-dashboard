"""Real HTTP provider and security checks; no microphone or playback."""
import json
import re
import subprocess
import time
import urllib.error
import urllib.request
from pathlib import Path

BASE = 'http://127.0.0.1:8730'
OUT = Path('/Volumes/Data_Sufayan/JARVIS_Thai_Voice/verification/dashboard')
OUT.mkdir(parents=True, exist_ok=True)
html = urllib.request.urlopen(BASE, timeout=25).read().decode()
token = re.search(r'name="jarvis-token" content="([^"]+)"', html).group(1)
rows = []


def call(path, data, content_type='application/json', origin=BASE, supplied=token):
    req = urllib.request.Request(BASE + path, data=data, method='POST', headers={
        'Origin': origin, 'X-Jarvis-Token': supplied, 'Content-Type': content_type})
    start = time.monotonic()
    try:
        response = urllib.request.urlopen(req, timeout=100)
    except urllib.error.HTTPError as error:
        response = error
    body = response.read()
    rows.append({'endpoint': path, 'status': response.status, 'bytes': len(body),
                 'seconds': round(time.monotonic()-start, 3),
                 'no_store': response.headers.get('Cache-Control') == 'no-store'})
    return response.status, body

code, audio = call('/api/speak', json.dumps({'text':'จาร์วิสพร้อมทดสอบเสียงภาษาไทย'}).encode())
assert code == 200
mp3 = OUT / 'http-thai.mp3'
mp3.write_bytes(audio)
probe = subprocess.run(['ffprobe','-v','error','-show_entries','format=duration,format_name','-of','json',str(mp3)],capture_output=True,text=True,timeout=10)
assert probe.returncode == 0
fixture = Path('/Volumes/Data_Sufayan/JARVIS_Thai_Voice/verification/quality/jarvis_ip.mp3')
listen_attempts = []
body = b''
for attempt in range(2):
    code, body = call('/api/listen', fixture.read_bytes(), 'audio/mpeg')
    listen_attempts.append(code)
    if code == 200:
        break
assert code == 200
transcript = json.loads(body)['text']
assert ''.join(transcript.split()) == 'จาร์วิสอธิบายว่าไอพีแอดเดรสคืออะไร'
assert call('/api/run', b'{"message":"hello","confirmed":true}')[0] == 400
assert call('/api/speak', b'{"text":"test"}', supplied='wrong')[0] == 401
assert call('/api/speak', b'{"text":"test"}', origin='https://untrusted.example')[0] == 403
assert call('/api/speak', b'[]')[0] == 400
assert call('/api/listen', b'invalid')[0] == 415
status = json.loads(urllib.request.urlopen(BASE+'/api/status',timeout=25).read())
assert status['tools_enabled'] is False and status['scope'] == 'chat-only'
assert status['agent_enabled'] is True and status['chat_enabled'] is True
assert status['chat_provider'] and status['chat_model']
report = {'passed': True, 'audio_origin': 'synthetic Edge; no microphone/playback',
          'status': status, 'transcript': transcript,
          'mp3': json.loads(probe.stdout), 'requests': rows,
          'listen_attempts': listen_attempts,
          'token_logged': False, 'remaining': ['human mic acceptance','human playback/Stop acceptance of the new chat reply flow','agent approval integration for tools']}
(OUT/'http-report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
print(json.dumps(report,ensure_ascii=False,indent=2))
