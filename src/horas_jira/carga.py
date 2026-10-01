"""Carga de horas (worklogs) en Jira, incluso en fechas pasadas."""

import csv
import re
from dataclasses import dataclass, field
from datetime import date, datetime, time
from pathlib import Path
from zoneinfo import ZoneInfo

from .jira_client import JiraClient, JiraError
from .registros import parsear_fecha_jira


class CargaError(Exception):
    """Datos de carga inválidos (duración, fecha, CSV mal armado, etc.)."""


@dataclass(frozen=True)
class Carga:
    ticket: str
    segundos: int
    inicio: datetime  # con zona horaria
    comentario: str
    origen: str  # de dónde vino, para los mensajes ("línea 3", "argumentos")

    @property
    def fecha(self) -> date:
        return self.inicio.date()


@dataclass
class Revision:
    """Resultado de validar una carga contra Jira, antes de registrarla."""

    carga: Carga
    resumen: str = ""
    error: str | None = None
    ya_cargado_segundos: int = 0  # lo que el usuario ya tiene en ese ticket ese día
    avisos: list[str] = field(default_factory=list)


# --- Parseo -----------------------------------------------------------------

_DURACION = re.compile(r"^(?:(?P<h>\d+(?:[.,]\d+)?)h)?\s*(?:(?P<m>\d+)m)?$")


def parsear_duracion(texto: str) -> int:
    """'1h', '30m', '1h30m', '1h 30m', '2.5' o '2,5' (horas) -> segundos."""
    texto = texto.strip().lower()
    if re.fullmatch(r"\d+(?:[.,]\d+)?", texto):
        segundos = round(float(texto.replace(",", ".")) * 3600)
    else:
        m = _DURACION.match(texto)
        if not texto or not m:
            raise CargaError(f"Duración inválida: {texto!r}. Usá por ejemplo 1h, 30m, 1h30m o 2.5")
        horas = float((m["h"] or "0").replace(",", "."))
        segundos = round(horas * 3600) + int(m["m"] or 0) * 60
    if segundos < 60:
        raise CargaError(f"Duración inválida: {texto!r}. El mínimo es 1 minuto.")
    return segundos


def formatear_duracion(segundos: int) -> str:
    horas, resto = divmod(round(segundos / 60), 60)
    if horas and resto:
        return f"{horas}h {resto}m"
    return f"{horas}h" if horas else f"{resto}m"


def armar_carga(
    ticket: str,
    duracion: str,
    fecha: str,
    hora: str,
    comentario: str,
    zona: ZoneInfo,
    origen: str,
) -> Carga:
    try:
        dia = date.fromisoformat(fecha.strip())
    except ValueError:
        raise CargaError(f"Fecha inválida: {fecha!r}. Usá el formato AAAA-MM-DD.") from None
    try:
        hora_inicio = time.fromisoformat(hora.strip())
    except ValueError:
        raise CargaError(f"Hora inválida: {hora!r}. Usá el formato HH:MM.") from None
    if dia > datetime.now(zona).date():
        raise CargaError(f"La fecha {dia:%d/%m/%Y} es futura.")
    ticket = ticket.strip().upper()
    if not re.fullmatch(r"[A-Z][A-Z0-9_]*-\d+", ticket):
        raise CargaError(f"Ticket inválido: {ticket!r}. Usá el formato AAA-123.")
    return Carga(
        ticket=ticket,
        segundos=parsear_duracion(duracion),
        inicio=datetime.combine(dia, hora_inicio, tzinfo=zona),
        comentario=comentario.strip(),
        origen=origen,
    )


