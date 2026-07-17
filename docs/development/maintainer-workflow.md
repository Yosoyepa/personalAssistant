# Flujo de desarrollo para un mantenedor único

Esta guía define el flujo canónico para cambiar el repositorio con ramas
`codex/`, worktrees aislados, revisión explícita y una sola pull request por
fase. Está pensada para un mantenedor que puede ejecutar trabajo en paralelo,
pero conserva una única autoridad de integración.

Los cambios de una fase se registran con la plantilla
[`hardening-log.md`](hardening-log.md). La plantilla guarda evidencia; esta guía
guarda las reglas y no debe copiarse dentro de cada log.

## Invariantes

- `main` siempre representa la última base integrada y verificable.
- Todo cambio vive en una rama con prefijo `codex/`; no se trabaja directamente
  sobre `main`.
- Cada tarea paralela usa su propio worktree y su propia rama.
- Cada fase reserva cinco roles en dos olas: tres roles independientes y,
  después de un checkpoint, dos roles de integración o hardening (`3 + 2`). Un
  rol puede terminar como `REVIEW_ONLY`; en ese caso entrega evidencia y no crea
  un commit vacío.
- El mantenedor revisa el diff completo antes de autorizar staging y vuelve a
  revisar exclusivamente el diff staged antes del commit.
- El staging enumera rutas o hunks. Están prohibidos `git add .`, `git add -A` y
  `git commit -am`.
- Los commits siguen Conventional Commits.
- Las ramas de trabajo se integran en la rama de fase. La fase produce una sola
  PR hacia `main` y esa PR se integra con merge commit, nunca con squash o
  rebase.
- Secretos y datos reales no entran al índice, al historial, a logs, trazas,
  fixtures, capturas ni documentación.
- No se usa `git reset --hard` como mecanismo de rollback. Los cambios
  compartidos se revierten con commits explícitos.
- El mismo bloqueo no se reintenta indefinidamente: después de tres ciclos
  consecutivos se declara `BLOCKED` y se solicita una decisión.

## 1. Abrir una fase

Ejecutar desde el checkout principal, con un árbol limpio. Sustituir el número y
el slug por los de la fase real.

```powershell
git status --short --branch
git fetch --prune
git switch main
git pull --ff-only

$Phase = "p0"
$PhaseBranch = "codex/phase-0-governance"
git switch -c $PhaseBranch
git status --short --branch
```

Registrar en el hardening log el commit base:

```powershell
git rev-parse HEAD
git show -s --format="%h %cI %s" HEAD
```

No continuar si el checkout principal contiene cambios ajenos, si `main` no
puede actualizarse con fast-forward o si la base registrada no coincide con la
base aprobada para la fase.

## 2. Crear worktrees y ejecutar las olas 3 + 2

Los nombres siguen `codex/<fase>-a<n>-<slug>` y los directorios viven al lado
del repositorio principal. Este ejemplo presupone que se ejecuta desde el
checkout principal.

```powershell
$Repo = git rev-parse --show-toplevel
$WorktreeRoot = "$Repo-worktrees"
New-Item -ItemType Directory -Force -Path $WorktreeRoot | Out-Null

git worktree add -b "codex/$Phase-a1-toolchain" `
  (Join-Path $WorktreeRoot "$Phase-a1-toolchain") $PhaseBranch
git worktree add -b "codex/$Phase-a2-governance" `
  (Join-Path $WorktreeRoot "$Phase-a2-governance") $PhaseBranch
git worktree add -b "codex/$Phase-a3-characterization" `
  (Join-Path $WorktreeRoot "$Phase-a3-characterization") $PhaseBranch

