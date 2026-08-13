# Phase 13 — Piloto de adopción del Well Code Template (WCT)

| Campo | Valor |
|---|---|
| Fase | `13 — adopción piloto WCT` |
| Estado | `IN_PROGRESS` |
| Mantenedor | `Yosoyepa` |
| Rama de fase | `kimi/phase-13-wct-pilot` |
| Fecha de inicio | `2026-08-13` |
| PR | `<pendiente>` |
| Merge commit | `<pendiente>` |

## Objetivo

Adoptar el harness WCT (`/home/jandradeu/Documents/well_code_template`) en modo
piloto acotado: gates ejecutables en tiers `fast`/`commit`, separación
autor/verificador, y baselines medidos sobre el código real — sin big-bang y
sin tocar el tier `full` (mutation testing completo queda para releases).

Motivación: permitir delegación a subagentes más baratos sin pérdida de
calidad, donde la ley son los exit codes de los gates y no la revisión humana
línea por línea.

## Qué se copió del template

- `tools/wct/` (CLI completo: gate, rules, doctor, integrity, archmetrics,
  dry, introvert, mutate, ratchet, accept, selftest, webhook).
- `governance/`: `rules/`, `lint/`, `semgrep/`, `decisions/` (ADR-001..003 del
  harness), `adapters/`, `policy.yaml`, `thresholds.yaml`.
- `quality/redteam/` (corpus adversarial para `wct selftest redteam`).
- Skills `wct-*` a `.agents/skills/` (convención de este repo).
- `.claude/agents/` (roles specifier/coder/cleaner/architect/hardener/
  verifier; el verifier no tiene permisos de escritura) y
  `.claude/settings.json` con hooks cableados a `wct hook`.

No se copiaron: los tests del propio harness (colisionarían con
`testpaths=["tests"]`), los valores de baselines del template (se midieron los
reales), ni `src/example`.

## Adaptaciones al proyecto (con justificación)

1. **Capas** (`governance/policy.yaml`): mapeo desde ADR-003 —
   `evals → infrastructure → adapters → contracts → application → domain`.
   `pydantic` NO está en `forbidden_external` de domain porque ADR-003 permite
   modelos Pydantic en dominio cuando la validación es parte del contrato.
2. **Proveedores de reglas**: solo `agents-md` (AGENTS.md generado, el archivo
   que lee Kimi Code). El resto de proveedores se pueden regenerar después.
3. **`runner.py`** (copia local del harness):
   - G-LINT/G-FMT usan la config ruff del proyecto (pyproject, triage fase 10)
     en vez de `governance/lint/ruff.toml`.
   - G-TYPE = `mypy src`; G-TEST = `pytest -q` (testpaths del proyecto).
   - G-DEPS = `deptry src --known-first-party personal_assistant`.
   - G-DEAD incluye `tools/vulture_whitelist.py` (parámetro formal
     `exc_traceback` de `__exit__`).
   - G-SAST-BANDIT lee `-c pyproject.toml` (`[tool.bandit]`).
   - G-SECRET: rutas reales del repo, soporte de `--baseline
     .secrets.baseline`, y parseo tolerante a `--slim` (sin `line_number`;
     stdout vacío = sin hallazgos). Bug upstream: el template asumía
     `line_number` presente con `--slim`.
   - G-MUT-SITES diferencial: implementa TEST-007 tal como está escrito
     ("archivo CAMBIADO con >100 sitios") usando el manifiesto
     `governance/generated/mutation-manifest.json`; un archivo legacy sobre el
     límite solo bloquea si el diff tocó sus funciones.
   - `archmetrics`: los imports bajo `if TYPE_CHECKING:` no cuentan como
     aristas (se borran en runtime; contarlos convierte shims anticycle en
     falsos positivos). Se añadió `architecture.cycle_allowlist` en
     `thresholds.yaml` para excepciones documentadas.
4. **G-FMT desactivado** en `policy.yaml` (visible como NO CUBIERTO en
   `wct report`): el proyecto no exige `ruff format` (ruff 0.16.2
   reformatearía 56 archivos); esa decisión corresponde a una fase de estilo.