def leer_csv(ruta: Path, hora_por_defecto: str, zona: ZoneInfo) -> list[Carga]:
    """Lee un CSV con columnas ticket,horas,fecha[,comentario][,hora]. Acepta ',' o ';' como separador."""
    try:
        texto = ruta.read_text(encoding="utf-8-sig")  # utf-8-sig: tolera el BOM que agrega Excel
    except FileNotFoundError:
        raise CargaError(f"No existe el archivo {ruta}") from None

    primera_linea = texto.splitlines()[0] if texto.strip() else ""
    separador = ";" if primera_linea.count(";") > primera_linea.count(",") else ","
    lector = csv.DictReader(texto.splitlines(), delimiter=separador)
    columnas = {c.strip().lower() for c in (lector.fieldnames or [])}
    faltan = {"ticket", "horas", "fecha"} - columnas
    if faltan:
        raise CargaError(f"Al CSV le faltan las columnas: {', '.join(sorted(faltan))}")

    cargas, errores = [], []
    for n, fila in enumerate(lector, start=2):  # la línea 1 es el encabezado
        fila = {(k or "").strip().lower(): (v or "").strip() for k, v in fila.items()}
        if not any(fila.values()):
            continue
        try:
            cargas.append(
                armar_carga(
                    fila["ticket"],
                    fila["horas"],
                    fila["fecha"],
                    fila.get("hora") or hora_por_defecto,
                    fila.get("comentario", ""),
                    zona,
                    origen=f"línea {n}",
                )
            )
        except CargaError as e:
            errores.append(f"  línea {n}: {e}")
    if errores:
        raise CargaError("El CSV tiene errores:\n" + "\n".join(errores))
    if not cargas:
        raise CargaError("El CSV no tiene ninguna carga.")
    return cargas


# --- Contra Jira -------------------------------------------------------------


def revisar(cliente: JiraClient, cargas: list[Carga], zona: ZoneInfo) -> list[Revision]:
    """Valida cada carga contra Jira: que el ticket exista y si ya hay horas tuyas ese día."""
    yo = cliente.usuario_actual()["accountId"]
    revisiones = [Revision(c) for c in cargas]

    for ticket in sorted({c.ticket for c in cargas}):
        del_ticket = [r for r in revisiones if r.carga.ticket == ticket]
        try:
            resumen = cliente.obtener_issue(ticket, ["summary"])["fields"]["summary"]
            desde = min(r.carga.fecha for r in del_ticket)
            mios_por_dia = segundos_por_dia(cliente, ticket, desde, zona, yo)
        except JiraError as e:
            for r in del_ticket:
                r.error = str(e)
            continue

        for r in del_ticket:
            r.resumen = resumen
            r.ya_cargado_segundos = mios_por_dia.get(r.carga.fecha, 0)
            if r.ya_cargado_segundos:
                r.avisos.append(
                    f"ya tenés {formatear_duracion(r.ya_cargado_segundos)} en {ticket} el {r.carga.fecha:%d/%m}"
                )
    return revisiones


def segundos_por_dia(cliente: JiraClient, ticket: str, desde: date, zona: ZoneInfo, account_id: str) -> dict[date, int]:
    """Segundos cargados por `account_id` en el ticket, por día local, desde `desde`."""
    inicio_ms = int(datetime.combine(desde, time.min, tzinfo=zona).timestamp() * 1000)
    por_dia: dict[date, int] = {}
    for wl in cliente.obtener_worklogs(ticket, inicio_ms):
        if (wl.get("author") or {}).get("accountId") == account_id:
            dia = parsear_fecha_jira(wl["started"]).astimezone(zona).date()
            por_dia[dia] = por_dia.get(dia, 0) + wl.get("timeSpentSeconds", 0)
    return por_dia


def registrar(cliente: JiraClient, carga: Carga, ajustar_estimacion: bool = True) -> str:
    """Crea el worklog en Jira y devuelve su id."""
    worklog = cliente.crear_worklog(
        carga.ticket,
        started=carga.inicio.strftime("%Y-%m-%dT%H:%M:%S.000%z"),
        segundos=carga.segundos,
        comentario_adf=texto_a_adf(carga.comentario),
        ajustar_estimacion=ajustar_estimacion,
    )
    return str(worklog.get("id", ""))


def texto_a_adf(texto: str) -> dict | None:
    """Texto plano -> documento ADF, un párrafo por línea."""
    if not texto:
        return None
    parrafos = [
        {"type": "paragraph", "content": [{"type": "text", "text": linea}]}
        for linea in texto.splitlines()
        if linea.strip()
    ]
    return {"type": "doc", "version": 1, "content": parrafos}
