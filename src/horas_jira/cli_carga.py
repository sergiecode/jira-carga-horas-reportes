"""Punto de entrada: `uv run cargar-horas`.

Ejemplos:
    uv run cargar-horas AAA-123 1h30m 2026-09-25 -c "Revisión de PRs"
    uv run cargar-horas --archivo cargas.csv --dry-run
"""

import argparse
import sys
from collections import defaultdict
from pathlib import Path

from .carga import CargaError, Revision, armar_carga, formatear_duracion, leer_csv, registrar, revisar
from .config import ConfigError, cargar_config
from .excel import DIAS_SEMANA
from .jira_client import JiraClient, JiraError


def main() -> int:
    args = _argumentos()
    try:
        config = cargar_config()
        if args.archivo:
            cargas = leer_csv(args.archivo, args.hora, config.zona_horaria)
        else:
            cargas = [
                armar_carga(args.ticket, args.duracion, args.fecha, args.hora, args.comentario,
                            config.zona_horaria, origen="argumentos")
            ]

        cliente = JiraClient(config.jira_url, config.jira_email, config.jira_api_token)
        print(f"Revisando {len(cargas)} carga(s) en Jira como {config.jira_email}...")
        revisiones = revisar(cliente, cargas, config.zona_horaria)
    except (ConfigError, CargaError, JiraError) as e:
        print(f"\nError: {e}", file=sys.stderr)
        return 1

    _vista_previa(revisiones)
    validas = [r for r in revisiones if not r.error]

    if args.dry_run:
        print("\nModo --dry-run: no se cargó nada.")
        return 0 if len(validas) == len(revisiones) else 1
    if not validas:
        print("\nNo hay nada válido para cargar.")
        return 1
    if not args.si and not _confirmar(f"\n¿Cargar {len(validas)} registro(s)? (s/n): "):
        print("Cancelado, no se cargó nada.")
        return 1

    fallidas = 0
    for r in validas:
        c = r.carga
        try:
            worklog_id = registrar(cliente, c, ajustar_estimacion=not args.mantener_estimacion)
            print(f"  OK     {c.fecha:%d/%m} {c.ticket} {formatear_duracion(c.segundos)} (worklog {worklog_id})")
        except JiraError as e:
            fallidas += 1
            print(f"  ERROR  {c.fecha:%d/%m} {c.ticket} ({c.origen}): {e}")

    no_validas = len(revisiones) - len(validas)
    print(f"\nCargados: {len(validas) - fallidas}. Fallidos: {fallidas + no_validas}.")
    return 0 if fallidas + no_validas == 0 else 1


def _argumentos() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        prog="cargar-horas",
        description="Carga horas en tickets de Jira, también en fechas pasadas. "
        "Los worklogs quedan a nombre del dueño del token del .env.",
    )
    p.add_argument("ticket", nargs="?", help="clave del ticket, ej. AAA-123")
    p.add_argument("duracion", nargs="?", help="ej. 1h, 30m, 1h30m o 2.5 (horas)")
    p.add_argument("fecha", nargs="?", help="AAAA-MM-DD")
    p.add_argument("-c", "--comentario", default="", help="comentario del worklog")
    p.add_argument("-a", "--archivo", type=Path, help="CSV con columnas ticket,horas,fecha[,comentario][,hora]")
    p.add_argument("--hora", default="09:00", help="hora de inicio del worklog (por defecto 09:00)")
    p.add_argument("--dry-run", action="store_true", help="valida y muestra lo que haría, sin cargar nada")
    p.add_argument("--si", action="store_true", help="no pedir confirmación")
    p.add_argument("--mantener-estimacion", action="store_true",
                   help="no descontar la estimación restante del ticket")
    args = p.parse_args()

    sueltos = [args.ticket, args.duracion, args.fecha]
    if args.archivo and any(sueltos):
        p.error("usá --archivo o ticket/duración/fecha, no las dos cosas")
    if not args.archivo and not all(sueltos):
        p.error("faltan argumentos: cargar-horas TICKET DURACION FECHA  (o --archivo cargas.csv)")
    return args


def _vista_previa(revisiones: list[Revision]) -> None:
    print(f"\n{'Fecha':<11}{'Hora':<7}{'Ticket':<14}{'Duración':<10}Resumen / comentario")
    print("-" * 90)
    por_dia: dict = defaultdict(int)
    for r in sorted(revisiones, key=lambda r: (r.carga.inicio, r.carga.ticket)):
        c = r.carga
        detalle = f"ERROR: {r.error}" if r.error else r.resumen[:45]
        if c.comentario and not r.error:
            detalle += f"  «{c.comentario[:30]}»"
        print(f"{c.fecha:%d/%m/%Y} {c.inicio:%H:%M}  {c.ticket:<14}{formatear_duracion(c.segundos):<10}{detalle}")
        if not r.error:
            por_dia[c.fecha] += c.segundos

    if por_dia:
        print("\nTotal a cargar por día:")
        for dia, segundos in sorted(por_dia.items()):
            print(f"  {DIAS_SEMANA[dia.weekday()]} {dia:%d/%m}: {formatear_duracion(segundos)}")

    avisos = [a for r in revisiones for a in r.avisos]
    if avisos:
        print("\nAtención, posibles duplicados:")
        for a in avisos:
            print(f"  - {a}")


def _confirmar(pregunta: str) -> bool:
    try:
        return input(pregunta).strip().lower() in ("s", "si", "sí", "y")
    except EOFError:
        return False


if __name__ == "__main__":
    sys.exit(main())