5. **pyproject.toml**: grupo `quality` (PyYAML, bandit, deptry,
   detect-secrets, import-linter, pytest-cov, vulture);
   `[tool.deptry.per_rule_ignores]` y `[tool.bandit] skips` documentados como
   réplica 1:1 del triage S* de fase 10.
6. **`.importlinter`**: 5 contratos derivados de ADR-003, con
   `ignore_imports` documentado para la arista diferida worker→bootstrap.

## Hallazgos reales que el piloto destapó (y su resolución)

- **Ciclo `domain.common.identity ↔ domain.common.permissions`**: shim de
  backward-compat (`__getattr__` para `Principal`) sin ningún consumidor en
  código, tests ni contratos. Eliminado (fix real, no excepción).
- **Ciclo `infrastructure.migrations ↔ infrastructure.config`**:
  `validate_identifier` vivía bajo `migrations/` y `config.py` lo importaba.
  Movido a `infrastructure/validation.py`; `migrations/validation.py` queda
  como re-export compatible.
- **Ciclo `infrastructure.worker ↔ infrastructure.bootstrap`**: wiring
  diferido intencional del CLI. Documentado como excepción en
  `thresholds.yaml → cycle_allowlist` + `.importlinter → ignore_imports`.
- **49 supresiones legacy** contadas; 47 sin justificación inline. Todas
  justificadas con el formato `# type: ignore[code]  # reason: ...`
  (validado contra mypy: el texto directo tras el bracket lo rechaza).
- **`tools/wct/selftest/redteam.py`**: variable `SECRET` (regex detectora de
  secretos) disparaba el escáner propio del proyecto
  (`test_public_artifacts.py`); renombrada a `SECRET_PATTERN`.
- **Códigos muertos**: parámetro `traceback` sin uso en 5 `__exit__`
  (renombrado a `exc_traceback` + whitelist vulture) y parámetro `call` del
  Protocol `ToolPort.execute` sin implementadores ni llamadas por keyword
  (renombrado a `tool_call`).
- **Falsos positivos de secretos**: 5 fixtures documentados (CI Postgres,
  placeholders de eval) + 189 hashes hex de cassettes/requests
  (`request_key`, SHA-256 del request LLM) auditados en `.secrets.baseline`
  con `is_secret: false`; gitleaks ya tenía su propia allowlist acotada
  (`.gitleaks.toml`, fase 12).

## Baselines medidos (no inventados)

| Métrica | Valor inicial | Dirección |
|---|---:|---|
| suppressions | 49 | lower_is_better (ratchet) |
| debt-markers | 0 | lower_is_better |
| introverted-tests | 37 | ratchet (heurístico) |
| archmetrics-zones | 3 | ratchet |

## Verificación

- `wct doctor`: 11/11 PASS.
- `wct gate --tier fast`: 7/7 (G-FMT SKIP documentado).
- `wct gate --tier commit` con `TEST_POSTGRES_DSN` contra PostgreSQL 16
  (contenedor podman `personal-assistant-pg16-phase13`, `postgres:16-alpine`):
  **17/17 PASS** (G-FMT SKIP documentado por `gates.disabled`; G-TEST corre la
  suite completa en ~87 s).
- `wct ratchet check`: todos los ratchets se mantienen (baselines intactos).
- Regresiones propias: `ruff check`, `mypy src` (128 archivos),
  `uv lock --check`, suite completa pytest.

## Decisiones pendientes para fases siguientes

- Tier `full` (mutation testing real, CRAP, semgrep, SBOM): herramientas no
  instaladas a propósito en el piloto; gates opcionales reportan SKIPPED.
- Split de los archivos legacy sobre 100 sitios de mutación (10+ archivos,
  incluido `postgres.py`): hoy cubiertos por el gate diferencial; cada uno se
  partirá cuando se toque.
- Adopción de `ruff format` como gate (56 archivos de drift): fase de estilo.
- Roles de subagente por fase (specifier→coder→verifier) en trabajo real.
- Evaluar si `prompts/`, `replies/`, `eval/` entran en `paths.protected`.
