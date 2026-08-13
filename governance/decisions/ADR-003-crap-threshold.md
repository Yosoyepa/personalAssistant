# ADR-003 — CRAP ≤ 6 en código nuevo

- **Estado**: aceptada
- **Fecha**: 2026-08-11
- **Decide**: usuario
- **Alternativas consideradas**: strict=6 en código nuevo · standard=15 global · legacy=30 con ratchet

## Contexto: dos fuentes que se contradicen

```
CRAP(fn) = CC² × (1 − coverage)³ + CC
```

| Fuente | Umbral |
|---|---|
| Tabla publicada en `crap4go`/`crap4clj` README | 1–5 limpio · 5–30 moderado · **30+ crappy** |
| `swarm-forge@six-pack roles/cleaner.prompt` | *"Run the language CRAP tool first and **reduce CRAP to 6 or below**."* |
| `crap4py` 0.1.1 | Sin default. Exige `--max-crap N` explícito. |

Uncle Bob publica 30 como umbral de alarma y a la vez exige 6 a sus propios agentes. La diferencia es un factor de cinco y hay que resolverla, no promediarla.

## Decisión

**`changed_max: 6`** — aplica siempre al código cambiado, sin importar el perfil del repo.
**`profiles: {strict: 6, standard: 15, legacy: 30}`** — aplica al repo completo, con ratchet bajando.

Un repo `legacy` **no autoriza escribir código nuevo malo**. Es el mismo principio que `diff-cover` aplica a la cobertura, trasladado a CRAP.

## Por qué 6 y no 30: la aritmética

La fórmula es multiplicativa, así que el umbral determina la combinación de complejidad y cobertura que se acepta:

| CC | cobertura | CRAP | ≤6 | ≤15 | ≤30 |
|---|---|---|---|---|---|
| 3 | 100 % | 3.0 | ✅ | ✅ | ✅ |
| 6 | 100 % | 6.0 | ✅ (límite exacto) | ✅ | ✅ |
| 6 | 95 % | 6.00075 | ⚠ al filo | ✅ | ✅ |
| 6 | 90 % | 6.036 | ❌ | ✅ | ✅ |
| 10 | 96 % | 10.006 | ❌ | ✅ | ✅ |
| 10 | 90 % | 10.1 | ❌ | ✅ | ✅ |
| 12 | 80 % | 13.15 | ❌ | ✅ | ✅ |
| 12 | 70 % | 15.89 | ❌ | ❌ | ✅ |
| 15 | 60 % | 29.4 | ❌ | ❌ | ✅ |
| 12 | 45 % | 130.2 | ❌ | ❌ | ❌ |

Lo que el umbral 6 fuerza: **funciones pequeñas Y cobertura por rama casi total, simultáneamente.** Con `CC = 6` exige cobertura ~100 %; con `CC = 10` exige ~96 %; con `CC = 12` es inalcanzable a cualquier cobertura (el término `+ CC` por sí solo ya lo duplica).

Eso es exactamente el punto: **hace imposibles F5 y F9 a la vez**, con un solo número. Con umbral 30, una función de CC 12 y 45 % de cobertura falla, pero una de CC 12 con 70 % pasa — y esa es la forma típica de la función que un agente produce cuando escribe la implementación primero y los tests después.

## Por qué el umbral 30 sigue existiendo

Como punto de entrada para adopción, no como destino. Un repo existente con 400 funciones no puede pasar a 6 en un commit, y bloquear todo el trabajo el primer día garantiza que el harness se desinstale.

**Riesgo reconocido**: el ratchet se estanca y 30 se vuelve permanente. Mitigación: `wct report` muestra la trayectoria de cada ratchet, así que el estancamiento es visible en vez de silencioso. No hay mitigación técnica más fuerte que esa; es una decisión de equipo.

## Cómo remediar (STYLE-003)

Como la fórmula es multiplicativa, el mismo score sale de causas opuestas y la remediación equivocada no lo mueve:

| Causa dominante | Síntoma | Remediación |
|---|---|---|
| CC alto | CC > 10, cobertura buena | **Parte la función.** Añadir tests no baja el término `+ CC`. |
| Cobertura baja | CC ≤ 6, cobertura < 90 % | **Añade tests.** Partir una función bien estructurada no ayuda. |
| Ambas | CC > 10 y cobertura < 80 % | Parte primero, luego cubre. Partir reduce el número de caminos a cubrir. |

## Interacción con el resto de los gates

- **`G-MUT-SITES`** (≤ 100 sitios de mutación por archivo, de `cleaner.prompt`) presiona en la misma dirección desde otro ángulo: tamaño de archivo en vez de complejidad de función.
- **`G-COV-DIFF`** (≥ 90 % en líneas nuevas) es el suelo de cobertura independiente. CRAP ≤ 6 con CC 6 exige más que eso, así que `G-CRAP` es el gate vinculante para funciones complejas y `G-COV-DIFF` para funciones simples.
- **`G-CC`** (`xenon --max-absolute B`, CC ≤ 10) es el techo de complejidad independiente de la cobertura. Existe para que una función de CC 20 con 100 % de cobertura —que pasa CRAP ≤ 6 con score 20… no, falla— sea rechazada por dos gates distintos, no uno.

## Implementación

```bash
# Repo completo, umbral del perfil
crap4py src/ --lcov lcov.info --max-crap 6

# Código cambiado, umbral fijo 6 (lo hace `wct gate` filtrando por diff)
wct gate --tier commit          # incluye G-CRAP con changed_max
```

Requiere cobertura por rama en formato LCOV (`crap4py` lee registros `BRDA`):

```bash
pytest --cov --cov-branch --cov-report=lcov:lcov.info
```

## Consecuencias

- `governance/thresholds.yaml` → `crap.changed_max: 6`, con la cita de `cleaner.prompt` en el comentario.
- El propio `wct` se somete a este umbral (perfil `strict`). Si el harness no puede pasar su propio harness, el umbral está mal calibrado y eso es información, no una excusa para bajarlo.
- Subir `changed_max` requiere `wct integrity bless` con razón y aprobador humano, y queda en `governance/integrity-log.md`.
