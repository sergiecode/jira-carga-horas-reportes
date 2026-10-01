"""Carga y validación de la configuración desde el archivo .env."""

import os
from dataclasses import dataclass
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from dotenv import load_dotenv

class ConfigError(Exception):
    """La configuración del .env es inválida o está incompleta."""


@dataclass(frozen=True)
class Config:
    jira_url: str
    jira_email: str
    jira_api_token: str
    proyectos: list[str]
    dias: int
    horas_minimas_dia: float
    zona_horaria: ZoneInfo
    equipo: list[str]
    carpeta_salida: Path


def cargar_config() -> Config:
    """Lee el .env (si existe) y las variables de entorno, y devuelve la configuración."""
    load_dotenv()
    # Se acepta JIRA_BASE_URL como alias de JIRA_URL.
    if not os.getenv("JIRA_URL", "").strip() and os.getenv("JIRA_BASE_URL", "").strip():
        os.environ["JIRA_URL"] = os.environ["JIRA_BASE_URL"]

    faltantes = [v for v in ("JIRA_URL", "JIRA_EMAIL", "JIRA_API_TOKEN") if not os.getenv(v, "").strip()]
    if faltantes:
        raise ConfigError(
            f"Faltan variables obligatorias en el .env: {', '.join(faltantes)}. "
            "Copiá .env.example como .env y completalo."
        )

    proyectos = _lista(os.getenv("JIRA_PROJECTS", ""))  # vacía = todos los proyectos visibles

    dias = _entero("DIAS", 7)
    if dias < 1:
        raise ConfigError("DIAS tiene que ser al menos 1.")

    nombre_zona = os.getenv("ZONA_HORARIA") or "America/Argentina/Buenos_Aires"
    try:
        zona = ZoneInfo(nombre_zona)
    except ZoneInfoNotFoundError:
        raise ConfigError(f"ZONA_HORARIA inválida: {nombre_zona!r}") from None

    return Config(
        jira_url=os.environ["JIRA_URL"].strip().rstrip("/"),
        jira_email=os.environ["JIRA_EMAIL"].strip(),
        jira_api_token=os.environ["JIRA_API_TOKEN"].strip(),
        proyectos=proyectos,
        dias=dias,
        horas_minimas_dia=_decimal("HORAS_MINIMAS_DIA", 8),
        zona_horaria=zona,
        equipo=_lista(os.getenv("EQUIPO", "")),
        carpeta_salida=Path(os.getenv("CARPETA_SALIDA") or "."),
    )


def _lista(valor: str) -> list[str]:
    return [item.strip() for item in valor.split(",") if item.strip()]


def _entero(nombre: str, por_defecto: int) -> int:
    valor = os.getenv(nombre, "").strip()
    if not valor:
        return por_defecto
    try:
        return int(valor)
    except ValueError:
        raise ConfigError(f"{nombre} tiene que ser un número entero (vino {valor!r}).") from None


def _decimal(nombre: str, por_defecto: float) -> float:
    valor = os.getenv(nombre, "").strip()
    if not valor:
        return por_defecto
    try:
        return float(valor.replace(",", "."))
    except ValueError:
        raise ConfigError(f"{nombre} tiene que ser un número (vino {valor!r}).") from None