git worktree list
```

### Ola 1: tres roles independientes

Antes de iniciar cada tarea, registrar en el log:

- objetivo y criterio de aceptación;
- modo `IMPLEMENTATION` o `REVIEW_ONLY`;
- rama y ruta del worktree;
- rutas que puede modificar;
- rutas que comparte con otra tarea;
- gates enfocados que debe ejecutar;
- rollback previsto.

Dos tareas no deben escribir simultáneamente el mismo archivo. Si el solapamiento
es inevitable, una tarea es dueña del archivo y la otra entrega notas o un diff
propuesto sin escribirlo.

Cada worktree debe confirmar su aislamiento:

```powershell
git branch --show-current
git status --short --branch
git rev-parse HEAD
```

### Checkpoint entre olas

La ola 2 no comienza hasta que los tres roles de ola 1 hayan entregado diff o
evidencia de revisión, validaciones y riesgos, y el mantenedor haya:

1. revisado cada diff;
2. integrado los commits aceptados y registrado los roles `REVIEW_ONLY` sin
   crear commits vacíos;
3. devuelto cualquier conflicto al agente afectado para actualizar su rama,
   resolución, revalidación y nueva revisión;
4. ejecutado los gates de fase;
5. actualizado el hardening log.

Ejemplo de integración desde el checkout principal:

```powershell
git switch $PhaseBranch
git merge --no-ff "codex/$Phase-a1-toolchain" `
  -m "chore($Phase): integrate toolchain hardening"
git merge --no-ff "codex/$Phase-a2-governance" `
  -m "chore($Phase): integrate repository governance"
git merge --no-ff "codex/$Phase-a3-characterization" `
  -m "test($Phase): integrate characterization coverage"
```

Si un merge encuentra conflictos, el orquestador no los resuelve silenciosamente
en la rama de fase. Aborta el merge y el agente afectado integra la rama de fase
actualizada en su propia rama:

```powershell
$AffectedBranch = "codex/$Phase-a2-governance"
$AffectedWorktree = Join-Path $WorktreeRoot "$Phase-a2-governance"

git merge --abort
git -C $AffectedWorktree switch $AffectedBranch
git -C $AffectedWorktree merge $PhaseBranch
```

El agente resuelve allí las rutas en conflicto, hace staging explícito, revalida
sus gates, crea un commit Conventional Commit y vuelve a entregar el diff para
revisión. Solo entonces el orquestador reintenta el merge en la rama de fase.

Si un rol termina como `REVIEW_ONLY` o su propuesta no se aprueba, se registra su
evidencia y se omite el merge; nunca se crea un commit vacío para conservar la
numeración.

### Ola 2: dos roles dependientes

Las ramas de ola 2 nacen del HEAD actualizado de la rama de fase, no de la base
original:

```powershell
git switch $PhaseBranch
git status --short --branch

git worktree add -b "codex/$Phase-a4-integration" `
  (Join-Path $WorktreeRoot "$Phase-a4-integration") $PhaseBranch
git worktree add -b "codex/$Phase-a5-hardening" `
  (Join-Path $WorktreeRoot "$Phase-a5-hardening") $PhaseBranch
```

Los dos roles de ola 2 se reservan para integración, regresiones, documentación
final, observabilidad, seguridad o correcciones reveladas por la ola 1. También
pueden cerrar como `REVIEW_ONLY`, sin commit vacío. La ola no amplía el alcance
funcional de la fase sin una aprobación nueva.

## 3. Ciclo obligatorio de revisión antes del commit

El orden es obligatorio. Un resultado inesperado detiene el ciclo.

### 3.1 Revisar el árbol de trabajo

```powershell
git status --short
git diff --stat
git diff --check
git diff --
```

Para archivos nuevos todavía no rastreados, abrirlos directamente y confirmar su
contenido; `git diff` no los muestra hasta el staging. Comprobar además:

- que todas las rutas pertenecen al alcance autorizado;
- que no se borró o renombró contenido incidentalmente;
- que el diff no contiene debug temporal, datos personales o secretos;
- que comentarios y documentación describen el comportamiento real;
- que cada cambio de comportamiento tiene prueba o justificación registrada.

### 3.2 Ejecutar gates enfocados

Ejecutar primero la prueba más cercana al cambio. Ejemplos:

```powershell
$env:APP_ENV_FILE = "disabled"
uv sync --frozen --all-extras --group dev
uv run pytest -q tests/test_architecture_boundaries.py
uv run pytest -q tests/test_command_router.py
```

Solo se ejecutan los archivos relevantes; el hardening log registra el comando
exacto y el código de salida. Después, antes de abrir la PR, se ejecuta el gate
completo de la sección 8.

### 3.3 Staging explícito

Enumerar cada ruta aceptada:

```powershell
git add -- README.md `
  docs/development/maintainer-workflow.md `
  docs/development/hardening-log.md
```

Para seleccionar hunks dentro de una ruta:

```powershell
git add -p -- path/to/file.py
```

Para una eliminación intencional:

```powershell
git rm -- path/to/obsolete-file.py
```

Nunca ejecutar `git add .`, `git add -A` ni `git commit -am`. Si una ruta no fue
revisada, no se incluye en el índice.

### 3.4 Revisar exactamente lo que se va a confirmar

```powershell
git status --short
git diff --cached --stat
git diff --cached --check
git diff --cached --
```

La revisión staged debe confirmar que:

- no quedó un cambio requerido fuera del índice;
- no entró una ruta ajena por accidente;
- el commit representa una sola unidad reversible;
- la evidencia de tests y rollback corresponde a ese contenido exacto.

Si hay un error, retirar únicamente la ruta afectada y repetir la revisión:

```powershell
git restore --staged -- path/to/file.py
```

## 4. Conventional Commits

Formato:

```text
<type>(<scope>): <descripción imperativa>
```

Tipos permitidos:

| Tipo | Uso |
|---|---|
| `feat` | Capacidad observable nueva. |
| `fix` | Corrección de un defecto. |
| `refactor` | Cambio interno sin alterar el contrato observable. |
| `test` | Cobertura o fixtures sin cambio funcional. |
| `docs` | Documentación únicamente. |
| `chore` | Mantenimiento que no cabe en los anteriores. |
| `build` | Empaquetado o dependencias de build. |
| `ci` | Automatización de integración. |
| `perf` | Mejora medible de rendimiento. |
| `revert` | Reversión explícita de un commit anterior. |

Reglas:

- resumen en minúscula, imperativo y sin punto final;
- scope corto y estable, por ejemplo `telegram`, `persistence`, `governance`;
- body para explicar por qué, riesgos o migración, no para repetir el diff;
- cambios incompatibles usan `!` y un footer `BREAKING CHANGE:`;
- no mezclar cambios sin relación para reducir el número de commits.

Ejemplo:

```powershell
git commit -m "docs(governance): define solo-maintainer workflow"
```

Después del commit:

```powershell
git show --stat --oneline HEAD
git show --check HEAD
git status --short --branch
```

## 5. Política de secretos

Nunca se versionan valores reales en `.env`, JSON, YAML, fixtures, prompts,
capturas, comandos, URLs o logs. `.env.example` contiene nombres y valores
vacíos o ficticios; los secretos reales viven en el entorno local o en el gestor
de secretos del despliegue.

Antes del commit, inspeccionar los nombres staged con este guard mínimo:

```powershell
$Staged = @(git diff --cached --name-only --diff-filter=ACMR)
$Forbidden = @($Staged | Where-Object {
  ($_ -match '(?i)(^|/)(\.env($|\.)|id_rsa|id_ed25519|credentials?\.json$)') -and
  ($_ -notmatch '(?i)(^|/)\.env\.example$')
})
if ($Forbidden.Count -gt 0) {
  $Forbidden | ForEach-Object { Write-Error "Ruta sensible staged: $_" }
  throw "Retire las rutas sensibles del índice."
}
```

Validar la configuración versionada de pre-commit y ejecutar sus hooks sobre las
rutas staged:

```powershell
$ChangedFiles = @(git diff --cached --name-only --diff-filter=ACMR)
if ($ChangedFiles.Count -eq 0) { throw "No hay rutas staged para validar." }
uv run pre-commit validate-config
uv run pre-commit run --files @ChangedFiles
```

