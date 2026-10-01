"""Tests de la carga de horas, con la API de Jira mockeada."""

import json
from zoneinfo import ZoneInfo

import pytest
import requests

from horas_jira import cli_carga
from horas_jira.carga import CargaError, armar_carga, leer_csv, parsear_duracion, registrar, revisar
from horas_jira.jira_client import JiraClient, JiraError

ZONA = ZoneInfo("America/Argentina/Buenos_Aires")
URL = "https://ejemplo.atlassian.net"


def respuesta(datos: dict, status: int = 200) -> requests.Response:
    r = requests.Response()
    r.status_code = status
    r._content = json.dumps(datos).encode()
    return r


class JiraFalso:
    """Simula /myself, /issue/{key}, GET y POST de /worklog. Guarda los POST recibidos."""

    def __init__(self):
        self.creados: list[dict] = []

    def __call__(self, metodo, url, **kwargs):
        ruta = url.removeprefix(URL)
        if ruta == "/rest/api/3/myself":
            return respuesta({"accountId": "yo", "displayName": "Yo"})
        if ruta == "/rest/api/3/issue/NOEXISTE-1":
            return respuesta({}, status=404)
        if ruta.startswith("/rest/api/3/issue/") and not ruta.endswith("/worklog"):
            return respuesta({"key": ruta.split("/")[-1], "fields": {"summary": "Un ticket"}})
        if metodo == "GET":  # worklogs existentes: 2h mías el 25/09 en AAA-1, 3h de otro
            return respuesta({"total": 2, "worklogs": [
                {"author": {"accountId": "yo"}, "started": "2026-09-25T10:00:00.000-0300", "timeSpentSeconds": 7200},
                {"author": {"accountId": "otro"}, "started": "2026-09-25T10:00:00.000-0300", "timeSpentSeconds": 10800},
            ]})
        self.creados.append({"ruta": ruta, "params": kwargs["params"], "json": kwargs["json"]})
        return respuesta({"id": str(len(self.creados))}, status=201)


@pytest.fixture
def jira(monkeypatch):
    falso = JiraFalso()
    monkeypatch.setattr(requests.Session, "request", falso)
    return falso


@pytest.fixture
def cliente():
    return JiraClient(URL, "yo@ejemplo.com", "token")


@pytest.mark.parametrize("texto,segundos", [
    ("1h", 3600), ("30m", 1800), ("1h30m", 5400), ("1h 30m", 5400),
    ("2.5", 9000), ("2,5", 9000), ("1.5h", 5400), ("90m", 5400),
])
def test_parsear_duracion(texto, segundos):
    assert parsear_duracion(texto) == segundos


@pytest.mark.parametrize("texto", ["", "h", "abc", "0", "0m", "1x"])
def test_duracion_invalida(texto):
    with pytest.raises(CargaError):
        parsear_duracion(texto)


def test_armar_carga_valida_datos():
    c = armar_carga("aaa-1", "1h", "2026-09-25", "09:00", "", ZONA, "args")
    assert c.ticket == "AAA-1"
    assert c.inicio.isoformat() == "2026-09-25T09:00:00-03:00"
    with pytest.raises(CargaError, match="Fecha"):
        armar_carga("AAA-1", "1h", "25/09/2026", "09:00", "", ZONA, "args")
    with pytest.raises(CargaError, match="futura"):
        armar_carga("AAA-1", "1h", "2999-01-01", "09:00", "", ZONA, "args")
    with pytest.raises(CargaError, match="Ticket"):
        armar_carga("AAA 1", "1h", "2026-09-25", "09:00", "", ZONA, "args")


def test_csv_con_punto_y_coma_y_bom(tmp_path):
    archivo = tmp_path / "cargas.csv"
    archivo.write_text(
        "Ticket;Horas;Fecha;Comentario;Hora\nAAA-1;4h;2026-09-25;Script;\n\nBBB-2;3h30m;2026-09-28;;14:00\n",
        encoding="utf-8-sig",
    )
    cargas = leer_csv(archivo, "09:00", ZONA)
    assert [(c.ticket, c.segundos, c.inicio.hour, c.comentario) for c in cargas] == [
        ("AAA-1", 14400, 9, "Script"),
        ("BBB-2", 12600, 14, ""),
    ]


