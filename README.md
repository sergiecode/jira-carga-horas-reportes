# horas-jira

## 0. Configurar (una sola vez)

1. Crear un token en https://id.atlassian.com/manage-profile/security/api-tokens
2. Copiar `.env.example` como `.env`
3. Completar en `.env`: `JIRA_URL`, `JIRA_EMAIL` y `JIRA_API_TOKEN`
4. Opcional: elegir qué proyectos entran en el reporte con `JIRA_PROJECTS` (ver abajo)

Todo lo que se crea o se carga queda a nombre del dueño del token.

### Proyectos del reporte (`JIRA_PROJECTS`)

```env
JIRA_PROJECTS=
```
Vacío: el reporte trae **todos los proyectos que tu usuario puede ver**. Si tenés acceso a proyectos de otros equipos, sus horas también aparecen.

```env
JIRA_PROJECTS=AAA,BBB
```
Solo esos proyectos. Se usa la **clave**, que es el prefijo de los tickets: en `AAA-123` la clave es `AAA`.

```env
JIRA_PROJECTS=10001,10002
```
También se pueden poner los **IDs** numéricos. Para verlos, abrir en el navegador (con la sesión de Jira iniciada):

```
https://tu-sitio.atlassian.net/rest/api/3/project/search
```

Muestra cada proyecto con su `id`, su `key` (la clave) y su `name`.

---

## 1. Ver las horas de la última semana

```powershell
uv run horas-jira
```

Genera `horas_2026-09-25_a_2026-10-01.xlsx`:
- **Detalle**: un registro por fila.
- **Resumen**: horas por persona y día. En rojo, los días hábiles con menos del mínimo.

---

## 2. Cargar horas en un ticket que ya existe

```powershell
uv run cargar-horas AAA-123 1h 2026-09-25
```
Carga 1h en AAA-123 el 25/09.

```powershell
uv run cargar-horas AAA-123 58m 2026-09-25 -c "Revisión de PRs"
```
Carga 58m con comentario.

Varias cargas juntas: armar `cargas.csv` así

```csv
ticket,horas,fecha,comentario
AAA-123,4h,2026-09-25,Soporte
BBB-45,2h30m,2026-09-26,
```

```powershell
uv run cargar-horas --archivo cargas.csv --dry-run
uv run cargar-horas --archivo cargas.csv
```
El primero, con `--dry-run`, es una prueba: no carga nada, solo muestra lo que haría. El segundo, sin `--dry-run`, carga.

---

## 3. Crear tickets Task y cargarles horas desde Excel

**Paso 1.** Generar el Excel vacío:

```powershell
uv run crear-tareas --template
```

**Paso 2.** Abrir `tareas.xlsx` y completar una fila por ticket:

| PROYECTO | TITULO | DESCRIPCION | HORAS ESTIMADAS | HORAS CARGADAS | FECHA CARGA | TICKET |
|---|---|---|---|---|---|---|
| AAA | REUNION DE EQUIPO | Reunión diaria | 1h | 58m | 28/09/2026 | |
| BBB | DOCUMENTACION | Documentación del módulo | 2h | 117m | 28/09/2026 | |

Dejar TICKET vacío. Guardar y **cerrar** el Excel.

**Paso 3.** Probar con `--dry-run` (no crea ni carga nada, solo muestra lo que haría):

```powershell
uv run crear-tareas tareas.xlsx --dry-run
```

**Paso 4.** Crear y cargar (pide confirmación, responder `s`):

```powershell
uv run crear-tareas tareas.xlsx
```

Por cada fila crea un Task asignado a vos, le pone la estimación y carga las horas en la fecha indicada. El número de ticket queda escrito en la columna TICKET.

Si se corta a la mitad, se corre de nuevo el mismo comando: las filas que ya tienen TICKET no se vuelven a crear.

---

## Formatos

- Horas: `1h`, `58m`, `1h30m`, `2.5`
- Fecha en comandos: `2026-09-25`
- Hora del worklog: 09:00 por defecto; se cambia con `--hora 14:00`

---

## TAREAS DIARIAS

Los 3 comandos del día a día, en orden.

### 1. Crear el template

```powershell
uv run crear-tareas --template tareas_2026-10-02.xlsx
```

Crea un Excel vacío con las columnas listas. Conviene ponerle la fecha en el nombre, porque si el archivo ya existe el comando no lo pisa y da error.

Completar una fila por ticket:

| PROYECTO | TITULO | DESCRIPCION | HORAS ESTIMADAS | HORAS CARGADAS | FECHA CARGA | TICKET |
|---|---|---|---|---|---|---|
| AAA | REUNION DE EQUIPO | Reunión diaria | 1h | 58m | 02/10/2026 | |
| AAA | REVISION DE CODIGO | Revisión de pull requests | 1h | 58m | 02/10/2026 | |
| BBB | DOCUMENTACION | Documentación del módulo | 2h | 117m | 02/10/2026 | |

- PROYECTO: la clave del proyecto (AAA, BBB...).
- HORAS: `1h`, `58m`, `1h30m` o `2.5`.
- FECHA CARGA: el día en que se cargan las horas. Puede ser una fecha pasada.
- TICKET: dejarla vacía. La completa el script.

Guardar y **cerrar** el Excel antes del paso 2: si está abierto, el script no puede escribir los tickets.

### 2. Crear los tickets y cargar las horas

```powershell
uv run crear-tareas tareas_2026-10-02.xlsx --dry-run
```

Prueba sin efecto: **no crea tickets ni carga horas**. Solo consulta Jira para validar cada fila y muestra qué haría. Si una fila tiene un error (falta el título, la fecha es futura, el proyecto no existe), lo muestra acá.

Si está todo bien, correr el mismo comando **sin** `--dry-run`, que es el que crea y carga:

```powershell
uv run crear-tareas tareas_2026-10-02.xlsx
```

Pide confirmación (responder `s`) y por cada fila:
1. Crea un ticket Task en estado Open, asignado a vos.
2. Le pone la estimación (HORAS ESTIMADAS).
3. Le carga las horas (HORAS CARGADAS) en la FECHA CARGA.
4. Escribe el número de ticket en la columna TICKET.

Si se corta a la mitad, correr el mismo comando de nuevo: las filas que ya tienen TICKET no se vuelven a crear y solo se carga lo que faltó.

Para reusar el mismo Excel otro día: cambiar FECHA CARGA y **borrar la columna TICKET**. Si no se borra, no se crean tickets nuevos.

### 3. Ver el resumen semanal

```powershell
uv run horas-jira
```

Trae las horas de los últimos 7 días, con hoy incluido, y genera `horas_AAAA-MM-DD_a_AAAA-MM-DD.xlsx`.

- **Resumen**: una fila por persona y una columna por día.
  - Rojo: día hábil con menos horas que el mínimo.
  - Gris: fin de semana.
  - El día de hoy nunca se marca, porque puede estar incompleto.
  - El mínimo está en la celda amarilla y se puede cambiar ahí mismo.
- **Detalle**: un registro por fila, con link a cada ticket. Si corregís algo acá, el Resumen se recalcula solo.

Sirve para chequear que lo del paso 2 quedó cargado en el día correcto.
