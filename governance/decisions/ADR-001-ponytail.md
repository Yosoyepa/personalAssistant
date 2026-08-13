# ADR-001 — Ponytail: vendorizar la escalera, no instalar el plugin

- **Estado**: aceptada
- **Fecha**: 2026-08-11
- **Decide**: usuario (sesión de diseño del harness)
- **Evidencia**: [`RESEARCH.md`](../../RESEARCH.md) §1

## Contexto

`DietrichGebert/ponytail` (MIT, ~100.6k stars, plugin v4.9.0) es un paquete de reglas + skills + hooks multi-proveedor cuyo único objetivo es sesgar al agente hacia la implementación mínima, mediante una escalera de 7 peldaños.

Se evaluó como candidato de integración porque F1 (boilerplate/sobre-construcción) y F2 (reimplementar lo que ya existe) son los modos de fallo más frecuentes del código generado por agentes **y los más difíciles de atrapar con gates**: `wct dry` y `vulture` los detectan *después* de escritos, no antes.

## Decisión

**Adoptar la escalera como capa de sesgo (anillos 1–2). Versionar su texto en `governance/rules/20-minimalism.yaml` con atribución MIT. NO instalar el plugin.**

Aplicar cuatro overrides obligatorios (MIN-002 a MIN-005).

## Razones para adoptar la escalera

Es la única contramedida disponible que actúa **antes** de que el código se escriba. Su coste es ~2.5 KB de contexto. Evidencia medida publicada por el propio repo (sesiones headless de Claude Code editando `tiangolo/full-stack-fastapi-template`, 12 tickets, n=4, Haiku 4.5, puntuadas sobre el `git diff`):

| Arm vs. baseline | LOC | tokens | coste | tiempo | safe |
|---|---|---|---|---|---|
| ponytail | −54 % | −22 % | −20 % | −27 % | 100 % |
| control de prosa terse | −20 % | +7 % | +3 % | +2 % | 100 % |
| prompt "YAGNI + one-liners" | −33 % | −14 % | −21 % | −30 % | 95 % |

Las ganancias se concentran donde existe la trampa de sobre-construcción (date picker: 404 → 23 líneas) y tienden a cero en código ya mínimo — lo que es el comportamiento esperado de una contramedida honesta.

## Razones para NO instalar el plugin

| Motivo | Detalle |
|---|---|
| Aporta cero al Plano de la Prueba | Sus tres hooks (`SessionStart`, `SubagentStart`, `UserPromptSubmit`) solo inyectan contexto. Ninguno devuelve `permissionDecision: deny` ni exit 2. |
| **Falla abierto en silencio** | Requiere `node` en el PATH no-interactivo. Si falta, la activación enmudece sin señal. Viola directamente el requisito fail-closed del harness (PLAN §6.4). |
| Escribe fuera del proyecto | Inserta una entrada `statusLine` en `~/.claude/settings.json`. Una plantilla base no debe tocar la configuración global del usuario. |
| Footgun de desinstalación | Hay que correr `node scripts/uninstall.js` **antes** de quitar el plugin, porque el script vive dentro del plugin. |
| Superficie de mantenimiento | 210 commits, 52 issues y 72 PRs abiertos frente a 100k stars. |
| Portabilidad frágil por construcción | El propio repo documenta que Gemini no puede llevar `hooks/hooks.json` en la raíz porque Gemini auto-carga esa ruta y los nombres de evento son de Claude/Codex. |

El texto de la regla es MIT y son 2.5 KB. Vendorizarlo cuesta un archivo y elimina las seis filas de arriba.

## Los cuatro overrides

Sin ellos, la escalera **contradice** las reglas de arquitectura y de testing. Los tres conflictos están documentados en `RESEARCH.md` §1.5.

### MIN-002 — Peldaño 5 subordinado a la Dependency Rule (conflicto C1)

Ponytail dice: *"¿Dependencia ya instalada? Úsala."*
`architect.prompt` de swarm-forge dice: *"Identify and correct… framework leakage, low-level data-shape leakage"* y *"Keep application policy isolated from UI, filesystem, database, network, framework, and device details."*

Usar la dependencia instalada **dentro del core** es exactamente la violación que ARCH-002 rechaza. El peldaño 5 se lee: úsala en la capa de adaptadores, detrás de un puerto.

### MIN-003 — Cláusula de testing anulada (conflicto C2)

Ponytail dice: *"non-trivial logic leaves ONE runnable check behind, the smallest thing that fails if the logic breaks (an assert-based demo/self-check or one small test file; **no frameworks, no fixtures**). Trivial one-liners need no test."*

`coder.prompt` dice: *"use TDD to specify behavior before implementation. First write focused unit tests that express the requested observable behavior **and would fail for a plausible wrong implementation**."*

Un self-check con `assert` sin framework no sobrevive mutation testing ni cobertura por rama. **Se resuelve a favor de la verificación**, y se escribe explícitamente en la regla porque el texto original dice lo contrario.

### MIN-004 — Marcadores `ponytail:` como deuda rastreada

Sin owner e issue, un marcador diferido es una nota privada que nadie volverá a leer. Con ratchet sobre el conteo total.

### MIN-005 — `ultra` prohibido

El modo agresivo ("para cuando el codebase te ha ofendido personalmente") optimiza brevedad a costa de legibilidad. No es una decisión que un proyecto deba heredar por defecto de su plantilla.

## Lo que se roba además de la escalera

`node scripts/check-rule-copies.js` + `npm test`: **falla el test suite si las copias de la regla en los 8 directorios de proveedor divergen del texto canónico.** Es el mecanismo que hace confiable cualquier regla multi-proveedor, y es la base de `wct rules check` (gate `G-RULES-SYNC`). Discutiblemente el segundo activo más valioso del repo, y trivial de replicar.

## Caveat registrado

El propio README de ponytail publica dos honestidades que quedan asentadas aquí:

1. **Auto-corrección de marketing.** Las cifras previas de "80–94 % menos código" venían de benchmarks single-shot donde el baseline del modelo desnudo rellenaba con prosa. El issue [#126](https://github.com/DietrichGebert/ponytail/issues/126) lo señaló; ahora 80–94 % se presenta como techo por tarea, no como media.

2. **Coste en modelos de razonamiento.** Modelos que gastan tokens de thinking deliberando la escalera pueden salir **peores** en coste y latencia. Se reporta explícitamente en GPT-5.5.

**Consecuencia operativa**: el modo por defecto de la plantilla es `lite` (`governance/policy.yaml` → `minimalism_mode`). Para decidir si conviene subir a `full` en un proyecto concreto, mide: corre el mismo conjunto de tareas con `lite` y con `full`, y compara LOC del diff, tokens y wall-clock. No lo asumas.

## Consecuencias

- `governance/rules/20-minimalism.yaml` contiene la escalera y los 4 overrides.
- Ninguna dependencia de Node en el camino crítico del harness.
- Los peldaños que se pueden verificar lo hacen: peldaño 2 → `G-DRY`, peldaño 5 → `G-DEPS`, resultado general → `G-DEAD`. Los peldaños 1, 4, 6 y 7 quedan como `verified_by: [human]` y aparecen así en `wct report`.
- Si ponytail evoluciona y añade enforcement real, esta decisión se revisa. El vendoring hace que el coste de cambiar de opinión sea un `git diff` sobre un archivo.
