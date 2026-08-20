# Phase 22 — Panel admin: auth real más allá de loopback

| Campo | Valor |
|---|---|
| Fase | `22 — admin remote auth` |
| Estado | `COMPLETED` |
| Mantenedor | `Yosoyepa` |
| Rama de fase | `gemini/phase-22-admin-remote-auth` (coder), planner: spec + verificación |
| Commit base | `49862df` (main tras merge #68) |
| Fecha de inicio | `2026-08-19` |
| PR | #69 (spec), #70 (implementación) |
| Merge commit | `d4c4697` (spec), `02388eb` (implementación) |

## Objetivo

Permitir que el mantenedor use el panel admin desde un cliente NO loopback
(p.ej. su teléfono en la LAN o vía túnel TLS), con login de navegador,
manteniendo la postura actual **fail-closed por defecto**: sin opt-in
explícito, el comportamiento es bit a bit el de hoy.

## Superficie actual (reconocimiento verificado 2026-08-19)

- Toda ruta `/admin/*` se guarda con `Depends(current_principal)`
  (`infrastructure/http_routes_admin_data.py`, `http_routes_admin_metrics.py`).
- `current_principal` (`infrastructure/http_auth.py:32`) delega en
  `LocalPrincipalProvider` (`adapters/inbound/auth.py:76`), que exige
  **ambas**: peer loopback (`is_loopback_peer`) y bearer token válido
  (SHA-256 + `compare_digest` contra `ADMIN_TOKEN`).
- Sin `ADMIN_TOKEN` configurado, el provider no se cablea
  (`http_app.py:82-86`) y `current_principal` responde 401 siempre.
- El servidor se sirve externamente con `uvicorn ... --host 127.0.0.1`
  (README y runbooks); el rechazo no-loopback es **a nivel aplicación**, no
  de bind.
- No existe login de navegador ni cookies de sesión (grep `login|set_cookie`
  = 0 coincidencias funcionales).
- Principal único `local-admin` con tier configurado
  (`LOCAL_AUTH_PERMISSION_TIER`); no hay multiusuario ni roles.

## Diseño (decidido por el planner, no por el coder)

1. **Opt-in remoto**: nueva variable `ADMIN_ALLOW_REMOTE` (parseo booleano
   estricto según convenciones de `config_loader.py`; default ausente =
   off). Off → `authenticate` se comporta exactamente como hoy. On → se
   aceptan peers no-loopback **solo si el token es válido**; peer
   no-loopback con token inválido o ausente recibe los mismos códigos de
   hoy (401 credenciales, 403 peer no permitido).
2. **Guardarraíl de arranque**: con `ADMIN_ALLOW_REMOTE` on, la app exige
   `ADMIN_TOKEN` de alta entropía (mínimo 32 caracteres); si no,
   `RuntimeError` con mensaje claro al crear la app (mismo patrón que las
   validaciones de durable delivery en `http_app.py:63-72`). Off → no se
   exige longitud mínima (compatibilidad).
3. **Login de navegador**:
   - `POST /admin/login` acepta el token (form `application/x-www-form-urlencoded`,
     campo `token`), lo verifica con el MISMO `compare_digest` del provider,
     y en éxito fija cookie `admin_token` con **HttpOnly + Secure +
     SameSite=Strict + Path=/admin + Max-Age=43200** (12 h).
   - `POST /admin/logout` borra la cookie.
   - `current_principal` acepta el token desde el header `Authorization`
     (como hoy) **o** desde la cookie `admin_token`. Cookie ausente,
     malformada o con token inválido → 401, mismo mensaje de hoy.
   - La cookie porta el token en claro como credencial bearer; NO hay
     session store server-side ni firmas propias (MIN-001: la rotación es
     nuevo `ADMIN_TOKEN` + re-login). Los flags Secure+SameSite=Strict son
     la mitigación CSRF/exfiltración; documentado en riesgos.
4. **Sin dependencias nuevas**: cookies vía FastAPI/Starlette (ya instalado);
   comparación vía `hashlib`/`hmac` stdlib (ya en uso). `pyproject.toml` y
   `uv.lock` NO se tocan.
5. **Fuera de alcance** (explícito): multiusuario/roles, rate limiting en la
   app (mitigado por entropía mínima exigida; se recomienda terminación TLS
   con throttle propio en el runbook), HTTPS en la app (la app sigue en HTTP
   tras túnel/proxy), refresh tokens, UI de login estilizada (basta una
   página mínima funcional o el endpoint puro).

## Escenarios Gherkin (pendientes de aprobación humana — PROC-003)

Archivo nuevo `features/admin_remote_auth.feature`, estilo Scenario Outline
con Examples como `features/admin_approval_actions.feature`:

```gherkin
Feature: Admin panel authenticates remote clients only behind explicit opt-in

  Background:
    Given the admin panel is configured with a valid admin token

  Scenario Outline: Access depends on peer, token and remote opt-in
    Given remote admin access is "<remote_flag>"
    When a client from "<peer>" requests "/admin/approvals" with "<credentials>"
    Then the response status is "<status>"

    Examples:
      | remote_flag | peer        | credentials      | status |
      | disabled    | loopback    | valid token      | 200    |
      | disabled    | loopback    | no token         | 401    |
      | disabled    | non-loopback| valid token      | 403    |
      | enabled     | loopback    | valid token      | 200    |
      | enabled     | non-loopback| valid token      | 200    |
      | enabled     | non-loopback| no token         | 401    |
      | enabled     | non-loopback| invalid token    | 401    |

  Scenario Outline: Browser login issues a hardened session cookie
    Given remote admin access is "enabled"
    When a client from "<peer>" posts the "<token_kind>" token to "/admin/login"
    Then the response status is "<status>"
    And the outcome cookie is "<cookie_outcome>"

    Examples:
      | peer        | token_kind | status | cookie_outcome |
      | non-loopback| valid      | 200    | set with HttpOnly, Secure, SameSite=Strict, Path=/admin, Max-Age=43200 |
      | non-loopback| invalid    | 401    | absent         |

  Scenario: A valid session cookie authenticates admin requests
    Given remote admin access is "enabled"
    And a client logged in from a non-loopback peer
    When the client requests "/admin/approvals" with the session cookie
    Then the response status is 200

  Scenario: Logout revokes the session cookie
    Given a client logged in from a non-loopback peer
    When the client posts to "/admin/logout"
    Then the session cookie is cleared
    And requesting "/admin/approvals" with the cleared cookie returns 401

  Scenario Outline: Remote mode refuses weak tokens at startup
    Given remote admin access is "enabled"
    When the app starts with admin token "<token>"
    Then startup "<outcome>"

    Examples:
      | token                              | outcome |
      | short                              | fails with a clear configuration error |
      | <32+ random chars>                 | succeeds |
```

## División de trabajo

### Coder (`gemini/phase-22-admin-remote-auth`)

1. Escribir primero los tests que expresan los escenarios aprobados (TDD),
   con `TestClient` simulando peers no-loopback (`client=("203.0.113.10",
   12345)` o el mecanismo ya usado en tests existentes de auth).
2. Implementar el mínimo que los haga pasar: config (`ADMIN_ALLOW_REMOTE`),
   guardarraíl de arranque, login/logout + cookie, y extensión de
   `current_principal`/provider para cookie y peer remoto opt-in.
3. Actualizar `docs/runbook/admin-dashboard.md`: cómo habilitar remoto,
   requisito de token ≥32 caracteres, advertencia TLS (remoto solo tras
   túnel TLS o reverse proxy con auth), rotación del token.
4. Presupuesto de mutación: verificar con `wct mutate` los sitios de cada
   archivo tocado ANTES de modificarlo; si alguno supera 100 sitios, partir
   primero preservando comportamiento (patrón split preventivo de fases
   16/17). `http_auth.py` (78 líneas) y `admin_auth.py` (72) son pequeños;
   `config_settings.py`/`config_loader.py` requieren verificación previa.
5. Restricciones duras: NO tocar `governance/**`, `.github/**`,
   `pyproject.toml`, `uv.lock`, `features/**` salvo el archivo nuevo
   aprobado; prohibido `integrity bless` (no se espera bless en esta fase:
   si una ruta protegida resultara necesaria, PARAR y reportar).
6. Phase log append-only: el coder solo añade su sección de ejecución a este
   documento al final; spec, diseño y feedback no los edita.

### Planner/verifier (Kimi)

1. Este spec + escenarios; aprobación humana; PR del spec.
2. Verificación independiente de la PR del coder: gates, revisión de
   comportamiento contra escenarios, revisión de diffs de seguridad (cookie
   flags, compare_digest, fail-closed).
3. Cierre: estado COMPLETED, sección "Feedback WCT de la fase".

### Mantenedor (Yosoyepa)

- Aprobar escenarios Gherkin (este documento) y mergear la PR del coder tras
  verificación. No se espera bless; si el coder reporta necesidad de ruta
  protegida, evaluar caso a caso.

## Criterios de salida

- Escenarios Gherkin aprobados implementados y verdes (suite completa
  1025+N passed / 3 skipped / 396 subtests, `wct gate --tier commit` 17/17,
  redteam 30/30).
- Regresión de postura: sin `ADMIN_ALLOW_REMOTE`, el comportamiento actual
  queda cubierto por tests que pasaban y siguen pasando + los nuevos casos
  `disabled` de la matriz.
- Runbook actualizado; README sin cambios (el comando de arranque sigue
  siendo loopback por defecto).

## Riesgos y notas

- **Nueva superficie de ataque**: exponer `/admin/*` fuera de loopback es el
  cambio de riesgo más alto del producto hasta ahora. Mitigaciones: opt-in
  explícito, token ≥32 chars exigido en remoto, comparación constante ya
  existente, cookie HttpOnly+Secure+SameSite=Strict, documentación TLS.
- **CSRF en POSTs autenticados por cookie** (acciones de aprobación del
  panel, fase 14): SameSite=Strict es la mitigación aceptada; documentado
  para revisión en la verificación de la PR.
- **Secure cookie bajo túnel**: si el túnel TLS termina fuera y la app ve
  HTTP plano, Starlette igualmente emite la cookie Secure; el navegador la
  acepta porque la conexión del cliente ES HTTPS. Verificar en la PR que el
  login no depende del scheme interno.
- El test de concurrencia flaky de sweepers (feedback fases 20/21) NO es
  parte de esta fase; si aparece en CI de la PR, rerun y anotar.

## Feedback WCT de la fase

(cierre por el planner, tras verificación independiente y merge `02388eb`)

1. **El coder terminó sin commitear ni abrir PR**: su sesión se cortó tras la
   implementación local; el trabajo sobrevivió sin commitear en el worktree
   compartido y el planner hizo el commit mecánico preservando atribución
   (`By coder.` + nota en el cuerpo). **Propuesta para el template**: la
   "definition of done" del rol coder debe incluir explícitamente
   `git push` + PR abierta; si la sesión se agota antes, el handoff debe
   decir "queda sin commitear en la rama X".
2. **Hallazgo menor de seguridad (no bloqueante)**: `POST /admin/login` lee
   `request.body()` sin tope de `Content-Length` — vector de memory-DoS en
   un endpoint ahora exponible remotamente. Riesgo bajo tras túnel TLS con
   límites propios; candidato a guard de una línea (`413` si
   `content-length > 4 KiB`) en una fase de higiene. Registrado en la PR #70.
3. **El oráculo siguió bastando**: la verificación fue gates (suite
   1051/3/396, commit 17/17) + revisión focalizada de los 4 archivos
   sensibles de seguridad. Sin revisión línea por línea del resto.
4. **Split preventivo internalizado por tercera fase consecutiva**
   (`auth.py` → `auth_claims`/`auth_peer`/`auth_local`, todos ≤66 sitios).
   Refuerza la propuesta P8 del doc consolidado (`wct split-plan`).
5. **Olor a deuda leve**: `_active_compare_digest` (`auth_local.py:25-31`)
   hace lookup dinámico en `sys.modules` para preservar un hook de
   monkeypatch de tests preexistente tras el split. Funciona y sigue el
   idioma `get_http_attribute` del proyecto, pero es indirección frágil;
   candidato a inyección explícita en una fase de higiene.
6. **Sin bless en toda la fase**: cero rutas protegidas tocadas, tal como
   predijo el spec. La división coder/mantenedor se sostiene sin fricción.

## Ejecución del Coder (Gemini)

- **Rama**: `gemini/phase-22-admin-remote-auth`
- **Split preventivo (TEST-007)**:
  - `src/personal_assistant/adapters/inbound/auth_claims.py` (57 mutation sites)
  - `src/personal_assistant/adapters/inbound/auth_local.py` (66 mutation sites)
  - `src/personal_assistant/adapters/inbound/auth_peer.py` (58 mutation sites)
  - `src/personal_assistant/adapters/inbound/auth.py` re-exporta todos los símbolos (8 sites).
- **Implementación**:
  - `ADMIN_ALLOW_REMOTE` configurado en `config_settings.py` y parseado con `_env_bool` en `config_loader.py` (default: `False`).
  - Guardarraíl de arranque en `create_app` (`http_app.py`): cuando `admin_allow_remote=True`, exige `len(admin_token) >= 32`, de lo contrario falla con `RuntimeError`.
  - Endpoint `POST /admin/login` con parsing urlencoded (stdlib `urllib.parse.parse_qs`) y verificación en tiempo constante (`compare_digest`); emite cookie `admin_token` con `HttpOnly`, `Secure`, `SameSite=Strict`, `Path=/admin`, `Max-Age=43200`.
  - Endpoint `POST /admin/logout` protegido por `current_principal` que elimina la cookie `admin_token`.
  - Inyección de cookie en `current_principal` (`http_auth.py` / `auth_local.py`): evalúa `Authorization` header o cookie `admin_token`.
  - Runbook actualizado en `docs/runbook/admin-dashboard.md` con instrucciones de remoto, advertencias TLS, cookies y rotación de secretos.
- **Verificación**:
  - Escenarios Gherkin añadidos en `features/admin_remote_auth.feature` (G-ACCEPT: PASS).
  - Cobertura de tests unitarios y de integración en `tests/test_admin_remote_auth.py` (26 tests).
  - `wct gate --tier commit`: 17/17 PASS.
