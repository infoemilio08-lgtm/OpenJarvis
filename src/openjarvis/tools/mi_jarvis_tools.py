"""Herramientas gratuitas y sin clave para "mi-jarvis".

* ``tiempo`` — tiempo actual y previsión con Open-Meteo (gratis, sin registro).
* ``leer_web`` — descarga una página (solo GET) y devuelve su texto, para que
  el agente pueda leer la noticia o el dato que ``web_search`` solo resume.
"""

from __future__ import annotations

import html
import re
from html.parser import HTMLParser
from typing import Any

from openjarvis.core.registry import ToolRegistry
from openjarvis.core.types import ToolResult
from openjarvis.security.ssrf import check_ssrf
from openjarvis.tools._stubs import BaseTool, ToolSpec

_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) mi-jarvis/1.0"
_TIMEOUT = 15.0

# Códigos WMO de Open-Meteo → descripción en español
_WMO = {
    0: "despejado", 1: "casi despejado", 2: "parcialmente nuboso", 3: "cubierto",
    45: "niebla", 48: "niebla con escarcha",
    51: "llovizna débil", 53: "llovizna", 55: "llovizna intensa",
    56: "llovizna helada", 57: "llovizna helada intensa",
    61: "lluvia débil", 63: "lluvia", 65: "lluvia fuerte",
    66: "lluvia helada", 67: "lluvia helada fuerte",
    71: "nieve débil", 73: "nieve", 75: "nieve fuerte", 77: "granos de nieve",
    80: "chubascos débiles", 81: "chubascos", 82: "chubascos violentos",
    85: "chubascos de nieve", 86: "chubascos de nieve fuertes",
    95: "tormenta", 96: "tormenta con granizo", 99: "tormenta con granizo fuerte",
}


def _get_json(url: str, params: dict[str, Any]) -> Any:
    import httpx

    with httpx.Client(timeout=_TIMEOUT, headers={"User-Agent": _UA}) as client:
        resp = client.get(url, params=params)
        resp.raise_for_status()
        return resp.json()


@ToolRegistry.register("tiempo")
class TiempoTool(BaseTool):
    """Tiempo actual y previsión de los próximos días (Open-Meteo)."""

    tool_id = "tiempo"

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="tiempo",
            description=(
                "Tiempo actual y previsión de hasta 7 días para cualquier ciudad"
                " del mundo (temperatura, lluvia, viento). Úsala SIEMPRE que"
                " pregunten por el tiempo, la temperatura o si va a llover."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "ciudad": {
                        "type": "string",
                        "description": "Ciudad, p. ej. 'Huelva' o 'Sevilla, España'.",
                    },
                    "dias": {
                        "type": "integer",
                        "description": "Días de previsión (1-7). Por defecto 3.",
                    },
                },
                "required": ["ciudad"],
            },
            category="weather",
            required_capabilities=["network:fetch"],
        )

    def execute(self, **params: Any) -> ToolResult:
        ciudad = str(params.get("ciudad", "")).strip()
        if not ciudad:
            return ToolResult(tool_name="tiempo", content="Falta la ciudad.", success=False)
        try:
            dias = max(1, min(7, int(params.get("dias") or 3)))
        except (TypeError, ValueError):
            dias = 3
        nombre = ciudad.split(",")[0].strip()
        try:
            geo = _get_json(
                "https://geocoding-api.open-meteo.com/v1/search",
                {"name": nombre, "count": 1, "language": "es", "format": "json"},
            )
            lugares = geo.get("results") or []
            if not lugares:
                return ToolResult(
                    tool_name="tiempo",
                    content=f"No encuentro la ciudad '{ciudad}'.",
                    success=False,
                )
            lugar = lugares[0]
            data = _get_json(
                "https://api.open-meteo.com/v1/forecast",
                {
                    "latitude": lugar["latitude"],
                    "longitude": lugar["longitude"],
                    "current": "temperature_2m,apparent_temperature,relative_humidity_2m,"
                    "precipitation,weather_code,wind_speed_10m",
                    "daily": "weather_code,temperature_2m_max,temperature_2m_min,"
                    "precipitation_probability_max,wind_speed_10m_max",
                    "timezone": "auto",
                    "forecast_days": dias,
                },
            )
        except Exception as exc:  # red, JSON, HTTP
            return ToolResult(
                tool_name="tiempo", content=f"Error consultando el tiempo: {exc}", success=False
            )

        cur = data.get("current", {})
        sitio = ", ".join(
            p for p in (lugar.get("name"), lugar.get("admin1"), lugar.get("country")) if p
        )
        lineas = [
            f"Tiempo en {sitio} ({cur.get('time', '')}, hora local):",
            f"Ahora: {_WMO.get(cur.get('weather_code'), 'desconocido')},"
            f" {cur.get('temperature_2m')} °C (sensación {cur.get('apparent_temperature')} °C),"
            f" humedad {cur.get('relative_humidity_2m')} %, viento"
            f" {cur.get('wind_speed_10m')} km/h, precipitación {cur.get('precipitation')} mm.",
            "Previsión:",
        ]
        d = data.get("daily", {})
        for i, fecha in enumerate(d.get("time", [])):
            lineas.append(
                f"- {fecha}: {_WMO.get(d['weather_code'][i], 'desconocido')},"
                f" mín {d['temperature_2m_min'][i]} °C / máx {d['temperature_2m_max'][i]} °C,"
                f" prob. lluvia {d['precipitation_probability_max'][i]} %,"
                f" viento máx {d['wind_speed_10m_max'][i]} km/h"
            )
        return ToolResult(
            tool_name="tiempo",
            content="\n".join(lineas),
            success=True,
            metadata={"source": "open-meteo.com"},
        )


