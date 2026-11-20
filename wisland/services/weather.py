# -*- coding: utf-8 -*-
"""城市天气：使用 UAPIs 天气接口，每个城市每天最多请求一次并持久缓存结果。"""
from __future__ import annotations

import hashlib
import json
import re
import time
from datetime import date, datetime, time as datetime_time, timedelta, timezone
from pathlib import Path

import httpx
from PySide6 import QtCore, QtGui

from wisland.core.bg import run_bg, ui
from wisland.core.config import DATA_DIR, cfg

API = "https://uapis.cn/api/v1/misc/weather"
_CACHE_VERSION = 2  # v2 增加空气质量、生活指数、分钟级降水等字段
_ASSET_DIR = Path(__file__).resolve().parents[1] / "assets"
_ICON_CSS = _ASSET_DIR / "qweather-icons.css"
_ICON_FONT = _ASSET_DIR / "qweather-icons.ttf"
_ICON_MAP: dict[str, str] | None = None
_FONT_FAMILY: str | None = None
_FONT_ATTEMPTED = False
_FALLBACK_ICON = ("\uE753", "Segoe Fluent Icons")


# The UAPI current-weather response includes QWeather-compatible icon codes.
# Forecast rows may only contain descriptions, so map those to the same icon set.
_TEXT_ICON_CODES = {
    "特大暴雨": "312", "大暴雨": "311", "暴雨": "310", "极端降雨": "308",
    "大雨到暴雨": "316", "暴雨到大暴雨": "317", "大暴雨到特大暴雨": "318",
    "中到大雨": "315", "小到中雨": "314", "雷阵雨伴有冰雹": "304",
    "强雷阵雨": "303", "雷阵雨": "302", "强阵雨": "301", "阵雨": "300",
    "冻雨": "313", "毛毛雨": "309", "小雨": "305", "中雨": "306", "大雨": "307",
    "大雪到暴雪": "410", "中到大雪": "409", "小到中雪": "408", "阵雨夹雪": "406",
    "雨雪": "405", "雨夹雪": "404", "暴雪": "403", "大雪": "402", "中雪": "401",
    "小雪": "400", "阵雪": "407", "雪": "499", "强阵雪": "457", "阵雪（夜间）": "457",
    "阵雨（夜间）": "350", "强阵雨（夜间）": "351", "沙尘暴": "507", "强沙尘暴": "508",
    "扬沙": "503", "浮尘": "502", "霾": "502", "重度霾": "504", "中度霾": "503",
    "严重霾": "504", "浓雾": "501", "大雾": "501", "雾": "501", "薄雾": "500",
    "晴间多云": "103", "少云": "102", "多云": "101", "阴": "104", "晴": "100",
    "thunderstorm with hail": "304", "strong thunderstorm": "303", "thunderstorm": "302",
    "heavy rainstorm": "312", "heavy rain": "307", "moderate rain": "306", "light rain": "305",
    "rain shower": "300", "shower": "300", "drizzle": "309", "freezing rain": "313",
    "heavy snow": "402", "moderate snow": "401", "light snow": "400", "snow": "499",
    "rain and snow": "404", "sleet": "404", "sandstorm": "507", "dust": "502",
    "haze": "502", "fog": "501", "overcast": "104", "mostly cloudy": "101",
    "cloudy": "101", "partly cloudy": "103", "sunny": "100", "clear": "100",
}
_NIGHT_ICON_CODES = {"100": "150", "101": "151", "102": "152", "103": "153",
                     "300": "350", "301": "351"}

_LIFE_NAMES = {
    "clothing": "穿衣", "uv": "紫外线", "car_wash": "洗车", "drying": "晾晒",
    "air_conditioner": "空调", "cold_risk": "感冒", "exercise": "运动", "comfort": "舒适度",
    "travel": "出行", "fishing": "钓鱼", "allergy": "过敏", "sunscreen": "防晒",
    "mood": "心情", "beer": "啤酒", "umbrella": "雨伞", "traffic": "交通",
    "air_purifier": "空气净化", "pollen": "花粉",
}


