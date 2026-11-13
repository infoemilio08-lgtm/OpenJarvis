"""Herramientas personales de "mi-jarvis" (todas de SOLO LECTURA).

* ``mis_documentos`` — lista y lee los archivos de tu carpeta de documentos
  (txt, md, csv, pdf, docx, xlsx).
* ``agenda`` — eventos de tu calendario a partir de su dirección iCal privada
  (variable ``CALENDARIO_ICS_URL``; varias separadas por comas).
* ``correo`` — correos de Gmail por IMAP con una contraseña de aplicación
  (variables ``GMAIL_USUARIO`` y ``GMAIL_CLAVE_APP``). Nunca marca nada como
  leído, ni envía ni borra.
"""

from __future__ import annotations

import os
import re
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

from openjarvis.core.registry import ToolRegistry
from openjarvis.core.types import ToolResult
from openjarvis.tools._stubs import BaseTool, ToolSpec

_TZ = "Europe/Madrid"
_MAX_CHARS = 12000


def _now() -> datetime:
    from zoneinfo import ZoneInfo

    return datetime.now(ZoneInfo(_TZ))


def _fail(tool: str, msg: str) -> ToolResult:
    return ToolResult(tool_name=tool, content=msg, success=False)


# --------------------------------------------------------------------------
# Documentos
# --------------------------------------------------------------------------

_DOC_EXTS = {".txt", ".md", ".csv", ".pdf", ".docx", ".xlsx"}


def _docs_dir() -> Path:
    env = os.environ.get("MI_JARVIS_DOCS", "").strip()
    if env:
        return Path(env).expanduser()
    onedrive = Path.home() / "OneDrive" / "Documentos" / "JARVIS" / "Mis documentos"
    if onedrive.exists():
        return onedrive
    return Path.home() / ".openjarvis" / "documentos"


def _read_doc(path: Path) -> str:
    ext = path.suffix.lower()
    if ext in {".txt", ".md", ".csv"}:
        return path.read_text(encoding="utf-8", errors="replace")
    if ext == ".pdf":
        from pypdf import PdfReader

        reader = PdfReader(str(path))
        return "\n".join((p.extract_text() or "") for p in reader.pages[:60])
    if ext == ".docx":
        import docx

        d = docx.Document(str(path))
        parts = [p.text for p in d.paragraphs]
        for table in d.tables:
            for row in table.rows:
                parts.append(" | ".join(c.text for c in row.cells))
        return "\n".join(parts)
    if ext == ".xlsx":
        from openpyxl import load_workbook

        wb = load_workbook(str(path), read_only=True, data_only=True)
        out = []
        for ws in wb.worksheets[:5]:
            out.append(f"## Hoja: {ws.title}")
            for i, row in enumerate(ws.iter_rows(values_only=True)):
                if i >= 300:
                    break
                if any(v is not None for v in row):
                    out.append(" | ".join("" if v is None else str(v) for v in row))
        return "\n".join(out)
    raise ValueError(f"Formato no soportado: {ext}")


@ToolRegistry.register("mis_documentos")
class MisDocumentosTool(BaseTool):
    tool_id = "mis_documentos"

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="mis_documentos",
            description=(
                "Lista o lee los documentos personales del usuario (tareas,"
                " notas, listas, PDFs, Word, Excel). Úsala cuando pregunte por"
                " sus tareas, pendientes, notas o algún documento suyo. Primero"
                " accion='listar' y luego accion='leer' con el nombre."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "accion": {"type": "string", "enum": ["listar", "leer"]},
                    "nombre": {
                        "type": "string",
                        "description": "Nombre del archivo a leer (tal como sale en la lista).",
                    },
                },
                "required": ["accion"],
            },
            category="filesystem",
            required_capabilities=["file:read"],
        )

    def execute(self, **params: Any) -> ToolResult:
        base = _docs_dir()
        if not base.exists():
            return _fail(
                "mis_documentos",
                f"La carpeta de documentos ({base}) no existe todavía.",
            )
        files = sorted(
            (p for p in base.rglob("*") if p.is_file() and p.suffix.lower() in _DOC_EXTS),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )
        accion = str(params.get("accion", "listar")).lower()
        if accion != "leer":
            if not files:
                return ToolResult(
                    tool_name="mis_documentos",
                    content="La carpeta de documentos está vacía.",
                    success=True,
                )
            lines = [
                f"- {p.relative_to(base)} (modificado "
                f"{datetime.fromtimestamp(p.stat().st_mtime):%d/%m/%Y %H:%M})"
                for p in files[:100]
            ]
            return ToolResult(
                tool_name="mis_documentos",
                content=f"Documentos en {base}:\n" + "\n".join(lines),
                success=True,
            )

        nombre = str(params.get("nombre", "")).strip().lower()
        if not nombre:
            return _fail("mis_documentos", "Indica el nombre del documento.")
        match = next((p for p in files if str(p.relative_to(base)).lower() == nombre), None)
        if match is None:
            match = next(
                (p for p in files if nombre in str(p.relative_to(base)).lower()), None
            )
        if match is None:
            return _fail("mis_documentos", f"No encuentro ningún documento llamado '{nombre}'.")
        # Defensa: el archivo debe estar dentro de la carpeta
        if base.resolve() not in match.resolve().parents:
            return _fail("mis_documentos", "Ruta no permitida.")
        try:
            text = _read_doc(match)
        except Exception as exc:
            return _fail("mis_documentos", f"No he podido leer {match.name}: {exc}")
        text = re.sub(r"\n{3,}", "\n\n", text).strip()
        return ToolResult(
            tool_name="mis_documentos",
            content=f"Documento: {match.relative_to(base)}\n\n{text[:_MAX_CHARS]}",
            success=True,
        )