class _TextExtractor(HTMLParser):
    _SKIP = {"script", "style", "noscript", "svg", "nav", "footer", "header", "form", "aside"}
    _BLOCK = {"p", "div", "br", "li", "h1", "h2", "h3", "h4", "tr", "section", "article"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.title = ""
        self._skip = 0
        self._in_title = False

    def handle_starttag(self, tag: str, attrs: Any) -> None:
        if tag in self._SKIP:
            self._skip += 1
        elif tag == "title":
            self._in_title = True
        elif tag in self._BLOCK:
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in self._SKIP and self._skip:
            self._skip -= 1
        elif tag == "title":
            self._in_title = False

    def handle_data(self, data: str) -> None:
        if self._in_title:
            self.title += data
        elif not self._skip:
            self.parts.append(data)


@ToolRegistry.register("leer_web")
class LeerWebTool(BaseTool):
    """Lee el texto de una página web (solo lectura, GET)."""

    tool_id = "leer_web"

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="leer_web",
            description=(
                "Abre una página web y devuelve su texto. Úsala después de"
                " web_search cuando el resumen no traiga el dato concreto"
                " (resultados deportivos, cifras, detalles de una noticia)."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "url": {"type": "string", "description": "Dirección http(s) de la página."},
                },
                "required": ["url"],
            },
            category="search",
            required_capabilities=["network:fetch"],
        )

    def execute(self, **params: Any) -> ToolResult:
        url = str(params.get("url", "")).strip()
        if not re.match(r"^https?://", url, re.I):
            return ToolResult(
                tool_name="leer_web", content="La URL debe empezar por http(s)://", success=False
            )
        blocked = check_ssrf(url)
        if blocked:
            return ToolResult(tool_name="leer_web", content=f"URL bloqueada: {blocked}", success=False)

        import httpx

        def _guard(request: Any) -> None:  # también en cada redirección
            reason = check_ssrf(str(request.url))
            if reason:
                raise httpx.RequestError(f"URL bloqueada: {reason}", request=request)

        try:
            with httpx.Client(
                timeout=_TIMEOUT,
                follow_redirects=True,
                max_redirects=5,
                headers={"User-Agent": _UA, "Accept-Language": "es-ES,es;q=0.9"},
                event_hooks={"request": [_guard]},
            ) as client:
                resp = client.get(url)
                resp.raise_for_status()
                ctype = resp.headers.get("content-type", "")
                raw = resp.text[:2_000_000]
        except Exception as exc:
            return ToolResult(
                tool_name="leer_web", content=f"No he podido abrir la página: {exc}", success=False
            )

        if "html" in ctype or raw.lstrip().startswith("<"):
            parser = _TextExtractor()
            try:
                parser.feed(raw)
            except Exception:
                pass
            text = "".join(parser.parts)
            title = html.unescape(parser.title.strip())
        else:
            text, title = raw, ""
        text = re.sub(r"[ \t\r\f\v]+", " ", text)
        text = re.sub(r"\n\s*\n+", "\n", text).strip()
        content = (f"Título: {title}\n" if title else "") + f"URL: {resp.url}\n\n" + text[:8000]
        return ToolResult(tool_name="leer_web", content=content, success=True)


__all__ = ["LeerWebTool", "TiempoTool"]
