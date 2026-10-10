"""Wake word module — logic only, no microphone in tests.

The listener is opt-in: it runs only when `python3 wakeword.py` is executed, and
it sends commands through the same guarded HTTP API the browser uses.
"""
import json

import pytest

import wakeword


def test_wake_word_variants_are_recognised():
    assert wakeword.is_wake('จาร์วิส อ่าน quickstart ให้หน่อย')
    assert wakeword.is_wake('จาร์วิส เปิดไฟ')
    assert wakeword.is_wake('จาวิส วันนี้วันอะไร')
    assert wakeword.is_wake('จารวิส ทดสอบ')
    assert wakeword.is_wake('JARVIS อะไรอยู่ในโฟลเดอร์นี้')


def test_non_wake_utterances_stay_quiet():
    assert wakeword.is_wake('สวัสดีครับ') is False
    assert wakeword.is_wake('วันนี้อากาศดี') is False
    assert wakeword.is_wake('') is False
    assert wakeword.is_wake('   ') is False
    assert wakeword.is_wake(None) is False
    assert wakeword.is_wake(12345) is False


def test_extract_command_drops_the_wake_phrase_and_punctuation():
    assert wakeword.extract_command('จาร์วิส อ่านไฟล์ quickstart.md') == 'อ่านไฟล์ quickstart.md'
    assert wakeword.extract_command('JARVIS, สรุปเอกสารให้หน่อย!') == 'สรุปเอกสารให้หน่อย'
    assert wakeword.extract_command('ดูสิ จาร์วิส ว่ามีอะไร') == 'ดูสิ ว่ามีอะไร'
    assert wakeword.extract_command('จาร์วิส') == ''
    assert wakeword.extract_command(None) == ''


def test_parse_token_reads_the_meta_tag():
    html = '<meta name="jarvis-token" content="abc-DEF_123"><p>x</p>'
    assert wakeword.parse_token(html) == 'abc-DEF_123'
    html2 = '<meta content="reversed-order" name="jarvis-token">'
    assert wakeword.parse_token(html2) == 'reversed-order'
    assert wakeword.parse_token('<html></html>') == ''
    assert wakeword.parse_token(b'<meta name="jarvis-token" content="bytes-ok">') == 'bytes-ok'


def test_api_headers_pin_loopback_origin_and_host():
    headers = wakeword.api_headers('tok', 8730, 'audio/wav')
    assert headers['X-Jarvis-Token'] == 'tok'
    assert headers['Origin'] == 'http://127.0.0.1:8730'
    assert headers['Host'] == '127.0.0.1:8730'
    assert headers['Content-Type'] == 'audio/wav'


def test_run_payload_matches_the_chat_schema():
    payload = wakeword.run_payload('เปิดไฟ', 'client-1', '11111111-1111-1111-1111-111111111111')
    assert payload == {'message': 'เปิดไฟ', 'client_id': 'client-1',
                       'request_id': '11111111-1111-1111-1111-111111111111'}
    assert set(payload) == {'message', 'client_id', 'request_id'}
    json.dumps(payload)  # JSON-serialisable for /api/run


def test_energy_gate_opens_after_hold_and_closes_on_silence():
    detector = wakeword.UtteranceDetector(threshold=0.012, hold_seconds=0.3, max_seconds=2.0)
    # hold = int(0.3 / 0.1) = 3 frames
    assert detector.feed(0.0) is None      # silence -> stays armed
    assert detector.feed(0.5) is None      # speech 1
    assert detector.feed(0.5) is None      # speech 2
    assert detector.feed(0.5) == 'start'   # speech 3 opens the gate
    assert detector.feed(0.5) == 'frame'   # still recording
    assert detector.feed(0.0) == 'end'     # silence closes it


def test_energy_gate_closes_at_the_max_length():
    detector = wakeword.UtteranceDetector(threshold=0.01, hold_seconds=0.1, max_seconds=0.2)
    assert detector.feed(0.5) == 'start'   # hold = 1 frame
    assert detector.feed(0.5) == 'frame'   # max_frames = 2, one frame in
    assert detector.feed(0.5) == 'end'     # cap reached


def test_module_import_starts_no_listening_session():
    assert not hasattr(wakeword, '_STARTED')
    with pytest.raises(SystemExit) as exit_info:
        wakeword.main(['--help'])
    assert exit_info.value.code == 0