# --------------------------------------------------------------------------
# Agenda (calendario iCal)
# --------------------------------------------------------------------------


@ToolRegistry.register("agenda")
class AgendaTool(BaseTool):
    tool_id = "agenda"

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="agenda",
            description=(
                "Eventos y citas del calendario del usuario. Úsala cuando pregunte"
                " qué tiene hoy, mañana, esta semana o por una cita concreta."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "desde": {
                        "type": "string",
                        "description": "Fecha inicial AAAA-MM-DD (por defecto hoy).",
                    },
                    "dias": {
                        "type": "integer",
                        "description": "Número de días a mostrar (1 = solo ese día). Por defecto 1.",
                    },
                },
            },
            category="calendar",
            required_capabilities=["network:fetch"],
        )

    def execute(self, **params: Any) -> ToolResult:
        urls = [
            u.strip()
            for u in os.environ.get("CALENDARIO_ICS_URL", "").split(",")
            if u.strip()
        ]
        if not urls:
            return _fail(
                "agenda",
                "El calendario no está conectado todavía (falta CALENDARIO_ICS_URL).",
            )
        try:
            dias = max(1, min(31, int(params.get("dias") or 1)))
        except (TypeError, ValueError):
            dias = 1
        try:
            start = date.fromisoformat(str(params.get("desde") or _now().date().isoformat()))
        except ValueError:
            start = _now().date()
        end = start + timedelta(days=dias)

        import httpx
        import icalendar
        import recurring_ical_events
        from zoneinfo import ZoneInfo

        tz = ZoneInfo(_TZ)
        events = []
        for url in urls:
            try:
                resp = httpx.get(
                    url.replace("webcal://", "https://"), timeout=20, follow_redirects=True
                )
                resp.raise_for_status()
                cal = icalendar.Calendar.from_ical(resp.content)
                for ev in recurring_ical_events.of(cal).between(start, end):
                    dt = ev.get("DTSTART").dt
                    all_day = not isinstance(dt, datetime)
                    if not all_day and dt.tzinfo is not None:
                        dt = dt.astimezone(tz)
                    events.append(
                        (
                            datetime.combine(dt, datetime.min.time()) if all_day else dt.replace(tzinfo=None),
                            all_day,
                            str(ev.get("SUMMARY", "(sin título)")),
                            str(ev.get("LOCATION", "") or ""),
                        )
                    )
            except Exception as exc:
                return _fail("agenda", f"No he podido leer el calendario: {exc}")

        events.sort(key=lambda e: e[0])
        rango = start.strftime("%d/%m/%Y") + (
            f" a {(end - timedelta(days=1)).strftime('%d/%m/%Y')}" if dias > 1 else ""
        )
        if not events:
            return ToolResult(tool_name="agenda", content=f"Sin eventos ({rango}).", success=True)
        lines = [f"Eventos ({rango}):"]
        for when, all_day, title, loc in events[:80]:
            hora = "todo el día" if all_day else when.strftime("%H:%M")
            lines.append(
                f"- {when:%a %d/%m} {hora}: {title}" + (f" ({loc})" if loc else "")
            )
        return ToolResult(tool_name="agenda", content="\n".join(lines), success=True)


# --------------------------------------------------------------------------
# Correo (Gmail por IMAP, solo lectura)
# --------------------------------------------------------------------------


def _decode(value: Any) -> str:
    from email.header import decode_header, make_header

    try:
        return str(make_header(decode_header(value or "")))
    except Exception:
        return str(value or "")


