from types import SimpleNamespace

import pytest

from tools.audio.piper_tts import PiperTTS


def test_piper_resolves_model_from_env(monkeypatch, tmp_path):
    model = tmp_path / "voice.onnx"
    model.write_bytes(b"onnx")
    monkeypatch.setenv("PIPER_MODEL_PATH", str(model))

    assert PiperTTS._resolve_model({"model": "auto"}) == model


def test_piper_can_use_python_module_runtime_when_binary_is_missing(monkeypatch):
    pytest.importorskip("piper")
    monkeypatch.setattr("tools.audio.piper_tts.shutil.which", lambda name: None)

    runtime = PiperTTS._runtime_command()

    assert runtime is not None
    assert runtime[-2:] == ["-m", "piper"]


def test_piper_execute_passes_resolved_model_path(monkeypatch, tmp_path):
    model = tmp_path / "voice.onnx"
    model.write_bytes(b"onnx")
    output = tmp_path / "voice.wav"
    calls = []

    def fake_run(cmd, input, capture_output, text, timeout, cwd):
        calls.append(cmd)
        output.write_bytes(b"wav")
        return SimpleNamespace(returncode=0, stderr="")

    monkeypatch.setattr(PiperTTS, "_runtime_command", staticmethod(lambda: ["python", "-m", "piper"]))
    monkeypatch.setattr(PiperTTS, "_resolve_model", classmethod(lambda cls, inputs: model))
    monkeypatch.setattr("tools.audio.piper_tts.subprocess.run", fake_run)

    result = PiperTTS().execute({"text": "hello", "output_path": str(output)})

    assert result.success
    assert calls[0][:3] == ["python", "-m", "piper"]
    assert calls[0][calls[0].index("--model") + 1] == str(model)
    assert output.read_bytes() == b"wav"
