"""Current weather and forecast via Open-Meteo's key-free JSON APIs."""
from __future__ import annotations

import threading
import time
from datetime import date

from ...ai.schema import Intent, SkillResult
from ..base import Skill

try:
    import requests
except Exception:  # pragma: no cover - optional dependency
    requests = None

_GEOCODE_URL = "https://geocoding-api.open-meteo.com/v1/search"
_FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
_CACHE_SECONDS = 300.0

_CONDITIONS = {
    0: "clear", 1: "mainly clear", 2: "partly cloudy", 3: "overcast",
    45: "foggy", 48: "foggy with rime", 51: "light drizzle",
    53: "drizzle", 55: "heavy drizzle", 56: "light freezing drizzle",
    57: "freezing drizzle", 61: "light rain", 63: "rain",
    65: "heavy rain", 66: "light freezing rain", 67: "freezing rain",
    71: "light snow", 73: "snow", 75: "heavy snow", 77: "snow grains",
    80: "light rain showers", 81: "rain showers", 82: "heavy rain showers",
    85: "light snow showers", 86: "heavy snow showers",
    95: "thunderstorms", 96: "thunderstorms with hail",
    99: "severe thunderstorms with hail",
}


def _condition(code) -> str:
    try:
        return _CONDITIONS.get(int(code), "unclassified conditions")
    except (TypeError, ValueError):
        return "unclassified conditions"


class WeatherSkill(Skill):
    def __init__(self) -> None:
        self.default_location = "London"
        self._cache: dict[tuple[str, int], tuple[float, SkillResult]] = {}
        self._lock = threading.Lock()

    def configure(self, config) -> None:
        location = str(getattr(config, "weather_default_location", "")).strip()
        if location:
            self.default_location = location

    def execute(self, intent: Intent) -> SkillResult:
        if requests is None:
            return self.fail("Weather data requires the requests package.")
        location = str(intent.get("location") or self.default_location).strip()
        if location.lower() in {"here", "local", "my location"}:
            location = self.default_location
        if not location or len(location) > 120:
            return self.fail("A valid weather location is required.")
        try:
            days = max(1, min(7, int(intent.get("days", 1))))
        except (TypeError, ValueError):
            return self.fail("Forecast days must be a number from 1 to 7.")

        key = (location.casefold(), days)
        with self._lock:
            cached = self._cache.get(key)
            if cached and time.monotonic() - cached[0] < _CACHE_SECONDS:
                return cached[1]
        try:
            result = self._fetch(location, days)
        except Exception as exc:
            return self.fail(f"Weather data unavailable: {exc}",
                             speak="I couldn't retrieve the weather just now, sir.")
        with self._lock:
            self._cache[key] = (time.monotonic(), result)
        return result

    def _fetch(self, location: str, days: int) -> SkillResult:
        geo = requests.get(_GEOCODE_URL, params={
            "name": location, "count": 1, "language": "en", "format": "json",
        }, timeout=5)
        geo.raise_for_status()
        places = geo.json().get("results") or []
        if not places:
            return self.fail(f"No weather location matched '{location}'.",
                             speak=f"I couldn't find {location}, sir.")
        place = places[0]
        latitude, longitude = place["latitude"], place["longitude"]
        label = ", ".join(dict.fromkeys(filter(None, (
            place.get("name"), place.get("admin1"), place.get("country")))))

        forecast = requests.get(_FORECAST_URL, params={
            "latitude": latitude,
            "longitude": longitude,
            "current": ("temperature_2m,apparent_temperature,"
                        "relative_humidity_2m,precipitation,weather_code,"
                        "wind_speed_10m"),
            "daily": ("weather_code,temperature_2m_max,temperature_2m_min,"
                      "precipitation_probability_max"),
            "timezone": "auto",
            "forecast_days": days,
        }, timeout=5)
        forecast.raise_for_status()
        payload = forecast.json()
        current = payload.get("current") or {}
        temp = float(current["temperature_2m"])
        feels = float(current["apparent_temperature"])
        condition = _condition(current.get("weather_code"))
        humidity = int(current.get("relative_humidity_2m", 0))
        wind = float(current.get("wind_speed_10m", 0))
        summary = (f"{label}: {temp:.1f}°C, {condition}; feels {feels:.1f}°C, "
                   f"humidity {humidity}%, wind {wind:.1f} km/h.")
        spoken = (f"In {label}, it is {temp:.0f} degrees and {condition}. "
                  f"It feels like {feels:.0f} degrees.")

        daily = payload.get("daily") or {}
        rows = []
        dates = daily.get("time") or []
        for index, iso_day in enumerate(dates):
            row = {
                "date": iso_day,
                "condition": _condition((daily.get("weather_code") or [])[index]),
                "high_c": (daily.get("temperature_2m_max") or [])[index],
                "low_c": (daily.get("temperature_2m_min") or [])[index],
                "rain_chance": (daily.get("precipitation_probability_max")
                                or [])[index],
            }
            rows.append(row)
        if days > 1 and len(rows) > 1:
            preview = rows[1:min(len(rows), 4)]
            phrases = []
            for row in preview:
                weekday = date.fromisoformat(row["date"]).strftime("%A")
                phrases.append(f"{weekday}, {_condition_text(row)}")
            spoken += " Forecast: " + "; ".join(phrases) + "."
            summary += " Forecast: " + "; ".join(
                f"{date.fromisoformat(row['date']).strftime('%a')} "
                f"{row['high_c']:.0f}/{row['low_c']:.0f}°C {row['condition']}"
                for row in preview)
        return self.ok(summary, speak=spoken, source="open-meteo", location=label,
                       current=current, forecast=rows)


def _condition_text(row: dict) -> str:
    return (f"{row['condition']}, a high of {row['high_c']:.0f} and a low of "
            f"{row['low_c']:.0f} degrees, with a {row['rain_chance']:.0f} "
            "percent chance of rain")


SKILL = WeatherSkill()
