# Phase 24 — Higiene (flake sweeper + login body guard + compare_digest) y experimento de esfuerzo medium

| Campo | Valor |
|---|---|
| Fase | `24 — hygiene + medium-effort coder experiment` |
| Estado | `SPEC_APPROVED` (escenario Gherkin 24B aprobado por el mantenedor, 2026-08-20) |
| Mantenedor | `Yosoyepa` |
| Rama de fase | `gemini/phase-24-hygiene` (coder a **medium effort**), planner: spec + verificación (sin bajar esfuerzo) |
| Commit base | `643c66a` (main tras merge #74) |
| Fecha de inicio | `2026-08-20` |
| PR | pendiente |
| Merge commit | pendiente |

## Objetivo doble

1. **Higiene**: cerrar los tres cabos menores abiertos en fases 20–23.
2. **Experimento controlado** (decisión del mantenedor, 2026-08-20): el coder
   corre a esfuerzo **medium** por primera vez; el planner/discriminador NO
   baja. La fase es mecánica y acotada — la clase de riesgo donde medium es
   defendible según la evaluación documentada al cierre de la fase 23.

## Ítems (3, todos mecánicos y acotados)

### 24A — Flake del sweeper concurrente: presupuesto de convergencia

- **Hecho**: `tests/test_delivery_adversarial_postgres.py::test_concurrent_sweepers_are_disjoint_idempotent_and_ignore_foreign_events`
  falló 2 veces en `tests (3.12)` de CI ("sweeper did not converge after
  conflicts") y pasa al rerun. Causa raíz verificada
  (`tests/test_delivery_adversarial_postgres.py:691-701`): cada sweeper
  reintenta `ReminderTransactionConflict` solo **3 veces**; bajo contención
  real de Postgres en CI no siempre alcanza.
- **Fix permitido**: subir el presupuesto de convergencia del test (p.ej. de
  3 a 10 intentos, opcionalmente con micro-backoff de unos ms entre
  intentos). NADA más.
- **Trampas PROHIBIDAS** (el punto del ejercicio): no debilitar aserciones
  (disjointness, unión == esperado, evento foreign intacto, sweep vacío
  idempotente se mantienen tal cual), no capturar excepciones más amplias,
  no añadir plugins de rerun (dependencia nueva), no marcar skip/xfail.
- **Evidencia exigida**: 20 ejecuciones consecutivas locales del test en
  verde (bucle shell contra Postgres real) + CI verde.

### 24B — `POST /admin/login`: guard de `Content-Length`

- **Hecho** (hallazgo de verificación, fase 22): `admin_login` lee
  `request.body()` sin tope — vector de memory-DoS en un endpoint ahora
  exponible remotamente (`http_routes_admin_auth.py`).
- **Fix**: rechazar con **413** ANTES de leer el body cuando el header
  `Content-Length` supere **4096** bytes. Dentro del límite, comportamiento
  actual (parse + verify → 200/401/403 según corresponda).
- **Residual documentado** (en el phase log): un cliente chunked sin
  `Content-Length` aún puede enviar bodies grandes; mitigación aceptada =
  terminador TLS/proxy delante (ya documentado como requisito en el runbook
  de la fase 22). No se pide streaming con tope en esta fase.
- **Gherkin nuevo** (pendiente de aprobación humana, se añade a
  `features/admin_remote_auth.feature`):

```gherkin
  Scenario Outline: Login rejects oversized bodies before reading them
    Given remote admin access is "enabled"
    When a client posts a login body of <size> bytes to "/admin/login"
    Then the response status is "<status>"
    And no session cookie is issued

    Examples:
      | size | status |
      | 4096 | 401    |
      | 4097 | 413    |
```

### 24C — Eliminar la indirección `_active_compare_digest`

- **Hecho** (feedback fase 22): `auth_local.py:25-31` hace lookup dinámico en
  `sys.modules` para preservar un hook de monkeypatch de tests preexistentes
  tras el split de `auth.py`.
- **Fix**: `verify_token` llama directamente a `hmac.compare_digest`
  (importado al módulo); se elimina `_active_compare_digest`. Los tests que
  parcheaban `personal_assistant.adapters.inbound.auth.compare_digest` se
  reescriben para ejercer el comportamiento REAL (token correcto/incorrecto)
  en vez de parchear el comparador — mejor trazabilidad al SUT (TEST-003).
  Debe mantenerse la cobertura de las mismas rutas (rama de digest
  coincidente y no coincidente).

## División de trabajo

### Coder (`gemini/phase-24-hygiene`, esfuerzo medium)

1. TDD donde aplique: 24B empieza por el test del 413/4096-4097; 24C por
   reescribir los tests parcheados a comportamiento real (deben FALLAR con
   la indirección presente si la indirección fuera funcionalmente distinta —
   aquí el criterio es: misma cobertura de ramas sin tocar
   `sys.modules`); 24A es cambio de constante/presupuesto con evidencia
   empírica.
2. Restricciones duras de siempre: sin `governance/**`, `.github/**`,
   `pyproject.toml`, `uv.lock`; prohibido `integrity bless` y
   `mutate update-manifest --approved-by` (el guard los bloquea en tu
   sesión); phase log append-only; no tocar otros `features/**` que el
   escenario nuevo aprobado.
3. Verificación obligatoria antes de entregar (el spec pide TIER COMMIT, no
   fast): suite completa con `TEST_POSTGRES_DSN`, `wct gate --tier commit`
   17/17, y las 20 corridas del test de 24A.
4. Definition of done (ya validada en fase 23): commits con `By coder.`,
   push y PR abierta; si la sesión se agota, prioriza commit+push y dilo.

### Planner (Kimi) — sin bajar esfuerzo

1. Spec + aprobación del Gherkin; verificación independiente completa:
   gates, diff review con foco en las trampas prohibidas de 24A y en que
   24C no cambie semántica, y fidelidad del reporte del coder (¿corrió lo
   que dice haber corrido?).
2. Cierre: estado COMPLETED, feedback WCT y **métricas del experimento**.

### Mantenedor (Yosoyepa)

- Aprobar el escenario Gherkin de 24B; mergear tras verificación. No se
  espera bless (ninguna ruta protegida en el alcance).

## Métricas del experimento (se llenan al cierre)

| Métrica | Umbral de éxito | Resultado |
|---|---|---|
| Gates verdes en la PR del coder sin intervención del planner | sí | pendiente |
| Fidelidad del reporte (corrió tier commit y lo reportó) | sí | pendiente |
| Hallazgos de revisión del discriminador (bloqueantes) | 0 | pendiente |
| Iteraciones de rework pedidas al coder | ≤1 | pendiente |
| Trampas prohibidas intentadas (24A) | 0 | pendiente |

Conclusión esperada: si la fila anterior sale limpia, medium pasa a ser el
default para fases de clase "mecánica bien especificada" (tabla de decisión
del cierre de la fase 23). Si no, se documenta qué falló.

## Criterios de salida

- Suite completa verde (1105+N), `wct gate --tier commit` 17/17, redteam
  30/30, CI 5/5.
- 20 corridas consecutivas del test de 24A en verde (evidencia en la PR).
- Diff review: aserciones de 24A intactas; 24B rechaza antes de leer; 24C
  sin `sys.modules` y con cobertura de ramas equivalente.

## Riesgos y notas

- Riesgo principal del experimento: que medium degrade el REPORTE antes que
  el código (precedente fase 23: tier fast reportado como verificación
  completa). Por eso la fidelidad del reporte es métrica explícita.
- 24B añade el primer uso de 413 del runtime; verificar que el manejador de
  errores global lo serializa igual que 401/403.

## Feedback WCT de la fase

(pendiente — se llena al cierre por el planner, junto con las métricas del
experimento)

## Ejecución del coder

### 1. Detalle por ítem

#### 24A — Flake sweeper concurrente (`tests/test_delivery_adversarial_postgres.py`)
- Se elevó el presupuesto de convergencia en `test_concurrent_sweepers_are_disjoint_idempotent_and_ignore_foreign_events` de 3 a 10 intentos, incorporando un micro-backoff `time.sleep(0.005)` en la captura de `ReminderTransactionConflict`.
- Se mantuvieron intactas todas las aserciones de disjointness, unión, no afectación del evento foráneo y sweep vacío. No se añadieron plugins de rerun ni marcas de skip/xfail.
- Se verificaron 20 ejecuciones consecutivas locales en verde contra la base de datos PostgreSQL real.

#### 24B — Guard Content-Length (`/admin/login`)
- Se añadió el escenario Gherkin a `features/admin_remote_auth.feature` (Scenario Outline para 4096 vs 4097 bytes).
- Se incorporó `ErrorCode.PAYLOAD_TOO_LARGE` en `domain/common/exceptions.py` con mapeo a HTTP 413 en `infrastructure/http_errors.py`, asegurando serialización JSON uniforme estándar.
- En `infrastructure/http_routes_admin_auth.py`, se valida el header `Content-Length` antes de leer el body (`request.body()`); si excede 4096 bytes, se lanza `AssistantError(ErrorCode.PAYLOAD_TOO_LARGE, ...)` retornando 413 sin procesar datos ni emitir cookies.
- Se agregaron los tests en `tests/test_admin_remote_auth.py` siguiendo TDD.
- **Residual documentado**: clientes con transfer-encoding chunked sin header `Content-Length` requieren terminador TLS o proxy inverso como filtro perimetral (documentado en el runbook de la fase 22).

#### 24C — Eliminación de `_active_compare_digest` (`adapters/inbound/auth_local.py`)
- Se eliminó la función `_active_compare_digest` y su inspección dinámica en `sys.modules`. `LocalPrincipalProvider.verify_token` invoca directamente `hmac.compare_digest`.
- Se limpió `adapters/inbound/auth.py` eliminando el re-export de `compare_digest`.
- Se reescribió el test en `tests/test_local_auth.py` (`test_token_comparison_verifies_correct_and_incorrect_tokens`) para probar directamente el SUT con tokens correctos e incorrectos (de igual y distinta longitud) sin monkeypatching, trazando comportamiento real (TEST-003).

### 2. Evidencia de verificación

- **20 ejecuciones de 24A contra PostgreSQL**:
  ```text
  Run 1..20: 1 passed in ~1.0s cada una (20/20 PASS)
  ```
- **Test suite completa (`pytest`)**:
  ```text
  1108 passed, 3 skipped, 1 warning, 396 subtests passed in 107.16s
  ```
- **WCT Gate Tier Commit (`wct gate --tier commit`)**:
  ```text
  GATE           STATUS  MS      SUMMARY
  G-META-1       PASS    385     configuración protegida coincide con integrity.lock
  G-META-2       PASS    67      todas las reglas nombran verificadores conocidos
  G-RULES-DRIFT  PASS    82      copias por proveedor sincronizadas
  G-SUPPRESS     PASS    1686    sin erosión por supresiones
  G-DEBT         PASS    756     deuda diferida trazable
  G-LINT         PASS    16      ok
  G-FMT          SKIP    0       desactivado por policy.yaml
  G-TYPE         PASS    454     ok
  G-TEST         PASS    102855  ok
  G-ARCH         PASS    401     ok
  G-ARCHMETRICS  PASS    925     dependency graph y métricas A/I/D saludables
  G-DEPS         PASS    343     ok
  G-DEAD         PASS    1666    ok
  G-SAST-BANDIT  PASS    4085    ok
  G-SECRET       PASS    2200    sin secretos nuevos
  G-MUT-SITES    PASS    1377    archivos dentro del presupuesto de mutación
  G-ACCEPT       PASS    2       Gherkin parseable y sin repetición estructural

  17/17 gates no bloqueantes
  ```
- **Redteam Adversarial (`wct selftest redteam`)**:
  ```text
  30/30 adversarios rechazados
  ```
