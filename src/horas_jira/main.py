"""Punto de entrada: `uv run horas-jira`."""

import sys

from .config import ConfigError, cargar_config
from .excel import generar_excel
from .jira_client import JiraClient, JiraError
from .periodo import hoy_en, ultimos_dias
from .registros import obtener_registros


def main() -> int:
    try:
        config = cargar_config()
        hoy = hoy_en(config.zona_horaria)
        periodo = ultimos_dias(config.dias, config.zona_horaria, hoy)
        print(f"Período: {periodo.desde:%d/%m/%Y} al {periodo.hasta:%d/%m/%Y} ({config.zona_horaria.key})")
        print(f"Proyectos: {', '.join(config.proyectos) if config.proyectos else 'todos los que podés ver'}")

        cliente = JiraClient(config.jira_url, config.jira_email, config.jira_api_token)
        registros = obtener_registros(cliente, config.proyectos, periodo, config.zona_horaria)

        print("Generando Excel...")
        ruta = generar_excel(
            registros,
            periodo,
            carpeta_salida=config.carpeta_salida,
            jira_url=config.jira_url,
            horas_minimas_dia=config.horas_minimas_dia,
            equipo=config.equipo,
            hoy=hoy,
        )
    except (ConfigError, JiraError) as e:
        print(f"\nError: {e}", file=sys.stderr)
        return 1
    except PermissionError as e:
        print(f"\nError: no se pudo escribir {e.filename}. ¿Está abierto en Excel? Cerralo y reintentá.", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("\nCancelado.", file=sys.stderr)
        return 130

    personas = {r.persona for r in registros}
    sin_carga = [p for p in config.equipo if p not in personas]
    print("\nListo.")
    print(f"  Archivo:            {ruta.resolve()}")
    print(f"  Registros:          {len(registros)}")
    print(f"  Horas totales:      {sum(r.horas for r in registros):.2f}")
    print(f"  Personas con carga: {len(personas)}")
    if sin_carga:
        print(f"  Sin carga:          {', '.join(sin_carga)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
