"""Punto de entrada: `uv run crear-tareas`.

Ejemplos:
    uv run crear-tareas --template
    uv run crear-tareas tareas.xlsx --dry-run
    uv run crear-tareas tareas.xlsx
"""

import argparse
import sys
from datetime import time
from pathlib import Path

from .carga import CargaError, formatear_duracion
from .config import ConfigError, cargar_config
from .jira_client import JiraClient, JiraError
from .tareas import Plan, crear_template, ejecutar, leer_tareas, planificar


def main() -> int:
    args = _argumentos()

    if args.template:
        ruta = args.archivo or Path("tareas.xlsx")
        try:
            crear_template(ruta)
        except CargaError as e:
            print(f"Error: {e}", file=sys.stderr)
            return 1
        print(f"Template creado: {ruta.resolve()}")
        return 0

    try:
        hora = time.fromisoformat(args.hora)
    except ValueError:
        print(f"Error: hora inválida {args.hora!r}. Usá HH:MM.", file=sys.stderr)
        return 1

    try:
        config = cargar_config()
        tareas = leer_tareas(args.archivo, config.zona_horaria)
        cliente = JiraClient(config.jira_url, config.jira_email, config.jira_api_token)
        print(f"Revisando {len(tareas)} fila(s) en Jira como {config.jira_email}...")
        planes, yo = planificar(cliente, tareas, config.zona_horaria)
    except (ConfigError, CargaError, JiraError) as e:
        print(f"\nError: {e}", file=sys.stderr)
        return 1

    _vista_previa(planes)
    a_ejecutar = [p for p in planes if not p.error and (p.crear or p.cargar)]
    con_error = sum(1 for p in planes if p.error)

    if args.dry_run:
        print("\nModo --dry-run: no se creó ni cargó nada.")
        return 1 if con_error else 0
    if not a_ejecutar:
        print("\nNo hay nada para hacer.")
        return 1 if con_error else 0
    if not _se_puede_escribir(args.archivo):
        print(f"\nError: no se puede escribir {args.archivo}. ¿Está abierto en Excel? Cerralo y reintentá.",
              file=sys.stderr)
        return 1
    if not args.si and not _confirmar(f"\n¿Procesar {len(a_ejecutar)} fila(s)? (s/n): "):
        print("Cancelado, no se hizo nada.")
        return 1

    print()
    fallidas = ejecutar(cliente, a_ejecutar, args.archivo, yo, config.zona_horaria, hora,
                        ajustar_estimacion=not args.mantener_estimacion)
    print(f"\nOK: {len(a_ejecutar) - fallidas}. Fallidas: {fallidas + con_error}.")
    if fallidas:
        print("Las filas que llegaron a crear su ticket ya lo tienen en la columna TICKET: "
              "si volvés a correrlo, solo se reintenta lo que faltó.")
    return 0 if fallidas + con_error == 0 else 1


def _argumentos() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        prog="crear-tareas",
        description="Crea tickets Task desde un Excel y les carga horas. "
        "Los tickets y las horas quedan a nombre del dueño del token del .env.",
    )
    p.add_argument("archivo", nargs="?", type=Path, help="Excel con la hoja Tareas")
    p.add_argument("--template", action="store_true", help="crea un Excel vacío para completar (por defecto tareas.xlsx)")
    p.add_argument("--hora", default="09:00", help="hora de inicio de los worklogs (por defecto 09:00)")
    p.add_argument("--dry-run", action="store_true", help="valida y muestra lo que haría, sin crear nada")
    p.add_argument("--si", action="store_true", help="no pedir confirmación")
    p.add_argument("--mantener-estimacion", action="store_true",
                   help="no descontar de la estimación restante las horas cargadas")
    args = p.parse_args()
    if not args.template and not args.archivo:
        p.error("falta el Excel: crear-tareas tareas.xlsx  (o crear-tareas --template para generar uno)")
    return args


def _vista_previa(planes: list[Plan]) -> None:
    print(f"\n{'Fila':<6}{'Proy.':<8}{'Estim.':<9}{'Carga':<16}{'Título':<42}Acción")
    print("-" * 110)
    for p in planes:
        t = p.tarea
        estimado = formatear_duracion(t.estimado_segundos) if t.estimado_segundos else "-"
        carga = f"{formatear_duracion(t.cargado_segundos)} {t.fecha_carga:%d/%m}" if t.cargado_segundos else "-"
        titulo = t.titulo[:40] or t.ticket
        print(f"{t.fila:<6}{t.proyecto:<8}{estimado:<9}{carga:<16}{titulo:<42}{p.accion()}")


def _se_puede_escribir(ruta: Path) -> bool:
    """En Windows, si Excel tiene el archivo abierto no se puede abrir para escritura."""
    try:
        with open(ruta, "r+b"):
            return True
    except PermissionError:
        return False


def _confirmar(pregunta: str) -> bool:
    try:
        return input(pregunta).strip().lower() in ("s", "si", "sí", "y")
    except EOFError:
        return False


if __name__ == "__main__":
    sys.exit(main())
