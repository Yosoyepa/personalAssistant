# Phase 25 — WhatsApp media entrante (audio/voz → transcripción → pipeline de conversación)

| Campo | Valor |
|---|---|
| Fase | `25 — whatsapp inbound media (audio/voice transcription)` |
| Estado | `APPROVED` (escenarios Gherkin aprobados por el mantenedor, 2026-08-20) |
| Mantenedor | `Yosoyepa` |
| Rama de fase | `gemini/phase-25-whatsapp-media` (coder a **medium effort**, con regla de reporte de desviaciones de la fase 24) |
| Commit base | `3d06ead` (main tras merge #78, release v0.2.0-alpha.3) |
| Fecha de inicio | `2026-08-20` |
| PR | TBD (spec), TBD (implementación) |
| Merge commit | TBD |

## Objetivo

Los mensajes de **audio/nota de voz** entrantes por WhatsApp dejan de ser un
no-op silencioso: se descargan de la Graph API de Meta, se transcriben con el
proveedor ya existente (`AudioTranscriptionProvider`) y el transcript entra al
pipeline de conversación exactamente como un mensaje de texto (extracción de
recordatorios, idempotencia por `wamid`, replies, traces).

## Decisión de alcance (a aprobar)

- **Dentro**: `audio` y `voice` (nota de voz). Es el mismo alcance que ya tiene
  Telegram en producción.
- **Fuera**: `image`, `document`, `video`, `sticker`, etc. No existe puerto de
  visión/OCR en `application/ports/` (MIN-001, peldaño 2: reutilizar lo
  existente). Estos mensajes reciben una **respuesta explícita** de "medio no
  soportado" — nunca un no-op silencioso.
- **Sin almacenamiento de binarios**: el audio vive en memoria durante la
  descarga/transcripción y se descarta (igual que Telegram). Los traces guardan
  solo metadatos + transcript truncado a 500 chars.

## Blueprint: el camino de Telegram ya existe

La fase es un **espejo** del pipeline de Telegram
(`src/personal_assistant/infrastructure/http_telegram_transcription.py`,
enganchado en `http_routes_telegram.py:70-96`). Puertos, adapter de
transcripción OpenAI-compatible y `container.transcription` son
canal-agnósticos y ya están en producción.

## Ítems

### 25A — Normalizador: poblar campos de media

- `src/personal_assistant/adapters/inbound/channels/whatsapp.py:15-36` hoy solo
  lee `messages[0].text.body`; ignora `message.type` y los bloques
  `audio`/`voice`/`image`/`document`.
- Cambio: poblar `media_kind`, `media_file_id` (= media-id de Meta),
  `media_mime_type`, `media_file_size` (campos que `NormalizedMessage` ya tiene,
  `application/dto/channels.py:31-34`). Para mensajes de media sin caption,
  `text = f"[{media_kind} message]"` (patrón de `channels/telegram.py:62-63`).
- Mensajes de tipo desconocido o sin contenido aprovechable siguen siendo no-op
  con 200 (comportamiento actual preservado).

### 25B — Cliente Graph API: descarga de media (seguridad primero)

- `WhatsAppGraphApiClient` (`adapters/outbound/notifications/whatsapp_client.py:25-97`)
  gana dos métodos:
  - `get_media_url(media_id)` → `GET /{api_version}/{media_id}` con Bearer
    token; retorna la URL de descarga efímera de Meta.
  - `download_media(url)` → descarga autenticada con Bearer token.
- **Redirects manualmente validados**: Meta redirige a `lookaside.fbsbx.com`.
  Prohibido `follow_redirects=True` a ciegas: seguir redirects a mano y validar
  CADA hop contra la `EgressAllowlist` (fail-closed, patrón de
  `adapters/outbound/egress.py:76-101`).
- `derive_egress_entries` (`egress.py:104-130`) añade `lookaside.fbsbx.com`
  cuando hay `whatsapp_access_token` configurado (sin él, la descarga no puede
  ocurrir de todos modos).
- **Orden de chequeos innegociable**: firma HMAC → normalización → allowlist de
  remitente (403) → SOLO ENTONCES red (get_media_url/download). Un remitente no
  autorizado no puede provocar llamadas salientes.
- El access token jamás se loguea; errores de Graph API se clasifican con el
  esquema existente (`whatsapp_parsing.py`).

### 25C — Hook de transcripción en el router de WhatsApp

- Nuevo módulo `src/personal_assistant/infrastructure/http_whatsapp_transcription.py`
  espejo del de Telegram: validaciones (file_id presente, transcripción
  configurada, tamaño declarado), descarga, validación de tamaño real,
  transcripción (`language="es"`, presupuesto `TokenBudget(limit=4_000)`),
  traces `tool_called`/`agent_failed` con run_id
  `whatsapp:{conversation_id}:{message_id}:transcription`, y
  `message.model_copy(update={"text": transcript.text, "command": None, "command_args": ""})`.
- Enganche en `http_routes_whatsapp.py` entre la resolución del principal y
  `container.commands.handle`, igual que `http_routes_telegram.py:70-96`.
- **TEST-007 preventivo**: si el módulo supera ~100 sitios de mutación, partir
  (patrón del split de auth en fase 22).
- Prompt de transcripción: se **reutiliza** la entrada `telegram_voice_transcription`
  de `prompts/registry.json` (su contenido es genérico en español). Si el coder
  considera que el nombre telegram-específico confunde, proponer alias nuevo en
  el reporte — no renombrar el existente (rompería Telegram).

### 25D — Límites y replies

- `MAX_WHATSAPP_AUDIO_BYTES = 20 * 1024 * 1024` junto a
  `MAX_TELEGRAM_AUDIO_BYTES` (`infrastructure/http_auth.py:21-29`), reutilizando
  `SUPPORTED_TRANSCRIPTION_EXTENSIONS`.
- Replies nuevos en `locales/es.json` + métodos en
  `application/services/replies.py` (mismo mecanismo que `telegram_audio_*`,
  `replies.py:181-199`): `whatsapp_audio_missing_file_id`,
  `whatsapp_transcription_not_configured`, `whatsapp_audio_too_large`,
  `whatsapp_audio_download_too_large`, `whatsapp_media_download_failed`,
  `whatsapp_transcription_failed`, `whatsapp_media_unsupported`.

### 25E — Gherkin, tests y docs

- Escenarios nuevos en `features/whatsapp_inbound_webhook.feature` (abajo,
  pendientes de aprobación). Cuidado con colisiones `placeholder-variant`:
  pasos estructuralmente distintos de los existentes.
- Tests en `tests/test_whatsapp_inbound_webhook.py` + un archivo nuevo si el
  presupuesto de sitios lo exige. Fixtures de payload con `type: audio|voice|image`.
- Docs: `docs/runbook/whatsapp.md` (sección de media: límites, egress
  `lookaside.fbsbx.com`, `TRANSCRIPTION_PROVIDER` compartido), y actualizar el
  known-gap de WhatsApp en el README y en `docs/releases/` cuando toque.

## Escenarios Gherkin (APROBADOS por el mantenedor, 2026-08-20)

Se añaden a `features/whatsapp_inbound_webhook.feature`:

```gherkin
  Scenario Outline: Signed voice note is transcribed and handled as a reminder
    When a signed WhatsApp webhook delivers a "<media_kind>" audio of <size_kb> KB from "<wa_number>"
    Then the audio is downloaded and transcribed for tenant "<tenant_id>"
    And the transcribed text is handled by the conversation pipeline
    And the response carries the assistant reply with sent flag "false"

    Examples:
      | wa_number    | tenant_id | media_kind | size_kb |
      | 573001112233 | personal  | audio      | 120     |
      | 573001112233 | personal  | voice      | 340     |

  Scenario Outline: Oversized audio is rejected with an explicit reply before download
    When a signed WhatsApp webhook delivers an audio declaring <size_mb> MB from "<wa_number>"
    Then no download or transcription is attempted
    And the user receives the whatsapp_audio_too_large reply

    Examples:
      | wa_number    | size_mb |
      | 573001112233 | 21      |
      | 573004445566 | 100     |

  Scenario: Audio whose downloaded bytes exceed the limit is rejected after download
    When a signed WhatsApp webhook delivers an audio whose downloaded payload exceeds the limit
    Then the user receives the whatsapp_audio_download_too_large reply

  Scenario: Transcription unavailable produces an explicit reply
    Given the transcription provider is not configured
    When a signed WhatsApp webhook delivers a "voice" audio of 100 KB from "573001112233"
    Then the user receives the whatsapp_transcription_not_configured reply

  Scenario: Transcription failure produces an explicit reply and a failure trace
    When transcription of a signed "voice" message from "573001112233" fails at the provider
    Then the user receives the whatsapp_transcription_failed reply
    And an agent_failed trace is recorded for the transcription

  Scenario Outline: Non-audio media gets an explicit unsupported reply
    When a signed WhatsApp webhook delivers a "<media_kind>" message from "<wa_number>"
    Then no download or transcription is attempted
    And the user receives the whatsapp_media_unsupported reply

    Examples:
      | wa_number    | media_kind |
      | 573001112233 | image      |
      | 573001112233 | document   |
      | 573001112233 | video      |

  Scenario: Media from an unauthorized sender is rejected before any download
    When a signed WhatsApp webhook delivers a "voice" audio from an unknown number
    Then the endpoint rejects it and no download, transcription, or reminder is attempted

  Scenario: Replayed delivery of the same audio message creates no duplicate
    When a signed WhatsApp webhook delivers audio message "<message_id>" a second time
    Then the endpoint acknowledges it and the reminder exists exactly once
```

## Criterios de salida

1. Suite completa en verde contra PostgreSQL real + gate `wct gate --tier commit`
   17/17 + redteam 30/30.
2. Cero mutantes sobrevivientes en lo cambiado y archivos dentro del presupuesto
   de sitios (TEST-002, TEST-007).
3. Los 8 escenarios Gherkin nuevos implementados con tests que fallarían ante
   una implementación plausiblemente incorrecta (TEST-001, TEST-003).
4. Orden de chequeos de seguridad verificado por test: remitente no autorizado
   → cero llamadas de red (mock de cliente Graph sin invocaciones Y aserción
   sobre el resultado del SUT).
5. Formato acotado: `wct fmt --staged` si se tocan archivos legacy (regla de la
   fase 24).
6. Phase log cerrado por el planner con métricas y feedback WCT.

## Instrucciones de proceso para el coder (fijas desde la fase 24)

- Esfuerzo **medium**; el planner/verificador no baja.
- El reporte debe declarar **toda** desviación del spec o del texto Gherkin
  aprobado, aunque sea benigna (regla añadida al cierre de la fase 24).
- Anotación de ejecución append-only en este documento (no editar lo anterior).

## Ejecución del coder

- **Fecha y autor**: 2026-08-21 — Coder (Gemini).
- **Componentes implementados**:
  - **25A**: Normalizador `WhatsAppAdapter.normalize_webhook` enriquecido para extraer `media_kind`, `media_file_id`, `media_mime_type` y `media_file_size` sobre mensajes de tipo `audio`, `voice`, `image`, `document`, `video`, con texto de reserva `f"[{media_kind} message]"`.
  - **25B**: Egress allowlist actualizado con `DEFAULT_WHATSAPP_MEDIA_HOST = "lookaside.fbsbx.com"`. En `WhatsAppGraphApiClient` se implementaron `get_media_url` y `download_media` con intercepción manual de redirecciones (`_NoRedirectHandler`) y validación fail-closed de cada salto contra `EgressAllowlist`.
  - **25C**: Módulo `http_whatsapp_transcription.py` creado e integrado en `http_routes_whatsapp.py`. Se preserva el orden estricto de seguridad: HMAC $\rightarrow$ Allowlist remitente (403) $\rightarrow$ descarga de medios / transcripción.
  - **25D**: `MAX_WHATSAPP_AUDIO_BYTES = 20 * 1024 * 1024` declarado en `http_auth.py`. 7 cadenas de respuesta en `locales/es.json` y métodos correspondientes en `ChannelRepliesMixin` / `AssistantReplies`.
  - **25E**: 8 escenarios Gherkin añadidos en `features/whatsapp_inbound_webhook.feature`, suite completa de pruebas en `tests/test_whatsapp_inbound_media.py`, y runbooks actualizados (`docs/runbook/whatsapp.md`, `README.md`).
- **Desviaciones declaradas**:
  1. *Desambiguación de pasos Gherkin para `accept ir-dry`*: Se ajustaron ligeramente 3 frases de pasos en `features/whatsapp_inbound_webhook.feature` (`"the voice response carries..."`, `"no media download or transcription..."`, `"the endpoint acknowledges the audio replay..."`) para eliminar colisiones estructurales `placeholder-variant` contra pasos preexistentes. Resultado: `accept ir-dry` pasa con 0 findings.
  2. *Modularización de `replies.py` para cumplir `TEST-007` (max 100 sitios de mutación)*: Se extrajo `ChannelRepliesMixin` a `src/personal_assistant/application/services/channel_replies.py`, heredado por `AssistantReplies`, reduciendo los sitios de `replies.py` a 88 y `channel_replies.py` a 16.
- **Gates y verificación**:
  - `wct gate --tier fast`: 7/7 PASS.
  - `wct gate --tier commit`: 17/17 PASS.
  - `pytest` suite completa: 1122 tests PASS, 0 fallos.
  - `wct selftest redteam`: 30/30 adversarios bloqueados.

## Feedback WCT de la fase

(pendiente — se llena al cierre por el planner)

