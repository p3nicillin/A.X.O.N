"""Persistent personal transcript adaptation tests."""
import json

import pytest

from axon.perception.speech_profile import SpeechProfile


def test_profile_applies_longest_phrase_corrections_case_insensitively(tmp_path):
    profile = SpeechProfile(tmp_path / "speech.json")
    profile.add("ma is", "what is")
    profile.add("weather", "forecast")

    corrected = profile.apply("Ma is the WEATHER")

    assert corrected == "what is the forecast"


def test_profile_persists_without_audio(tmp_path):
    path = tmp_path / "speech.json"
    SpeechProfile(path).add("accent", "axon")

    loaded = SpeechProfile(path)
    raw = json.loads(path.read_text(encoding="utf-8"))

    assert loaded.snapshot() == [{"heard": "accent", "expected": "axon"}]
    assert set(raw) == {"version", "corrections"}
    assert "audio" not in raw


def test_profile_removes_and_validates_corrections(tmp_path):
    profile = SpeechProfile(tmp_path / "speech.json")
    profile.add("wrong", "right")

    assert profile.remove("WRONG") is True
    assert profile.remove("missing") is False
    assert profile.apply("wrong") == "wrong"
    with pytest.raises(ValueError):
        profile.add("", "right")