Los hooks versionados incluyen detección de claves privadas y credenciales AWS.
`gitleaks` solo cuenta como evidencia cuando existe como check CI versionado en
el repositorio; no se improvisan comandos locales ni se declara aprobado un
check que no está configurado. Los hooks no reemplazan la revisión completa del
diff staged.

Si un secreto aparece en cualquier commit:

1. detener la integración y no copiar el valor al log;
2. revocar o rotar la credencial inmediatamente;
3. retirar el archivo del índice o crear una corrección explícita;
4. asumir comprometido cualquier secreto que haya sido enviado a un remoto;
5. coordinar limpieza de historial solo después de rotar, porque borrar el
   archivo no invalida la credencial;
6. añadir una regresión o guard que evite repetir el incidente.

## 6. Rollback seguro

Todo hardening log debe definir disparador, responsable, pasos, impacto en datos
y gate de verificación antes de integrar.

### Cambio sin commit

Revertir únicamente rutas confirmadas, después de revisar su diff:

```powershell
git diff -- path/to/file.py
git restore --worktree -- path/to/file.py
```

No usar ese comando sobre cambios ajenos o no comprendidos.

### Commit local o compartido

Preferir un commit de reversión auditable:

```powershell
$CommitToRevert = $env:COMMIT_TO_REVERT
if ([string]::IsNullOrWhiteSpace($CommitToRevert)) {
  throw "Defina COMMIT_TO_REVERT con el SHA que se debe revertir."
}
git revert $CommitToRevert
```

### Fase integrada con merge commit

Crear una rama de rollback desde `main`, revertir el merge conservando el primer
padre, ejecutar los gates y abrir una PR urgente:

```powershell
$PhaseSlug = "phase-0-governance"
$MergeCommit = $env:MERGE_COMMIT_TO_REVERT
if ([string]::IsNullOrWhiteSpace($MergeCommit)) {
  throw "Defina MERGE_COMMIT_TO_REVERT con el SHA del merge de fase."
}

git switch main
git pull --ff-only
git switch -c "codex/rollback-$PhaseSlug"
git revert -m 1 $MergeCommit
```

No usar `git reset --hard` ni force-push sobre historia compartida. Cambios de
datos o esquema deben tener migración reversible o estrategia de forward-fix;
si no existe una recuperación verificable, la fase queda bloqueada antes del
merge.

## 7. Regla de bloqueo tras tres ciclos

Un ciclo es: reproducir el mismo bloqueo, recoger evidencia, aplicar una
corrección segura y volver a ejecutar el gate afectado. El log debe identificar
el bloqueo con una huella estable: comando, error principal y dependencia o
precondición faltante.

- Ciclo 1: diagnosticar y probar la corrección más directa.
- Ciclo 2: validar una alternativa segura o aislar mejor la causa.
- Ciclo 3: confirmar que persiste la misma condición.

Tras el tercer ciclo consecutivo con la misma huella:

- marcar la tarea y la fase como `BLOCKED`;
- detener reintentos automáticos y no ampliar el alcance;
- conservar cambios reversibles y evidencia, sin stage ni commit adicional;
- registrar qué autoridad, decisión o cambio externo desbloquea el trabajo;
- solicitar aprobación del mantenedor.

Una condición distinta inicia un conteo distinto. Dificultad, lentitud o una
prueba nueva fallando no se consideran por sí solas el mismo bloqueo.

## 8. Gates de fase

Ejecutar desde la rama de fase integrada. Esta es la secuencia canónica y
reproducible; cualquier código de salida distinto de cero bloquea la PR. Los
umbrales de cobertura no se omiten ni se marcan `N/A` si revelan deuda de la
base.

