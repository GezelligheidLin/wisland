"""UAPIs weather response normalization and per-day request-cache tests."""
import json
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path
from unittest.mock import Mock, patch

from PySide6 import QtWidgets

from wisland.services import weather
from wisland.ui.settings import SettingsWindow


class WeatherTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    def sample_response(self):
        today = date.today()
        forecast = [
            {"date": (today + timedelta(days=i)).isoformat(), "temp_min": 8 + i, "temp_max": 16 + i,
             "weather_day": "晴", "weather_night": "多云", "sunrise": "06:30", "sunset": "17:45"}
            for i in range(7)
        ]
        hourly = [
            {"time": f"{today.isoformat()}T{hour:02d}:00:00+0800", "temperature": 12 + hour / 10,
             "weather": "多云", "pop": 25}
            for hour in range(24)
        ]
        return {
            "province": "上海市", "city": "上海", "adcode": "310000",
            "temperature": 12.3, "weather": "多云", "weather_icon": "101",
            "humidity": 70, "wind_direction": "东北风", "wind_power": "微风", "pressure": 1012,
            "visibility": 8.5, "cloud": 65, "forecast": forecast, "hourly_forecast": hourly,
            "feels_like": 10.6, "uv": 3.8, "precipitation": 0, "report_time": "2026-11-20 16:50:00",
            "aqi": 44, "aqi_level": 1, "aqi_category": "优", "aqi_primary": "PM10",
            "air_pollutants": {"pm25": 25, "pm10": 44, "o3": 80, "no2": 25, "so2": 10, "co": 0.4},
            "life_indices": {
                "clothing": {"level": "冷", "brief": "冷", "advice": "建议穿厚外套"},
                "uv": {"level": "高", "brief": "较强", "advice": "注意防晒"},
                "traffic": {"level": "畅通", "brief": "极佳", "advice": "适合出行"},
            },
            "minutely_forecast": {"summary": "未来两小时无降水"},
        }

    def test_parse_uapis_weather_maps_fields_and_qweather_icons(self):
        data = weather.parse_uapis_weather(self.sample_response(), "Shanghai")
        self.assertEqual(data["city"], "上海")
        self.assertEqual(data["icon"], "101")
        self.assertEqual(data["temp"], 12.3)
        self.assertEqual(data["visibility"], 8500)
        self.assertEqual(len(data["hourly"]), 8)
        self.assertIsInstance(data["hourly"][0]["time"], int)
        self.assertEqual(data["hourly"][0]["pop"], 0.25)
        self.assertEqual(len(data["daily"]), 5)
        self.assertEqual(data["daily"][0]["text"], "晴")
        self.assertIsInstance(data["sunrise"], int)
        self.assertEqual(data["aqi"], 44)
        self.assertEqual(data["aqi_category"], "优")
        self.assertEqual(data["air"]["pm25"], 25)
        self.assertEqual(data["uv"], 3.8)
        self.assertEqual(data["precipitation"], 0)
        self.assertEqual(data["minutely"], "未来两小时无降水")
        self.assertEqual(data["life_indices"][0]["name"], "穿衣")
        self.assertEqual(data["life_indices"][1]["level"], "高")
        glyph, family = weather.weather_glyph("101")
        self.assertEqual(family, "qweather-icons")
        self.assertEqual(glyph, "\uf102")

    def test_settings_save_uapis_key_to_dedicated_config_field(self):
        with patch.object(weather.cfg, "set") as save:
            SettingsWindow._save_weather_api_key("  uapi-test-key  ")
        save.assert_called_once_with("weather.uapi_key", "uapi-test-key")

    def test_explicit_utc_forecast_keeps_zero_timezone_offset(self):
        epoch, offset, has_offset = weather._epoch_and_offset("2026-11-20T12:00:00Z")
        self.assertIsInstance(epoch, int)
        self.assertEqual(offset, 0)
        self.assertTrue(has_offset)

    def test_add_city_validates_and_reuses_the_single_daily_request(self):
        with tempfile.TemporaryDirectory() as folder:
            cache_path = Path(folder) / "weather.json"
            service = weather.WeatherService(cache_path=cache_path)
            values = {"weather.cities": [], "weather.city": "", "weather.uapi_key": "", "weather.lang": "zh"}
            pending = []

            def fake_run_bg(fn, *args, on_done=None, on_error=None):
                pending.append((fn, args, on_done, on_error))
                return None

            def set_config(key, value):
                values[key] = value

            result = weather.parse_uapis_weather(self.sample_response(), "上海")
            # The implicit default city already has today's cache, so adding a
            # new city must trigger exactly one extra request.
            cache_path.write_text(json.dumps({"version": weather._CACHE_VERSION, "cities": {
                service._city_key({"name": "上海"}): {
                    "attempt_date": date.today().isoformat(), "data_date": date.today().isoformat(),
                    "error": "", "data": result,
                },
            }}, ensure_ascii=False), encoding="utf-8")
            with patch.object(weather.cfg, "get", side_effect=lambda key, default=None: values.get(key, default)), \
                    patch.object(weather.cfg, "set", side_effect=set_config), \
                    patch.object(weather, "run_bg", side_effect=fake_run_bg):
                service.add_city("北京")
                self.assertEqual(len(pending), 1)
                _fetch, _args, on_done, _on_error = pending[0]
                on_done(result)
                self.assertEqual(values["weather.cities"], [{"name": "上海"}, {"name": "北京"}])
                self.assertEqual(len(pending), 1)
                self.assertEqual(service.cities_data[0]["city"], "上海")
                self.assertEqual(json.loads(cache_path.read_text(encoding="utf-8"))["cities"][
                    service._city_key({"name": "北京"})]["attempt_date"], date.today().isoformat())
            service._client.close()

    def test_api_key_uses_authorization_header(self):
        service = weather.WeatherService(cache_path=Path(tempfile.gettempdir()) / "unused-weather-cache.json")
        response = Mock()
        response.json.return_value = self.sample_response()
        response.raise_for_status.return_value = None
        with patch.object(weather.cfg, "get", side_effect=lambda key, default=None: {
                "weather.lang": "zh"}.get(key, default)), patch.object(service._client, "get", return_value=response) as get:
            service._fetch_one("uapi-test-key", {"name": "上海"})
        self.assertEqual(get.call_args.kwargs["headers"], {"Authorization": "Bearer uapi-test-key"})
        self.assertEqual(get.call_args.kwargs["params"]["forecast"], "true")
        self.assertEqual(get.call_args.kwargs["params"]["hourly"], "true")
        self.assertEqual(get.call_args.kwargs["params"]["indices"], "true")
        self.assertEqual(get.call_args.kwargs["params"]["minutely"], "true")
        service._client.close()

    def test_clock_display_accepts_legacy_and_epoch_values(self):
        from wisland.ui.pages.info import _fmt_clock
        self.assertEqual(_fmt_clock("06:25", 28800), "06:25")
        self.assertEqual(_fmt_clock(1795127100, 28800), "06:25")
        self.assertEqual(_fmt_clock(None, 28800), "--")

    def test_weather_panel_formatters(self):
        from wisland.ui.pages import info
        data = weather.parse_uapis_weather(self.sample_response(), "上海")
        self.assertEqual(info._clock_text("2026-11-20 16:50:00"), "16:50")
        self.assertEqual(info._clock_text("2026-11-20T17:05+08:00"), "17:05")
        self.assertEqual(info._clock_text(None), "")
        self.assertIn("PM2.5 25", info._air_text(data))
        self.assertIn("首要 PM10", info._air_text(data))
        self.assertIn("O₃ 80", info._air_text(data))
        self.assertEqual(info._aqi_color(44, 1).name(), "#34c759")
        self.assertEqual(info._aqi_color(160, None).name(), "#ff453a")

    def test_edit_city_and_make_default(self):
        with tempfile.TemporaryDirectory() as folder:
            cache_path = Path(folder) / "weather.json"
            service = weather.WeatherService(cache_path=cache_path)
            values = {"weather.cities": [{"name": "上海"}, {"name": "北京"}],
                      "weather.uapi_key": "", "weather.lang": "zh"}
            today = date.today().isoformat()
            cache = {}
            for name in ("上海", "北京", "广州"):
                row = weather.parse_uapis_weather(self.sample_response(), name)
                row["name"] = name
                cache[service._city_key({"name": name})] = {
                    "attempt_date": today, "data_date": today, "error": "", "data": row}
            cache_path.write_text(json.dumps({"version": weather._CACHE_VERSION, "cities": cache}),
                                  encoding="utf-8")
            pending = []

            def fake_run_bg(fn, *args, on_done=None, on_error=None):
                pending.append((fn, args))

            def set_config(key, value):
                values[key] = value

            with patch.object(weather.cfg, "get", side_effect=lambda key, default=None: values.get(key, default)), \
                    patch.object(weather.cfg, "set", side_effect=set_config), \
                    patch.object(weather, "run_bg", side_effect=fake_run_bg):
                service.refresh()
                self.assertEqual(pending, [])
                service.edit_city(1, "广州")
                self.assertEqual(values["weather.cities"], [{"name": "上海"}, {"name": "广州"}])
                self.assertEqual(pending, [])  # 广州今日已有缓存，不再请求
                self.assertEqual([r["name"] for r in service.cities_data], ["上海", "广州"])
                service.make_default(1)
                self.assertEqual(values["weather.cities"], [{"name": "广州"}, {"name": "上海"}])
                self.assertEqual([r["name"] for r in service.cities_data], ["广州", "上海"])
                service.edit_city(1, "广州")  # 与现有城市重名，拒绝
                self.assertEqual(values["weather.cities"], [{"name": "广州"}, {"name": "上海"}])
            service._client.close()

    def test_older_cache_version_is_rebuilt(self):
        with tempfile.TemporaryDirectory() as folder:
            cache_path = Path(folder) / "weather.json"
            cache_path.write_text(json.dumps({"version": 1, "cities": {"x": {"attempt_date": "2026-11-20"}}}),
                                  encoding="utf-8")
            service = weather.WeatherService(cache_path=cache_path)
            self.assertEqual(service._read_cache(), {})
            service._client.close()

    def test_daily_cache_prevents_a_second_city_request(self):
        with tempfile.TemporaryDirectory() as folder:
            cache_path = Path(folder) / "weather.json"
            service = weather.WeatherService(cache_path=cache_path)
            city = {"name": "上海"}
            values = {"weather.cities": [city], "weather.uapi_key": "", "weather.lang": "zh"}
            calls = []

            def fake_run_bg(fn, *args, on_done=None, on_error=None):
                calls.append((fn, args, on_done))
                return None

            with patch.object(weather.cfg, "get", side_effect=lambda key, default=None: values.get(key, default)), \
                    patch.object(weather, "run_bg", side_effect=fake_run_bg):
                service.refresh()
                self.assertEqual(len(calls), 1)
                fetch, args, done = calls.pop()
                with patch.object(service, "_fetch_one", return_value=weather.parse_uapis_weather(self.sample_response(), "上海")):
                    done(fetch(*args))

                restarted = weather.WeatherService(cache_path=cache_path)
                restarted.refresh()
                self.assertEqual(calls, [])
                self.assertEqual(len(restarted.cities_data), 1)
                self.assertTrue(restarted.cities_data[0]["ok"])
                self.assertEqual(json.loads(cache_path.read_text(encoding="utf-8"))["cities"][
                    restarted._city_key(city)]["attempt_date"], date.today().isoformat())
                restarted._client.close()
            service._client.close()


if __name__ == "__main__":
    unittest.main()
