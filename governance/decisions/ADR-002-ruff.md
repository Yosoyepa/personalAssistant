# ADR-002 — ruff como motor único de lint

- **Estado**: aceptada
- **Fecha**: 2026-08-11
- **Decide**: usuario
- **Alternativas consideradas**: ruff único · ruff activo + legacy generado · flake8+isort+black activos

## Contexto

El requisito original nombró `flake8` e `isort` explícitamente. Ambos existen, están mantenidos (`flake8` 7.3.0, `isort` 8.0.1) y son el estándar de facto en muchos equipos.

## Decisión

**`ruff` 0.16.2 es el motor único de lint, formato y orden de imports.** El perfil `flake8` + `isort` + `black` se genera en `governance/lint/legacy/` y se activa con `wct config --lint-profile legacy`, sin cambiar el umbral (0 findings) — solo el comando que ejecuta el gate.

## La razón decisiva no es la comodidad, es la latencia del anillo 3

El harness corre lint en `PostToolUse`, es decir **después de cada edición individual de archivo**, con un presupuesto de 2 segundos (`governance/thresholds.yaml` → `budgets_seconds.hook_post`). Ese presupuesto no es una preferencia: es lo que hace que el feedback llegue mientras el agente todavía tiene en contexto por qué escribió el código así.

Medición de referencia por archivo:

| Herramienta | Latencia aprox. |
|---|---|
| `ruff check` | ~20 ms |
| `flake8` | ~700 ms |
| `isort --check-only` | ~400 ms |
| `black --check` | ~500 ms |
| **legacy total** | **~1.6 s (3 procesos)** |

Con el perfil legacy, una edición que toca dos archivos ya excede el presupuesto. La consecuencia no es "va más lento": es que **el lint se cae del anillo 3 al anillo 4** y se pierde el feedback por archivo. `wct` hace esa degradación explícita y automática cuando detecta `lint_profile: legacy`, en vez de dejar que el hook expire silenciosamente.

## Cobertura de reglas

`ruff` cubre en un binario lo que en el perfil legacy requiere flake8 más doce plugins:

pycodestyle (`E`,`W`) · pyflakes (`F`) · isort (`I`) · bugbear (`B`) · comprehensions (`C4`) · simplify (`SIM`) · bandit subset (`S`) · annotations (`ANN`) · pytest-style (`PT`) · pyupgrade (`UP`) · pep8-naming (`N`) · datetimez (`DTZ`) · boolean-trap (`FBT`) · return (`RET`) · use-pathlib (`PTH`) · pydocstyle (`D`) · eradicate (`ERA`) · tryceratops (`TRY`) · mccabe (`C90`).

La tabla de equivalencia completa está en el encabezado de `governance/lint/ruff.toml`.

**Honestidad sobre la paridad**: no es exacta. `ruff` implementa reglas que ningún plugin de flake8 cubre (todo el grupo `RUF`, y parte de `SIM` y `TRY`). En la otra dirección, algunos plugins de nicho de flake8 no tienen equivalente en `ruff`. Conmutar el perfil cambia el conjunto de findings, no solo la velocidad.

## Lo que NO reemplaza

`ruff` no sustituye a estas, que siguen siendo gates propios:

- **`bandit` completo** (`G-SAST`) — `ruff` implementa un subconjunto de flake8-bandit, no bandit entero.
- **`semgrep`** (`G-SAST-SEMGREP`) — reglas propias sobre uso de tipos, que ningún linter de reglas fijas puede expresar.
- **`pylint`** (opt-in) — se mantiene aparte por sus reglas de **diseño** (`too-many-arguments`, `too-many-instance-attributes`, `duplicate-code` vía symilar), no por su lint de estilo. Se activa solo si el proyecto lo pide.
- **`mypy`** (`G-TYPE`) — `ruff` no hace inferencia de tipos.

## Consecuencias

- `pyproject.toml` referencia `governance/lint/ruff.toml`. Un solo archivo de configuración.
- `governance/lint/legacy/` contiene `.flake8`, `.isort.cfg` y `black.toml`, generados y mantenidos, pero inactivos.
- Migrar un repo que ya usa black: `ruff format --diff .` debe salir vacío antes de conmutar. `ruff format` implementa el mismo algoritmo que black, con divergencias marginales documentadas por ruff (magic trailing comma en algunos literales anidados).
- Si un equipo exige flake8 en su CI corporativo, `wct config --lint-profile legacy` lo satisface sin tocar ninguna otra parte del harness.