```powershell
$env:APP_ENV_FILE = "disabled"
git fetch --prune
uv lock --check
uv sync --frozen --all-extras --group dev
uv run ruff check .
uv run mypy src
uv run pytest -q
uv run coverage run --source=src/personal_assistant -m pytest
uv run coverage xml
uv run coverage report --fail-under=85
uv run diff-cover coverage.xml --compare-branch origin/main --fail-under=90
uv run python -m compileall -q src
uv build
uv run pip-audit
uv run pre-commit validate-config
uv run pre-commit run --all-files
git diff --check
```

Además:

- ejecutar tests enfocados por cada cambio de comportamiento;
- revisar secretos según la sección 5;
- confirmar que tests omitidos o gates no aplicables tengan justificación y
  riesgo residual en el hardening log;
- probar los pasos de rollback hasta el límite seguro para el entorno local;
- revisar el diff final de la rama de fase contra `main`:

```powershell
git fetch --prune
git diff --stat origin/main...HEAD
git diff --check origin/main...HEAD
git diff origin/main...HEAD --
```

## 9. Una PR por fase y merge commit

Las ramas de tarea no producen PRs por defecto. Se integran y verifican en la
rama de fase; luego se abre una única PR de fase hacia `main`.

```powershell
git push -u origin $PhaseBranch
gh pr create --base main --head $PhaseBranch `
  --title "chore($Phase): complete phase hardening" `
  --body "Resumen, gates, riesgos y rollback: ver hardening log de la fase."
```

La PR debe incluir alcance, fuera de alcance, commits de las dos olas, evidencia
de gates, riesgos residuales, política de secretos y rollback. Con CI verde y la
revisión del mantenedor resuelta, integrar usando merge commit:

```powershell
gh pr merge --merge --delete-branch
```

No usar `--squash` ni `--rebase`; el merge commit es el punto de rollback de la
fase.

## 10. Limpieza posterior

Solo retirar worktrees que estén limpios y cuyas ramas ya estén integradas o
descartadas explícitamente:

```powershell
$TaskPath = Join-Path $WorktreeRoot "$Phase-a1-toolchain"
git -C $TaskPath status --short --branch
git worktree remove $TaskPath
git branch -d "codex/$Phase-a1-toolchain"
git worktree prune
git worktree list
```

No usar `--force` para ocultar cambios pendientes. Un worktree sucio requiere
revisión y decisión explícita.

## 11. Definition of Done

Una tarea está terminada cuando:

- [ ] cumple su objetivo y criterios de aceptación sin ampliar alcance;
- [ ] conserva los invariantes de contrato, tenant, permisos e idempotencia;
- [ ] el diff completo fue revisado antes del staging;
- [ ] las rutas o hunks staged fueron enumerados explícitamente;
- [ ] el diff staged fue revisado y pasa `git diff --cached --check`;
- [ ] los tests enfocados pasan y su evidencia está en el hardening log;
- [ ] no contiene secretos, datos reales ni artefactos temporales;
- [ ] tiene rollback verificable y una unidad de commit reversible;
- [ ] usa un mensaje Conventional Commit;
- [ ] entrega commit, diff, validaciones y riesgos al mantenedor.

Una fase está terminada cuando, además:

- [ ] los cinco roles 3 + 2 entregaron implementación o evidencia
      `REVIEW_ONLY`, sin commits vacíos;
- [ ] los conflictos fueron devueltos al agente afectado, revalidados y
      revisados antes de reintegrar;
- [ ] todas las tareas aceptadas están integradas en la rama de fase;
- [ ] los gates completos de la sección 8 pasan;
- [ ] el hardening log está completo, incluida la revisión de secretos;
- [ ] no hay bloqueos abiertos ni ciclos de fallo sin decisión;
- [ ] la PR única de fase refleja el diff final contra `main`;
- [ ] CI y revisión están resueltos;
- [ ] existe un plan para revertir el merge commit;
- [ ] la PR se integra con merge commit;
- [ ] los worktrees y ramas temporales se limpian de forma segura.

El objetivo no se marca como completo antes de la aprobación del mantenedor y de
la evidencia requerida por este Definition of Done.
