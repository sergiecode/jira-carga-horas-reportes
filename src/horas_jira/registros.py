"""Obtención de los registros de horas (worklogs) desde Jira."""

from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from .jira_client import JiraClient
from .periodo import Periodo


@dataclass(frozen=True)
class Registro:
    """Un worklog, ya filtrado y normalizado."""

    fecha: date  # fecha de inicio del worklog, en la zona horaria configurada
    persona: str
    ticket: str
    resumen: str
    proyecto: str
    horas: float
    comentario: str


def armar_jql(proyectos: list[str], periodo: Periodo) -> str:
    # worklogDate se evalúa en la zona horaria del usuario de Jira, que puede no coincidir con la
    # configurada. Ampliamos un día de cada lado para no perder tickets; el filtro exacto se hace
    # después, worklog por worklog.
    desde = periodo.desde - timedelta(days=1)
    hasta = periodo.hasta + timedelta(days=1)
    jql = f'worklogDate >= "{desde:%Y-%m-%d}" AND worklogDate <= "{hasta:%Y-%m-%d}"'
    if proyectos:  # sin proyectos: todos los que el usuario puede ver
        jql = f"project in ({', '.join(proyectos)}) AND {jql}"
    return jql


def obtener_registros(
    cliente: JiraClient,
    proyectos: list[str],
    periodo: Periodo,
    zona: ZoneInfo,
    workers: int = 8,
) -> list[Registro]:
    """Busca los tickets con horas en el período y devuelve sus worklogs dentro del período."""
    print("Buscando tickets con horas cargadas...")
    issues = list(cliente.buscar_issues(armar_jql(proyectos, periodo), ["summary", "project"]))
    print(f"  {len(issues)} tickets encontrados.")
    if not issues:
        return []

    print(f"Leyendo worklogs ({workers} en paralelo)...")
    started_after = periodo.inicio_epoch_ms(zona)
    registros: list[Registro] = []
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futuros = {pool.submit(cliente.obtener_worklogs, issue["key"], started_after): issue for issue in issues}
        for n, futuro in enumerate(as_completed(futuros), start=1):
            issue = futuros[futuro]
            # startedAfter solo acota el inicio: el filtro exacto por fecha local se hace acá.
            del_issue = [_a_registro(wl, issue, zona) for wl in futuro.result()]
            nuevos = [r for r in del_issue if periodo.contiene(r.fecha)]
            registros.extend(nuevos)
            print(f"  [{n}/{len(issues)}] {issue['key']}: {len(nuevos)} registros")

    registros.sort(key=lambda r: (r.fecha, r.persona, r.ticket))
    return registros


def _a_registro(worklog: dict, issue: dict, zona: ZoneInfo) -> Registro:
    campos = issue.get("fields", {})
    return Registro(
        fecha=parsear_fecha_jira(worklog["started"]).astimezone(zona).date(),
        persona=(worklog.get("author") or {}).get("displayName", "(desconocido)"),
        ticket=issue["key"],
        resumen=campos.get("summary", ""),
        proyecto=(campos.get("project") or {}).get("name", ""),
        horas=worklog.get("timeSpentSeconds", 0) / 3600,
        comentario=adf_a_texto(worklog.get("comment")),
    )


def parsear_fecha_jira(valor: str) -> datetime:
    """Jira devuelve fechas como '2026-09-28T09:00:00.000-0300'."""
    return datetime.strptime(valor, "%Y-%m-%dT%H:%M:%S.%f%z")


# --- ADF (Atlassian Document Format) -> texto plano --------------------------

_NODOS_BLOQUE = {"paragraph", "heading", "listItem", "codeBlock", "blockquote", "tableRow", "rule"}


def adf_a_texto(adf: dict | str | None) -> str:
    """Extrae el texto plano de un documento ADF (o devuelve el texto si ya viene plano)."""
    if not adf:
        return ""
    if isinstance(adf, str):
        return adf.strip()
    lineas = _texto_nodo(adf).splitlines()
    return "\n".join(linea.strip() for linea in lineas if linea.strip())


def _texto_nodo(nodo: dict) -> str:
    tipo = nodo.get("type")
    if tipo == "text":
        return nodo.get("text", "")
    if tipo == "hardBreak":
        return "\n"
    if tipo in ("mention", "emoji", "date", "status", "inlineCard"):
        attrs = nodo.get("attrs", {})
        return str(attrs.get("text") or attrs.get("shortName") or attrs.get("url") or "")
    texto = "".join(_texto_nodo(hijo) for hijo in nodo.get("content", []))
    return texto + "\n" if tipo in _NODOS_BLOQUE else texto