def _icon_map() -> dict[str, str]:
    """Read the official QWeather font's CSS codepoint map bundled with the app."""
    global _ICON_MAP
    if _ICON_MAP is None:
        try:
            css = _ICON_CSS.read_text(encoding="utf-8")
            _ICON_MAP = {
                code: chr(int(point, 16))
                for code, point in re.findall(
                    r"\.qi-(\d+)::before\s*\{\s*content:\s*\"\\([0-9a-fA-F]+)\"",
                    css,
                )
            }
        except OSError:
            _ICON_MAP = {}
    return _ICON_MAP


def _icon_font_family() -> str | None:
    """Load the bundled QWeather font once after the Qt application exists."""
    global _FONT_ATTEMPTED, _FONT_FAMILY
    if _FONT_FAMILY:
        return _FONT_FAMILY
    if _FONT_ATTEMPTED or QtGui.QGuiApplication.instance() is None:
        return None
    _FONT_ATTEMPTED = True
    font_id = QtGui.QFontDatabase.addApplicationFont(str(_ICON_FONT))
    if font_id >= 0:
        families = QtGui.QFontDatabase.applicationFontFamilies(font_id)
        if families:
            _FONT_FAMILY = families[0]
    return _FONT_FAMILY


def weather_glyph(icon_code: str) -> tuple[str, str]:
    """Return a QWeather Icons glyph and font family for a weather icon code."""
    code = str(icon_code or "")
    glyph = _icon_map().get(code)
    family = _icon_font_family()
    if glyph and family:
        return glyph, family
    return _FALLBACK_ICON


def _icon_from_description(description: str, hour: int | None = None) -> str:
    text = str(description or "").strip().casefold()
    code = next((value for label, value in sorted(_TEXT_ICON_CODES.items(), key=lambda item: len(item[0]), reverse=True)
                 if label.casefold() in text), "999")
    if hour is not None and (hour >= 18 or hour < 6):
        code = _NIGHT_ICON_CODES.get(code, code)
    return code