def test_csv_informa_todas_las_lineas_con_error(tmp_path):
    archivo = tmp_path / "cargas.csv"
    archivo.write_text("ticket,horas,fecha\nAAA-1,mucho,2026-09-25\nAAT-2,1h,ayer\nAAT-3,1h,2026-09-25\n")
    with pytest.raises(CargaError) as e:
        leer_csv(archivo, "09:00", ZONA)
    assert "línea 2" in str(e.value) and "línea 3" in str(e.value) and "línea 4" not in str(e.value)


def test_revisar_detecta_duplicados_y_tickets_inexistentes(jira, cliente):
    cargas = [
        armar_carga("AAA-1", "1h", "2026-09-25", "09:00", "", ZONA, "línea 2"),
        armar_carga("AAA-1", "1h", "2026-09-26", "09:00", "", ZONA, "línea 3"),
        armar_carga("NOEXISTE-1", "1h", "2026-09-25", "09:00", "", ZONA, "línea 4"),
    ]
    dup, sin_dup, inexistente = revisar(cliente, cargas, ZONA)
    assert dup.resumen == "Un ticket"
    assert dup.ya_cargado_segundos == 7200  # solo las mías, no las del otro usuario
    assert dup.avisos == ["ya tenés 2h en AAA-1 el 25/09"]
    assert sin_dup.avisos == []
    assert "404" in inexistente.error


def test_registrar_manda_fecha_pasada_y_comentario_adf(jira, cliente):
    c = armar_carga("AAA-1", "1h30m", "2026-09-25", "09:00", "Revisión de PRs", ZONA, "args")
    assert registrar(cliente, c) == "1"
    (creado,) = jira.creados
    assert creado["ruta"] == "/rest/api/3/issue/AAA-1/worklog"
    assert creado["params"] == {"adjustEstimate": "auto"}
    assert creado["json"]["started"] == "2026-09-25T09:00:00.000-0300"
    assert creado["json"]["timeSpentSeconds"] == 5400
    assert creado["json"]["comment"]["content"][0]["content"][0]["text"] == "Revisión de PRs"


def test_post_no_reintenta_ante_5xx(monkeypatch, cliente):
    llamadas = []
    monkeypatch.setattr(requests.Session, "request", lambda *a, **k: llamadas.append(1) or respuesta({}, status=502))
    c = armar_carga("AAA-1", "1h", "2026-09-25", "09:00", "", ZONA, "args")
    with pytest.raises(JiraError):
        registrar(cliente, c)
    assert len(llamadas) == 1


# --- Comando completo --------------------------------------------------------


@pytest.fixture
def entorno(monkeypatch):
    monkeypatch.setattr("horas_jira.config.load_dotenv", lambda: None)  # no leer el .env real
    for var, valor in {"JIRA_URL": URL, "JIRA_EMAIL": "yo@ejemplo.com", "JIRA_API_TOKEN": "t"}.items():
        monkeypatch.setenv(var, valor)


def correr(monkeypatch, *argv):
    monkeypatch.setattr("sys.argv", ["cargar-horas", *argv])
    return cli_carga.main()


def test_cli_dry_run_no_carga(jira, entorno, monkeypatch, capsys):
    assert correr(monkeypatch, "AAA-1", "1h", "2026-09-25", "--dry-run") == 0
    salida = capsys.readouterr().out
    assert "ya tenés 2h en AAA-1 el 25/09" in salida
    assert "vie 25/09: 1h" in salida
    assert jira.creados == []


def test_cli_sin_confirmacion_no_carga(jira, entorno, monkeypatch):
    monkeypatch.setattr("builtins.input", lambda _: "n")
    assert correr(monkeypatch, "AAA-1", "1h", "2026-09-25") == 1
    assert jira.creados == []


def test_cli_csv_carga_las_validas(jira, entorno, monkeypatch, tmp_path, capsys):
    archivo = tmp_path / "cargas.csv"
    archivo.write_text("ticket,horas,fecha\nAAA-1,4h,2026-09-25\nNOEXISTE-1,1h,2026-09-25\nBBB-2,2h,2026-09-28\n")
    assert correr(monkeypatch, "--archivo", str(archivo), "--si", "--mantener-estimacion") == 1  # una falló
    assert [c["ruta"] for c in jira.creados] == ["/rest/api/3/issue/AAA-1/worklog", "/rest/api/3/issue/BBB-2/worklog"]
    assert all(c["params"] == {"adjustEstimate": "leave"} for c in jira.creados)
    assert "Cargados: 2. Fallidos: 1." in capsys.readouterr().out
