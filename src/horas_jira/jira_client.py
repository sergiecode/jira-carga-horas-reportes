"""Cliente mínimo para la API REST v3 de Jira Cloud."""

import threading
import time
from collections.abc import Iterator

import requests


class JiraError(Exception):
    """Error al comunicarse con Jira (credenciales, permisos, red, etc.)."""


class JiraClient:
    """Cliente HTTP con autenticación básica (email + API token) y reintentos.

    Es seguro usarlo desde varios hilos: cada hilo tiene su propia sesión HTTP.
    """

    def __init__(self, url: str, email: str, api_token: str, *, max_reintentos: int = 5, timeout: float = 30):
        self.url = url.rstrip("/")
        self._auth = (email, api_token)
        self._max_reintentos = max_reintentos
        self._timeout = timeout
        self._local = threading.local()

    # --- Endpoints ---------------------------------------------------------

    def buscar_issues(self, jql: str, campos: list[str]) -> Iterator[dict]:
        """Recorre todos los issues que matchean el JQL (POST /search/jql, pagina con nextPageToken)."""
        cuerpo: dict = {"jql": jql, "fields": campos, "maxResults": 100}
        while True:
            datos = self._request("POST", "/rest/api/3/search/jql", json=cuerpo)
            yield from datos.get("issues", [])
            token = datos.get("nextPageToken")
            if not token or datos.get("isLast", False):
                return
            cuerpo["nextPageToken"] = token

    def obtener_worklogs(self, issue_key: str, started_after_ms: int) -> list[dict]:
        """Todos los worklogs del issue que empezaron después de `started_after_ms` (pagina con startAt/total)."""
        worklogs: list[dict] = []
        start_at = 0
        while True:
            datos = self._request(
                "GET",
                f"/rest/api/3/issue/{issue_key}/worklog",
                params={"startedAfter": started_after_ms, "startAt": start_at, "maxResults": 1000},
            )
            pagina = datos.get("worklogs", [])
            worklogs.extend(pagina)
            start_at += len(pagina)
            if not pagina or start_at >= datos.get("total", 0):
                return worklogs

    def obtener_issue(self, issue_key: str, campos: list[str]) -> dict:
        return self._request("GET", f"/rest/api/3/issue/{issue_key}", params={"fields": ",".join(campos)})

    def tipos_de_issue(self, proyecto: str) -> list[dict]:
        """Tipos de issue que se pueden crear en el proyecto (id, name, ...)."""
        datos = self._request("GET", f"/rest/api/3/issue/createmeta/{proyecto}/issuetypes")
        return datos.get("issueTypes") or datos.get("values") or []

    def campos_de_creacion(self, proyecto: str, tipo_id: str) -> list[dict]:
        """Campos de la pantalla de creación para ese proyecto y tipo (fieldId, required, ...)."""
        datos = self._request(
            "GET", f"/rest/api/3/issue/createmeta/{proyecto}/issuetypes/{tipo_id}", params={"maxResults": 200}
        )
        return datos.get("fields") or datos.get("values") or []

    def crear_issue(self, campos: dict) -> dict:
        """Crea un issue y devuelve {id, key, self}."""
        # Sin reintentos ante 5xx: el issue podría haberse creado igual.
        return self._request("POST", "/rest/api/3/issue", json={"fields": campos}, reintentar_5xx=False)

    def editar_issue(self, issue_key: str, campos: dict) -> None:
        self._request("PUT", f"/rest/api/3/issue/{issue_key}", json={"fields": campos})

    def usuario_actual(self) -> dict:
        """El usuario dueño del token (accountId, displayName, ...)."""
        return self._request("GET", "/rest/api/3/myself")

    def crear_worklog(
        self,
        issue_key: str,
        started: str,
        segundos: int,
        comentario_adf: dict | None = None,
        ajustar_estimacion: bool = True,
    ) -> dict:
        """Crea un worklog a nombre del dueño del token. `started` va como '2026-09-25T09:00:00.000-0300'."""
        cuerpo: dict = {"started": started, "timeSpentSeconds": segundos}
        if comentario_adf:
            cuerpo["comment"] = comentario_adf
        return self._request(
            "POST",
            f"/rest/api/3/issue/{issue_key}/worklog",
            params={"adjustEstimate": "auto" if ajustar_estimacion else "leave"},
            json=cuerpo,
            # Un 5xx en un POST puede haber creado el worklog igual: no reintentar para no duplicar.
            reintentar_5xx=False,
        )

    # --- HTTP --------------------------------------------------------------

    def _sesion(self) -> requests.Session:
        if not hasattr(self._local, "sesion"):
            sesion = requests.Session()
            sesion.auth = self._auth
            sesion.headers["Accept"] = "application/json"
            self._local.sesion = sesion
        return self._local.sesion

    def _request(self, metodo: str, ruta: str, *, reintentar_5xx: bool = True, **kwargs) -> dict:
        """Hace el request y devuelve el JSON. Reintenta ante 429, 5xx y errores de red."""
        url = self.url + ruta
        for intento in range(1, self._max_reintentos + 1):
            ultimo = intento == self._max_reintentos
            try:
                resp = self._sesion().request(metodo, url, timeout=self._timeout, **kwargs)
            except (requests.ConnectionError, requests.Timeout) as e:
                if ultimo:
                    raise JiraError(f"No se pudo conectar con {self.url}: {e}") from e
                time.sleep(_espera_backoff(intento))
                continue

            if resp.status_code == 429 or (resp.status_code >= 500 and reintentar_5xx):
                if ultimo:
                    raise JiraError(f"Jira respondió {resp.status_code} en {ruta} después de {intento} intentos.")
                espera = _espera_retry_after(resp)
                if espera is None:
                    espera = _espera_backoff(intento)
                print(f"  Jira respondió {resp.status_code}; reintento en {espera:.0f}s...")
                time.sleep(espera)
                continue

            if resp.status_code == 401:
                raise JiraError(
                    "Jira rechazó las credenciales (401). Revisá JIRA_EMAIL y JIRA_API_TOKEN en el .env; "
                    "el token se crea en https://id.atlassian.com/manage-profile/security/api-tokens"
                )
            if resp.status_code == 403:
                raise JiraError(
                    f"Sin permisos para acceder a {ruta} (403). Verificá que tu usuario pueda ver "
                    "esos proyectos y sus worklogs."
                )
            if resp.status_code == 404:
                raise JiraError(f"No existe o no tenés acceso (404): {ruta}")
            if not resp.ok:
                raise JiraError(f"Jira respondió {resp.status_code} en {ruta}: {_detalle_error(resp)}")
            return resp.json() if resp.content else {}  # algunos endpoints (PUT) responden 204 sin cuerpo

        raise AssertionError("inalcanzable")


def _detalle_error(resp: requests.Response) -> str:
    """Jira devuelve {"errorMessages": [...], "errors": {"campo": "mensaje"}}; si no, el texto crudo."""
    try:
        datos = resp.json()
        mensajes = list(datos.get("errorMessages", []))
        mensajes += [f"{campo}: {msg}" for campo, msg in datos.get("errors", {}).items()]
        if mensajes:
            return "; ".join(mensajes)
    except (ValueError, AttributeError):
        pass
    return resp.text[:500]


def _espera_retry_after(resp: requests.Response) -> float | None:
    valor = resp.headers.get("Retry-After")
    try:
        return max(float(valor), 0) if valor is not None else None
    except ValueError:
        return None


def _espera_backoff(intento: int) -> float:
    return min(2**intento, 60)
