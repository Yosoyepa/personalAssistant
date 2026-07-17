# Gobierno de GitHub y checks requeridos

Este repositorio declara su gobierno en `.github/` y conserva la configuración
remota como un paso explícito del mantenedor. Ningún workflow necesita tokens de
Telegram, LLM, voz ni otro proveedor externo: esas integraciones permanecen
deshabilitadas y las pruebas usan fakes. La contraseña del servicio PostgreSQL
es una credencial efímera y pública, limitada al contenedor del runner.

## Checks estables

La protección de `main` debe exigir exactamente estos contextos. Los nombres son
parte del contrato y no se deben cambiar sin actualizar simultáneamente el
script de gobierno y esta guía.

| Check | Responsabilidad |
|---|---|
| `quality` | lock, Ruff, Mypy, compilación y build del paquete |
| `tests (3.11)` | suite completa y cobertura global de líneas en Python 3.11 |
| `tests (3.12)` | suite completa, cobertura global de líneas y diff coverage en Python 3.12 |
| `security` | detección de secretos con Gitleaks y auditoría con `pip-audit` |
| `postgres-integration` | arranque de PostgreSQL 16, creación real del esquema y tests del adaptador |

Los workflows usan permisos `contents: read`, instalaciones bloqueadas por
`uv.lock`, timeouts y cancelación de ejecuciones obsoletas. `Dependabot` agrupa
actualizaciones menores/parches de Python y actualizaciones de GitHub Actions.

## Revisar el plan sin tocar GitHub

El modo predeterminado de `scripts/configure-github-governance.ps1` es `Plan`.
No requiere `gh`, no hace llamadas de red y muestra el JSON deseado:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass `
  -File scripts/configure-github-governance.ps1
```

El plan configura:

- PR obligatoria para `main`, incluidos administradores;
- los cinco checks anteriores, actualizados contra la base de la rama;
- resolución obligatoria de conversaciones;
- prohibición de force-push y eliminación de `main`;
- merge commit habilitado, squash/rebase deshabilitados y borrado de la rama
  después del merge.

La regla exige cero aprobaciones bloqueantes porque el repositorio tiene un solo
mantenedor y GitHub no permite aprobar la propia PR. `CODEOWNERS` solicita a
`@Yosoyepa` en todos los cambios; la revisión manual y el checklist de la PR
siguen siendo obligatorios.

## Verificar drift de forma read-only

Después de que los checks hayan aparecido al menos una vez en GitHub, autenticar
`gh` con una cuenta que pueda leer la configuración y ejecutar:

```powershell
gh auth status
powershell -NoProfile -ExecutionPolicy Bypass `
  -File scripts/configure-github-governance.ps1 `
  -Mode Verify
```

`Verify` solo usa solicitudes `GET`, compara merge methods, checks y protección
de rama, e informa un código de salida distinto de cero si encuentra drift.

## Aplicar solo con autorización

La aplicación muta el repositorio remoto y, por diseño, requiere dos señales
explícitas. No debe ejecutarse desde CI ni antes de revisar el plan:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass `
  -File scripts/configure-github-governance.ps1 `
  -Mode Apply `
  -ConfirmRemoteMutation
```

`PATCH` y `PUT` fijan el estado completo deseado, por lo que repetir el comando
es idempotente. Al terminar, el mismo script ejecuta `Verify`. Si una llamada
falla a mitad del proceso, se puede corregir la precondición y repetirla sin
acumular reglas duplicadas.

## Evidencia antes de proteger `main`

1. Abrir la PR de fase y comprobar que existen los cinco nombres exactos.
2. Resolver fallos reales; no relajar Mypy ni los umbrales de cobertura para
   convertir una base roja en verde.
3. Ejecutar `Plan` y adjuntar la salida revisada al hardening log sin datos de
   autenticación.
4. Tras autorización del mantenedor, ejecutar `Apply` una sola vez desde un
   entorno local autenticado.
5. Ejecutar `Verify`, guardar su resultado y confirmar en la UI que solo está
   disponible el método merge commit.

Para rollback, cambiar primero el script y esta guía mediante PR, revisar el
nuevo plan y volver a aplicar. No desactivar la protección directamente como
atajo ante un check fallido.
