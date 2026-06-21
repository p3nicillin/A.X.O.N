"""Structured in-app weather capability tests."""
from axon.ai.context import Context
from axon.ai.intent_engine import LocalIntentEngine
from axon.ai.schema import Intent
from axon.config import Config
from axon.skills.registry import SkillRegistry
from axon.skills.weather import handler


class FakeResponse:
    def __init__(self, payload):
        self.payload = payload

    def raise_for_status(self):
        pass

    def json(self):
        return self.payload


class FakeRequests:
    def __init__(self):
        self.calls = []

    def get(self, url, params, timeout):
        self.calls.append((url, params, timeout))
        if "geocoding" in url:
            return FakeResponse({"results": [{
                "name": "Testville", "admin1": "Testshire",
                "country": "United Kingdom", "latitude": 51.5,
                "longitude": -0.1,
            }]})
        return FakeResponse({
            "current": {
                "temperature_2m": 18.4, "apparent_temperature": 17.8,
                "relative_humidity_2m": 62, "precipitation": 0,
                "weather_code": 2, "wind_speed_10m": 11.2,
            },
            "daily": {
                "time": ["2026-06-21", "2026-06-22", "2026-06-23"],
                "weather_code": [2, 61, 3],
                "temperature_2m_max": [21.0, 19.0, 22.0],
                "temperature_2m_min": [13.0, 12.0, 14.0],
                "precipitation_probability_max": [10, 70, 20],
            },
        })


def test_weather_query_routes_to_dedicated_skill():
    registry = SkillRegistry().discover()
    engine = LocalIntentEngine(registry.catalogue())

    packet = engine.interpret("what is the weather in Manchester", Context())

    assert packet.intent.type == "get_weather"
    assert packet.intent.parameters == {"location": "Manchester", "days": 1}


def test_weather_returns_structured_in_app_result(monkeypatch):
    fake = FakeRequests()
    monkeypatch.setattr(handler, "requests", fake)
    skill = SkillRegistry().discover().route(Intent(type="get_weather"))
    skill._cache.clear()

    result = skill.execute(Intent(type="get_weather", parameters={
        "location": "Testville", "days": 3,
    }))

    assert result.ok is True
    assert result.data["source"] == "open-meteo"
    assert result.data["location"] == "Testville, Testshire, United Kingdom"
    assert len(result.data["forecast"]) == 3
    assert "partly cloudy" in result.summary
    assert "Forecast" in result.speak
    assert len(fake.calls) == 2


def test_weather_uses_configured_default_and_cache(monkeypatch):
    fake = FakeRequests()
    monkeypatch.setattr(handler, "requests", fake)
    cfg = Config()
    cfg.weather_default_location = "Testville"
    registry = SkillRegistry(config=cfg).discover()
    skill = registry.route(Intent(type="get_weather"))
    skill._cache.clear()

    first = skill.execute(Intent(type="get_weather", parameters={"days": 1}))
    second = skill.execute(Intent(type="get_weather", parameters={"days": 1}))

    assert first.ok and second.ok
    assert fake.calls[0][1]["name"] == "Testville"
    assert len(fake.calls) == 2


def test_weather_rejects_invalid_forecast_days_without_network(monkeypatch):
    fake = FakeRequests()
    monkeypatch.setattr(handler, "requests", fake)

    skill = SkillRegistry().discover().route(Intent(type="get_weather"))
    skill._cache.clear()
    result = skill.execute(Intent(
        type="get_weather", parameters={"days": "many"}))

    assert result.ok is False
    assert fake.calls == []