def _body_text(msg: Any) -> str:
    import html as _html

    if msg.is_multipart():
        for part in msg.walk():
            if part.get_content_type() == "text/plain" and not part.get_filename():
                try:
                    return part.get_content()
                except Exception:
                    continue
        for part in msg.walk():
            if part.get_content_type() == "text/html":
                try:
                    raw = part.get_content()
                except Exception:
                    continue
                return _html.unescape(re.sub(r"<[^>]+>", " ", raw))
        return ""
    try:
        content = msg.get_content()
    except Exception:
        return ""
    if msg.get_content_type() == "text/html":
        content = _html.unescape(re.sub(r"<[^>]+>", " ", content))
    return content


@ToolRegistry.register("correo")
class CorreoTool(BaseTool):
    tool_id = "correo"

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="correo",
            description=(
                "Consulta el correo de Gmail del usuario (solo lectura: no envía,"
                " no borra, no marca como leído). accion='no_leidos' para los"
                " correos sin leer, 'recientes' para los últimos, 'buscar' con"
                " 'texto' para buscar (admite sintaxis de Gmail, p. ej."
                " 'from:banco newer_than:7d')."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "accion": {"type": "string", "enum": ["no_leidos", "recientes", "buscar"]},
                    "texto": {"type": "string", "description": "Qué buscar (solo con accion='buscar')."},
                    "cantidad": {"type": "integer", "description": "Máximo de correos (por defecto 8)."},
                    "con_contenido": {
                        "type": "boolean",
                        "description": "Incluir un extracto del cuerpo de cada correo.",
                    },
                },
                "required": ["accion"],
            },
            category="email",
            required_capabilities=["network:fetch"],
        )

    def execute(self, **params: Any) -> ToolResult:
        import email
        import imaplib
        from email import policy

        user = os.environ.get("GMAIL_USUARIO", "").strip()
        pwd = os.environ.get("GMAIL_CLAVE_APP", "").replace(" ", "").strip()
        if not user or not pwd:
            return _fail(
                "correo",
                "El correo no está conectado todavía (faltan GMAIL_USUARIO y GMAIL_CLAVE_APP).",
            )
        accion = str(params.get("accion", "no_leidos")).lower()
        try:
            cantidad = max(1, min(20, int(params.get("cantidad") or 8)))
        except (TypeError, ValueError):
            cantidad = 8
        con_contenido = bool(params.get("con_contenido", False))

        try:
            imap = imaplib.IMAP4_SSL("imap.gmail.com", 993, timeout=25)
            imap.login(user, pwd)
            imap.select("INBOX", readonly=True)  # nunca modifica el buzón
            if accion == "buscar":
                texto = str(params.get("texto", "")).replace('"', "").strip()
                if not texto:
                    imap.logout()
                    return _fail("correo", "Indica qué buscar.")
                typ, data = imap.search(None, "X-GM-RAW", f'"{texto}"')
            elif accion == "recientes":
                typ, data = imap.search(None, "ALL")
            else:
                typ, data = imap.search(None, "UNSEEN")
            ids = (data[0] or b"").split() if typ == "OK" else []
            total = len(ids)
            ids = ids[-cantidad:][::-1]
            lines = []
            for mid in ids:
                part = "(BODY.PEEK[])" if con_contenido else "(BODY.PEEK[HEADER])"
                typ, msg_data = imap.fetch(mid, part)
                if typ != "OK" or not msg_data or not isinstance(msg_data[0], tuple):
                    continue
                msg = email.message_from_bytes(msg_data[0][1], policy=policy.default)
                line = (
                    f"- {_decode(msg.get('Date'))[:25]} | De: {_decode(msg.get('From'))}"
                    f" | Asunto: {_decode(msg.get('Subject'))}"
                )
                if con_contenido:
                    body = re.sub(r"\s+", " ", _body_text(msg)).strip()[:600]
                    line += f"\n  {body}"
                lines.append(line)
            imap.logout()
        except imaplib.IMAP4.error as exc:
            return _fail("correo", f"Gmail ha rechazado el acceso: {exc}")
        except Exception as exc:
            return _fail("correo", f"Error leyendo el correo: {exc}")

        titulo = {
            "no_leidos": f"Correos sin leer: {total}",
            "recientes": "Últimos correos",
            "buscar": f"Resultados de búsqueda: {total}",
        }.get(accion, "Correos")
        return ToolResult(
            tool_name="correo",
            content=titulo + ("\n" + "\n".join(lines) if lines else "\n(ninguno)"),
            success=True,
        )


__all__ = ["AgendaTool", "CorreoTool", "MisDocumentosTool"]
