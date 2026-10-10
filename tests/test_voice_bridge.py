import json
from unittest.mock import patch

import pytest
import voice_bridge


def test_transcript_uses_actual_hermes_result_field():
    with patch('voice_bridge.decode_audio', return_value=b'WAV'), patch('voice_bridge._call', return_value={'success': True, 'transcript': 'จาร์วิส ไอพี'}) as fn:
        assert voice_bridge.transcribe(b'fixture', 'audio/mpeg') == 'จาร์วิส ไอพี'
        from pathlib import Path
        path = Path(fn.call_args.args[0]['path'])
        assert not path.exists()


def test_empty_transcript_is_not_success():
    with patch('voice_bridge.decode_audio', return_value=b'WAV'), patch('voice_bridge._call', return_value={'success': True, 'transcript': ''}):
        with pytest.raises(ValueError):
            voice_bridge.transcribe(b'fixture', 'audio/mpeg')


def test_nonlocal_configuration_fails_closed():
    with patch('voice_bridge._stt_config', return_value={'provider': 'openai'}):
        with pytest.raises(RuntimeError):
            voice_bridge._transcribe_file(None)


def test_stt_model_override_is_pilot_scoped_and_allowlisted(monkeypatch):
    monkeypatch.delenv('JARVIS_STT_MODEL', raising=False)
    assert voice_bridge.stt_model_override() == ''
    monkeypatch.setenv('JARVIS_STT_MODEL', 'small')
    assert voice_bridge.stt_model_override() == 'small'
    monkeypatch.setenv('JARVIS_STT_MODEL', 'huge')
    with pytest.raises(RuntimeError):
        voice_bridge.stt_model_override()


def test_transcribe_passes_the_overridden_model(monkeypatch):
    import sys
    import types
    seen = {}
    fake_tools = types.ModuleType('tools')
    fake = types.ModuleType('tools.transcription_tools')
    fake.transcribe_audio = lambda **kw: seen.update(kw) or {'success': True, 'transcript': 'ok'}
    fake_tools.transcription_tools = fake
    monkeypatch.setitem(sys.modules, 'tools', fake_tools)
    monkeypatch.setitem(sys.modules, 'tools.transcription_tools', fake)
    monkeypatch.setattr(voice_bridge, '_stt_config', lambda: {'provider': 'local', 'enabled': True})
    monkeypatch.setenv('JARVIS_STT_MODEL', 'small')
    assert voice_bridge._transcribe_file('x.wav') == {'success': True, 'transcript': 'ok'}
    assert seen == {'file_path': 'x.wav', 'source': 'jarvis-dashboard', 'model': 'small'}
    monkeypatch.delenv('JARVIS_STT_MODEL')
    seen.clear()
    voice_bridge._transcribe_file('x.wav')
    assert 'model' not in seen
