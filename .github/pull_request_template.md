## Alcance

<!-- Qué cambia y qué criterio de aceptación satisface. -->

## Fuera de alcance

<!-- Qué se deja intencionalmente para otra fase. -->

## Evidencia de verificación

<!-- Comandos ejecutados, resultados y checks CI relevantes. -->

- [ ] `quality`
- [ ] `tests (3.11)`
- [ ] `tests (3.12)`
- [ ] `security`
- [ ] `postgres-integration`

## Riesgos y rollback

<!-- Riesgo residual, disparador de rollback y commit/merge commit que se revertiría. -->

## Seguridad y datos

- [ ] Revisé el diff completo y no contiene secretos ni datos personales reales.
- [ ] Las credenciales de fixtures o servicios CI son ficticias y están aisladas.
- [ ] Los proveedores externos permanecen deshabilitados o reemplazados por fakes en pruebas.

## Revisión e integración

- [ ] El cambio conserva tenant scope, permisos e idempotencia cuando aplican.
- [ ] Los cambios de comportamiento tienen pruebas o una justificación registrada.
- [ ] El diff final y el plan de rollback fueron revisados por el mantenedor.
- [ ] Esta PR se integrará con merge commit; no con squash ni rebase.