def _epoch_and_offset(value) -> tuple[int | None, int, bool]:
    """Parse API forecast time into Unix seconds, UTC offset and offset presence."""
    if isinstance(value, (int, float)):
        return int(value), 0, False
    try:
        parsed = datetime.fromisoformat(str(value).strip().replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None, 0, False
    has_offset = parsed.tzinfo is not None
    if not has_offset:
        parsed = parsed.astimezone()
    offset = parsed.utcoffset()
    return int(parsed.timestamp()), int(offset.total_seconds()) if offset else 0, has_offset


def _number(value) -> float | None:
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _local_clock_epoch(day_value, clock_value, tz_offset: int) -> int | None:
    """Convert a city's YYYY-MM-DD and HH:MM forecast values to Unix seconds."""
    if isinstance(clock_value, (int, float)):
        return int(clock_value)
    if not day_value or not clock_value:
        return None
    try:
        day = date.fromisoformat(str(day_value)[:10])
        clock = datetime_time.fromisoformat(str(clock_value))
        local = datetime.combine(day, clock, timezone(timedelta(seconds=tz_offset)))
        return int(local.timestamp())
    except (TypeError, ValueError):
        return None


def parse_uapis_weather(payload: dict, requested_city: str = "", lang: str = "zh") -> dict:
    """Normalize one UAPIs current/forecast response for the existing weather UI."""
    if not isinstance(payload, dict) or payload.get("temperature") is None:
        message = (payload.get("message") or payload.get("detail") or payload.get("error")) if isinstance(payload, dict) else None
        raise ValueError(str(message or "天气服务返回的数据不完整"))

    forecast = payload.get("forecast") if isinstance(payload.get("forecast"), list) else []
    today_forecast = forecast[0] if forecast and isinstance(forecast[0], dict) else {}
    hourly_source = payload.get("hourly_forecast") if isinstance(payload.get("hourly_forecast"), list) else []
    _, tz_offset, has_api_offset = (_epoch_and_offset(hourly_source[0].get("time"))
                                    if hourly_source and isinstance(hourly_source[0], dict) else (None, 0, False))
    if not has_api_offset:
        local_offset = datetime.now().astimezone().utcoffset()
        tz_offset = int(local_offset.total_seconds()) if local_offset else 0

    current_desc = str(payload.get("weather") or "")
    current_icon = str(payload.get("weather_icon") or _icon_from_description(current_desc, 12))
    temp_min = _number(payload.get("temp_min", today_forecast.get("temp_min")))
    temp_max = _number(payload.get("temp_max", today_forecast.get("temp_max")))
    visibility_km = _number(payload.get("visibility"))
    wind_speed = _number(payload.get("wind_speed"))
    air_source = payload.get("air_pollutants") if isinstance(payload.get("air_pollutants"), dict) else {}
    air = {key: _number(air_source.get(key)) for key in ("pm25", "pm10", "o3", "no2", "so2", "co")}
    alerts = []
    for item in payload.get("alerts") if isinstance(payload.get("alerts"), list) else []:
        if not isinstance(item, dict):
            continue
        guidance = item.get("guidance") if isinstance(item.get("guidance"), list) else []
        alert_type = str(item.get("type") or "").strip()
        alert_level = str(item.get("level") or "").strip()
        alerts.append({
            "title": str(item.get("title") or f"{alert_type}{alert_level}预警").strip(),
            "type": alert_type,
            "level": alert_level,
            "text": str(item.get("text") or item.get("description") or "").strip(),
            "publisher": str(item.get("publisher") or item.get("sender") or "").strip(),
            "publish_time": str(item.get("publish_time") or item.get("time") or "").strip(),
            "guidance": [str(value).strip() for value in guidance if str(value).strip()],
        })
    life_indices = []
    life_source = payload.get("life_indices") if isinstance(payload.get("life_indices"), dict) else {}
    for key, item in life_source.items():
        if not isinstance(item, dict):
            continue
        level = str(item.get("level") or item.get("brief") or "").strip()
        if not level:
            continue
        life_indices.append({
            "key": str(key), "name": _LIFE_NAMES.get(str(key), str(key)),
            "level": level, "brief": str(item.get("brief") or "").strip(),
            "advice": str(item.get("advice") or "").strip(),
        })
    minutely_source = payload.get("minutely_forecast")
    if not isinstance(minutely_source, dict):
        minutely_source = payload.get("minutely_precip")
    minutely = str(minutely_source.get("summary") or "").strip() if isinstance(minutely_source, dict) else ""
    out = {
        "ok": True, "error": "", "city": payload.get("city") or requested_city,
        "temp": _number(payload.get("temperature")),
        "feels_like": _number(payload.get("feels_like")),
        "desc": current_desc, "icon": current_icon,
        "humidity": payload.get("humidity"),
        "wind": payload.get("wind_power") or "",
        "wind_speed": wind_speed,
        "wind_direction": payload.get("wind_direction") or "",
        "wind_deg": None,
        "sunrise": _local_clock_epoch(today_forecast.get("date"),
                                      payload.get("sunrise") or today_forecast.get("sunrise"), tz_offset),
        "sunset": _local_clock_epoch(today_forecast.get("date"),
                                     payload.get("sunset") or today_forecast.get("sunset"), tz_offset),
        "pressure": payload.get("pressure"),
        # The current UI formats visibility in meters, while UAPIs reports kilometers.
        "visibility": visibility_km * 1000 if visibility_km is not None else None,
        "clouds": payload.get("cloud"),
        "uv": _number(payload.get("uv")),
        "precipitation": _number(payload.get("precipitation")),
        "aqi": _number(payload.get("aqi")),
        "aqi_level": payload.get("aqi_level"),
        "aqi_category": str(payload.get("aqi_category") or "").strip(),
        "aqi_primary": str(payload.get("aqi_primary") or "").strip(),
        "air": {key: value for key, value in air.items() if value is not None},
        "alerts": alerts,
        "life_indices": life_indices,
        "minutely": minutely,
        "report_time": str(payload.get("report_time") or "").strip(),
        "province": str(payload.get("province") or "").strip(),
        "district": str(payload.get("district") or "").strip(),
        "adcode": str(payload.get("adcode") or "").strip(),
        "temp_min": temp_min, "temp_max": temp_max,
        "tz_offset": tz_offset, "hourly": [], "daily": [], "time": time.time(),
    }

    tz = timezone(timedelta(seconds=tz_offset))
    for item in hourly_source[1::3]:
        if not isinstance(item, dict):
            continue
        epoch, item_offset, has_item_offset = _epoch_and_offset(item.get("time"))
        temp = _number(item.get("temperature"))
        if epoch is None or temp is None:
            continue
        item_tz = timezone(timedelta(seconds=item_offset)) if has_item_offset else tz
        local_hour = datetime.fromtimestamp(epoch, item_tz).hour
        pop = _number(item.get("pop")) or 0.0
        if pop > 1:
            pop /= 100.0
        out["hourly"].append({
            "time": epoch, "temp": temp,
            "icon": str(item.get("weather_icon") or _icon_from_description(item.get("weather"), local_hour)),
            "pop": max(0.0, min(1.0, pop)),
            "text": str(item.get("weather") or "").strip(),
            "wind": str(item.get("wind_scale") or "").strip(),
            "wind_direction": str(item.get("wind_direction") or "").strip(),
            "wind_speed": _number(item.get("wind_speed")),
            "uv": _number(item.get("uv_index")),
        })
        if len(out["hourly"]) >= 8:
            break

    for item in forecast[:5]:
        if not isinstance(item, dict):
            continue
        low, high = _number(item.get("temp_min")), _number(item.get("temp_max"))
        if low is None or high is None:
            continue
        out["daily"].append({
            "date": str(item.get("date") or ""), "min": low, "max": high,
            "icon": str(item.get("weather_icon_day") or item.get("weather_icon")
                        or _icon_from_description(item.get("weather_day") or item.get("weather_night"), 12)),
            "text": str(item.get("weather_day") or "").strip(),
            "text_night": str(item.get("weather_night") or "").strip(),
            "week": str(item.get("week") or "").strip(),
            "wind": str(item.get("wind_scale_day") or "").strip(),
            "wind_direction": str(item.get("wind_dir_day") or "").strip(),
            "uv": _number(item.get("uv_index")),
            "sunrise": str(item.get("sunrise") or "").strip(),
            "sunset": str(item.get("sunset") or "").strip(),
        })
    return out


class WeatherService(QtCore.QObject):
    """Multi-city weather with a persistent per-city, per-local-day request cache."""
    updated = QtCore.Signal(dict)
    cities_updated = QtCore.Signal(list)
    city_added = QtCore.Signal(bool, str)

    def __init__(self, parent=None, cache_path: str | Path | None = None):
        super().__init__(parent)
        ui(lambda: None)
        # Ignore broken system proxy settings (e.g. NO_PROXY-only registries);
        # UAPIs is a domestic endpoint, same as the stock quotes client.
        self._client = httpx.Client(timeout=httpx.Timeout(12.0, connect=6.0),
                                    follow_redirects=True, trust_env=False)
        self._busy = False
        self._again = False
        self.last: dict = {}
        self.cities_data: list[dict] = []
        self._cache_path = Path(cache_path) if cache_path else DATA_DIR / "weather_cache.json"
        self._cache: dict = {}
        self._run_cities: list[dict] = []
        self._run_date = ""
        self._pending: list[tuple[int, dict]] = []
        self._working: list[dict | None] = []
        self._adding: set[str] = set()
        self._timer = QtCore.QTimer(self)
        self._timer.timeout.connect(self.refresh)

    def cities(self) -> list[dict]:
        configured = cfg.get("weather.cities")
        if isinstance(configured, list):
            cities = [dict(city) for city in configured if isinstance(city, dict)]
            if cities:
                return cities
        city = {"name": cfg.get("weather.city") or "上海"}
        adcode = cfg.get("weather.adcode")
        if adcode:
            city["adcode"] = adcode
        return [city]

    def _save(self, cities: list[dict]):
        cfg.set("weather.cities", cities)

    @staticmethod
    def _city_key(city: dict) -> str:
        identity = city.get("adcode") or city.get("q") or city.get("name") or "上海"
        return hashlib.sha256(str(identity).strip().casefold().encode("utf-8")).hexdigest()

    def _read_cache(self) -> dict:
        try:
            data = json.loads(self._cache_path.read_text(encoding="utf-8"))
            cities = data.get("cities") if isinstance(data, dict) and data.get("version") == _CACHE_VERSION else None
            return cities if isinstance(cities, dict) else {}
        except (OSError, ValueError, TypeError):
            return {}

    def _write_cache(self, cache: dict):
        self._cache_path.parent.mkdir(parents=True, exist_ok=True)
        temp_path = self._cache_path.with_suffix(self._cache_path.suffix + ".tmp")
        temp_path.write_text(json.dumps({"version": _CACHE_VERSION, "cities": cache}, ensure_ascii=False), encoding="utf-8")
        temp_path.replace(self._cache_path)

    def remove_city(self, index: int):
        cities = self.cities()
        if 0 <= index < len(cities) and len(cities) > 1:
            cities.pop(index)
            self._save(cities)
            if 0 <= index < len(self.cities_data):
                self.cities_data.pop(index)
            self._publish()

    def make_default(self, index: int):
        cities = self.cities()
        if 0 < index < len(cities):
            cities.insert(0, cities.pop(index))
            self._save(cities)
            if index < len(self.cities_data):
                self.cities_data.insert(0, self.cities_data.pop(index))
            self._publish()

    def add_city(self, query: str):
        query = (query or "").strip()
        if not query:
            self.city_added.emit(False, "请输入城市名")
            return
        cities = self.cities()
        if any(str(city.get("name") or city.get("q") or "").strip().casefold() == query.casefold() for city in cities):
            self.city_added.emit(True, f"{query} 已在城市列表中")
            return

        city = {"name": query}
        key = self._city_key(city)
        if key in self._adding:
            self.city_added.emit(False, f"正在查询 {query}")
            return
        self._adding.add(key)
        cache = self._read_cache()
        record = cache.get(key) if isinstance(cache.get(key), dict) else {}
        today = date.today().isoformat()
        if record.get("attempt_date") == today:
            self._adding.discard(key)
            if not isinstance(record.get("data"), dict):
                self.city_added.emit(False, f"{query} 今日已查询且没有可用缓存，请明天再试")
                return
            cities.append(city)
            self._save(cities)
            self.city_added.emit(True, f"已添加 {query}（使用今日缓存）")
            self.refresh()
            return

        record = dict(record)
        record.update(attempt_date=today, error="请求结果尚未缓存（今日不自动重试）")
        cache[key] = record
        try:
            self._write_cache(cache)
        except OSError:
            pass
        api_key = str(cfg.get("weather.uapi_key") or "").strip()

        def added(data):
            self._adding.discard(key)
            cities_now = self.cities()
            if not any(str(item.get("name") or item.get("q") or "").strip().casefold() == query.casefold()
                       for item in cities_now):
                cities_now.append(city)
                self._save(cities_now)
            cached_data = {field: value for field, value in data.items() if field not in ("index", "error")}
            cached_data["error"] = ""
            latest_cache = self._read_cache()
            latest_cache[key] = {"attempt_date": today, "data_date": today, "error": "", "data": cached_data}
            try:
                self._write_cache(latest_cache)
            except OSError:
                pass
            self.city_added.emit(True, f"已添加 {query}")
            self.refresh()

        def failed(exc):
            self._adding.discard(key)
            latest_cache = self._read_cache()
            failed_record = dict(latest_cache.get(key) or {})
            failed_record.update(attempt_date=today, error=self._friendly(exc))
            latest_cache[key] = failed_record
            try:
                self._write_cache(latest_cache)
            except OSError:
                pass
            self.city_added.emit(False, f"查询失败：{self._friendly(exc)}")

        run_bg(self._fetch_one, api_key, city, on_done=added, on_error=failed)

    def edit_city(self, index: int, query: str):
        """把第 index 个城市改成新名字；沿用每日缓存，未缓存时才请求一次。"""
        query = (query or "").strip()
        if not query:
            self.city_added.emit(False, "请输入城市名")
            return
        cities = self.cities()
        if not 0 <= index < len(cities):
            self.city_added.emit(False, "城市不存在")
            return
        old = str(cities[index].get("name") or cities[index].get("q") or "")
        if query.casefold() == old.casefold():
            self.city_added.emit(True, "城市名未变化")
            return
        if any(j != index and str(c.get("name") or c.get("q") or "").strip().casefold() == query.casefold()
               for j, c in enumerate(cities)):
            self.city_added.emit(True, f"{query} 已在城市列表中")
            return
        cities[index] = {"name": query}
        self._save(cities)
        self.city_added.emit(True, f"已将 {old} 修改为 {query}")
        self.refresh()

    def start(self):
        self.refresh()

    def stop(self):
        self._timer.stop()

    def _schedule_next_day(self):
        now = datetime.now().astimezone()
        tomorrow = now.date() + timedelta(days=1)
        next_midnight = datetime.combine(tomorrow, datetime_time.min, tzinfo=now.tzinfo)
        delay_ms = max(1000, int((next_midnight - now).total_seconds() * 1000))
        self._timer.start(delay_ms)

    def refresh(self):
        """Publish today's cache; query only cities not yet attempted today."""
        if self._busy:
            self._again = True
            return
        self._run_date = date.today().isoformat()
        self._run_cities = self.cities()
        self._cache = self._read_cache()
        self._working = [None] * len(self._run_cities)
        self._pending = []

        for index, city in enumerate(self._run_cities):
            key = self._city_key(city)
            record = self._cache.get(key) if isinstance(self._cache.get(key), dict) else {}
            cached = record.get("data") if isinstance(record.get("data"), dict) else None
            if record.get("attempt_date") == self._run_date:
                if cached:
                    row = dict(cached)
                    if record.get("error"):
                        row["error"] = f"今日查询失败，显示上次缓存：{record['error']}"
                else:
                    row = {"ok": False, "error": record.get("error") or "今日天气查询已尝试，暂无可用数据",
                           "name": city.get("name") or city.get("q") or "?"}
                row["index"] = index
                self._working[index] = row
                continue

            self._pending.append((index, city))
            # Persist before sending: even if the app exits mid-request, it won't
            # accidentally repeat a potentially billed request after restarting.
            updated = dict(record)
            updated.update(attempt_date=self._run_date, error="请求结果尚未缓存（今日不自动重试）")
            self._cache[key] = updated

        if not self._pending:
            self.cities_data = [row for row in self._working if row is not None]
            self._publish()
            self._schedule_next_day()
            return

        try:
            self._write_cache(self._cache)
        except OSError:
            # Keep the app usable if its cache directory is read-only.
            pass
        self._busy = True
        key = str(cfg.get("weather.uapi_key") or "").strip()
        run_bg(self._fetch_all, key, self._pending, on_done=self._done, on_error=self._fail)

    @staticmethod
    def _lang() -> str:
        return "en" if str(cfg.get("weather.lang") or "zh").strip().lower().startswith("en") else "zh"

    def _fetch_one(self, key: str, city: dict) -> dict:
        params = {"lang": self._lang(), "extended": "true", "forecast": "true",
                  "hourly": "true", "indices": "true", "minutely": "true"}
        if city.get("adcode"):
            params["adcode"] = str(city["adcode"]).strip()
        else:
            name = str(city.get("q") or city.get("name") or "上海").strip()
            if not name:
                raise ValueError("城市名不能为空")
            params["city"] = name
        headers = {"Authorization": f"Bearer {key}"} if key else {}
        response = self._client.get(API, params=params, headers=headers)
        response.raise_for_status()
        data = parse_uapis_weather(response.json(), str(city.get("name") or city.get("q") or ""), params["lang"])
        data["name"] = str(city.get("name") or data.get("city") or city.get("q") or "")
        return data

    def _fetch_all(self, key: str, pending: list[tuple[int, dict]]) -> list[dict]:
        results = []
        for index, city in pending:
            try:
                item = self._fetch_one(key, city)
            except Exception as exc:  # noqa: BLE001
                item = {"ok": False, "error": self._friendly(exc), "name": city.get("name", "?")}
            item["index"] = index
            results.append(item)
        return results

    def _done(self, results: list[dict]):
        self._busy = False
        by_index = {item.get("index"): item for item in results}
        pending_indices = {index for index, _ in self._pending}
        for index, city in enumerate(self._run_cities):
            if index not in pending_indices:
                continue
            key = self._city_key(city)
            item = by_index.get(index) or {"ok": False, "error": "天气请求没有返回结果", "name": city.get("name", "?")}
            if item.get("ok"):
                cached_data = {field: value for field, value in item.items() if field not in ("index", "error")}
                cached_data["error"] = ""
                self._cache[key] = {"attempt_date": self._run_date, "data_date": self._run_date,
                                    "error": "", "data": cached_data}
                self._working[index] = item
            else:
                record = dict(self._cache.get(key) or {})
                record.update(attempt_date=self._run_date, error=item.get("error") or "天气请求失败")
                self._cache[key] = record
                previous = record.get("data")
                if isinstance(previous, dict):
                    row = dict(previous)
                    row["error"] = f"今日查询失败，显示上次缓存：{record['error']}"
                    row["index"] = index
                    self._working[index] = row
                else:
                    self._working[index] = item

        try:
            self._write_cache(self._cache)
        except OSError:
            pass
        self.cities_data = [row for row in self._working if row is not None]
        self._publish()
        self._schedule_next_day()
        if self._again:
            self._again = False
            self.refresh()

    def _publish(self):
        for index, item in enumerate(self.cities_data):
            item["index"] = index
        self.cities_updated.emit(list(self.cities_data))
        if self.cities_data:
            self._emit_first(self.cities_data[0])

    def _fail(self, exc: Exception):
        self._busy = False
        message = self._friendly(exc)
        for index, city in self._pending:
            key = self._city_key(city)
            record = dict(self._cache.get(key) or {})
            record.update(attempt_date=self._run_date, error=message)
            self._cache[key] = record
            stale = record.get("data")
            if isinstance(stale, dict):
                row = dict(stale)
                row["error"] = f"今日查询失败，显示上次缓存：{message}"
                row["index"] = index
                self._working[index] = row
            else:
                self._working[index] = {"ok": False, "error": message, "name": city.get("name", "?"), "index": index}
        try:
            self._write_cache(self._cache)
        except OSError:
            pass
        self.cities_data = [row for row in self._working if row is not None]
        self._publish()
        self._schedule_next_day()

    @staticmethod
    def _friendly(exc: Exception) -> str:
        if isinstance(exc, httpx.HTTPStatusError):
            code = exc.response.status_code
            return {
                401: "UAPIs Key 无效或已过期",
                402: "UAPIs 免费额度或账户积分不足",
                403: "UAPIs Key 无权调用此接口",
                404: "找不到该城市",
                429: "天气接口请求过于频繁或额度已用完",
            }.get(code, f"天气服务错误 HTTP {code}")
        if isinstance(exc, httpx.TimeoutException):
            return "天气请求超时"
        if isinstance(exc, httpx.TransportError):
            return "无法连接 UAPIs 天气服务"
        if isinstance(exc, ValueError) and str(exc):
            return str(exc)
        return f"天气解析失败：{str(exc)[:60]}"

    def _emit_first(self, item: dict):
        if item.get("ok"):
            self.last = item
        self.updated.emit(item)
