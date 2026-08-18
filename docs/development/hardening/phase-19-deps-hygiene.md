# Phase 19 — Dependency hygiene (supersedes Dependabot #50–#54)

| Campo | Valor |
|---|---|
| Fase | `19 — dependency hygiene` |
| Estado | `COMPLETED` |
| Mantenedor | `Yosoyepa` |
| Rama de fase | `yosoyepa/phase-19-deps-hygiene` (coder; bless del mantenedor en la misma rama) |
| Fecha de inicio | `2026-08-18` |
| PR | `#60` (supersede #50–#54) |
| Merge commit | `0796739` |

## Objetivo

Consolidar en una sola rama los 5 bumps de Dependabot (#50 ruff, #51
diff-cover, #52 import-linter, #53 coverage, #54 pyyaml), que individualmente
quedaban imposibles de mergear: tocaban `pyproject.toml` sin regenerar
`uv.lock`, así que `uv lock --check` de CI los rechazaba. Las 5 son
dependencias de desarrollo; cero cambio de superficie de runtime.

## Ejecución (coder)

- Commit `2b1ee26`: toca únicamente `pyproject.toml` y `uv.lock`.
- Bumps aplicados (requisito y versión real en el entorno, verificadas por el
  discriminador): ruff `>=0.16.3` (0.16.3), diff-cover `>=10.5.0` (10.5.0),
  import-linter `>=2.13` (2.13), coverage `>=7.15.4` (7.15.4), PyYAML
  `>=6.0.3` (6.0.3).
- Cero findings nuevos de ruff 0.16.3 y de import-linter 2.13 (salto de 10
  minors) sobre el código existente.
- El coder respetó la regla dura: no corrió `integrity bless`, no tocó
  `governance/**`, y reportó el FAIL esperado de G-META-1 con su causa
  exacta (`modificado: pyproject.toml`).

## Ejecución (discriminador + mantenedor)

- Verificación reproducida, no solo leída: `uv lock --check` OK, suite
  1015 passed / 3 skipped / 396 subtests (idéntica a `main`), gate commit
  16/17 con único FAIL esperado en G-META-1.
- Bless del mantenedor en su terminal: commit `20e9c42`, entrada en
  `governance/integrity-log.md` (reason "phase 19 dev dependency bumps
  (supersedes dependabot #50-#54)").
- Gate commit post-bless reproducido: **17/17**.
- `update-branch` (main había avanzado con #59), CI 5/5 sobre el head nuevo,
  merge `0796739`. Dependabot #50–#54 cerradas con comentario de superseded.

## Feedback WCT de la fase

1. **G-META-1 no tiene enforcement en CI.** Ningún workflow corre `wct gate`
   ni `wct integrity verify`: una PR que toque rutas protegidas sin bless pasa
   CI verde. La barrera real hoy es el hook local más la revisión del
   discriminador. Propuesta para el template: documentar un paso de CI
   recomendado (`python -m tools.wct integrity verify`, o el tier commit
   completo) para que la integridad de rutas protegidas tenga diente remoto.
2. **Regla del worktree compartido (nueva, lado verificador).** Mientras el
   coder trabaja en el checkout compartido, el verificador no debe correr
   operaciones git que cambien de rama — incluidos merges con `--watch` en
   background. En esta fase una tarea en background cambió el checkout a
   `main` a mitad de la corrida del coder; el trabajo sobrevivió porque los
   cambios sin commitear viajan entre ramas, pero pudo perderse. Regla:
   merges/watch en background solo antes de lanzar al coder o después de su
   PR.
3. **`gh pr checks --watch` devuelve corridas obsoletas tras
   `update-branch`.** Justo después de actualizar la rama, `--watch` reporta
   como verdes las corridas del SHA anterior. Hay que confirmar que las
   corridas corresponden al head nuevo (comparar run IDs) antes de mergear.
4. **Las reglas duras del prompt se obedecen sin vigilancia.** El coder no
   bendijo, no tocó governance y reportó el fallo esperado con su causa. El
   oráculo (suite + gate + `uv lock --check`) bastó para validar sin revisión
   línea por línea: el diff era exactamente los 5 bumps.
5. **Consolidar PRs de Dependabot funciona como patrón.** Una sola rama +
   `uv lock` + bless del mantenedor resolvió 5 PRs atascadas con coste total
   de una PR. Candidato a procedimiento estándar del template para higiene de
   dependencias.
