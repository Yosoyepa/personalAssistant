# Demo WhatsApp real (sandbox de Meta)

Guía para mostrar el loop completo del producto con mensajes reales de
WhatsApp: texto y nota de voz → transcripción/extracción → recordatorio
agendado → entrega proactiva del worker → visibilidad en el panel admin.

Público objetivo: el mantenedor (operador único). Tiempo estimado: 45-60 min
la primera vez.

## 1. Qué vas a montar

```
WhatsApp (tu número) ⇄ Meta Cloud API (sandbox)
        │  HTTPS (firma HMAC-SHA256)
        ▼
Túnel TLS (cloudflared / ngrok)  ──solo GET+POST /webhooks/whatsapp──▶  app (127.0.0.1:8000)
                                                                          │
                                          ┌───────────────────────────────┤
                                          ▼                               ▼
                                     PostgreSQL 16                  panel admin
                                     (worker durable)              (loopback, login)
```

## 2. Prerrequisitos externos (Meta)

1. Cuenta de desarrollador en <https://developers.facebook.com/> y una app con
   el producto **WhatsApp** añadido.
2. En **WhatsApp → Configuración de API**:
   - Anota el **número de prueba** y su **Phone number ID**
     (`WHATSAPP_PHONE_NUMBER_ID`).
   - Genera el **token de acceso temporal** (24 h). Para una demo estable,
     crea un token permanente vía *System User*; si no, basta el temporal —
     pero verifica que no haya expirado justo antes de la demo.
   - Registra **tu número personal** como destinatario de prueba
     (*To* → *Manage phone number list*). La sandbox solo entrega a números
     registrados.
3. En **Configuración de la app → Básica**: copia el **App Secret**
   (`WHATSAPP_APP_SECRET`).
4. Inventa un **verify token** (cualquier cadena larga y aleatoria; se usa solo
   en el handshake): `WHATSAPP_VERIFY_TOKEN`.

## 3. Configuración local

1. Copia `.env.example` a `.env` (ignorado por git) y completa:

   ```bash
   PERSISTENCE_BACKEND="postgres"
   DATABASE_URL="postgresql://<usuario>:<clave>@127.0.0.1:5432/<db>"
   ADMIN_TOKEN="<al menos 32 caracteres aleatorios>"

   WHATSAPP_ENABLED="true"
   WHATSAPP_APP_SECRET="<app secret>"
   WHATSAPP_VERIFY_TOKEN="<el verify token que inventaste>"
   WHATSAPP_ALLOWED_USER_IDS="<tu número, p. ej. 573001112233>"
   WHATSAPP_ACCESS_TOKEN="<token de Meta>"
   WHATSAPP_PHONE_NUMBER_ID="<phone number id>"

   TRANSCRIPTION_PROVIDER="groq"          # u otro compatible OpenAI
   TRANSCRIPTION_API_KEY="<tu Groq key>"  # o GROQ_API_KEY
   TRANSCRIPTION_BASE_URL="https://api.groq.com/openai"
   TRANSCRIPTION_MODEL="whisper-large-v3-turbo"

   REMINDER_WORKER_ENABLED="true"
   ```

   La egress allowlist se deriva sola: con `WHATSAPP_ACCESS_TOKEN` presente
   quedan permitidos `graph.facebook.com` y `lookaside.fbsbx.com` (descarga de
   media). Si defines `EGRESS_ALLOWED_HOSTS` explícito, debe cubrir ambos o el
   arranque falla cerrado.

2. PostgreSQL 16 activo y migraciones aplicadas:

   ```bash
   export DATABASE_URL="postgresql://..." DATABASE_SCHEMA="public"
   uv run python -m personal_assistant.infrastructure.migrations status
   uv run python -m personal_assistant.infrastructure.migrations apply
   ```

   Detalles en `docs/runbook/persistence.md`.

3. Arranca la app y el worker (dos terminales):

   ```bash
   uv run uvicorn personal_assistant.infrastructure.http:app --host 127.0.0.1 --port 8000
   uv run python -m personal_assistant.infrastructure.worker
   ```

4. Verifica salud: `curl http://127.0.0.1:8000/livez` y `.../readyz`
   (200; `/readyz` queda 503 si la DB no está migrada — fail-closed).

## 4. Borde HTTPS (túnel)

Meta exige HTTPS público para el callback. Opciones:

- `cloudflared tunnel --url http://127.0.0.1:8000`
- `ngrok http 8000`

Anota la URL pública (`https://<subdominio>.trycloudflare.com` o similar).
El único path que Meta necesita es `/webhooks/whatsapp` (GET para el
handshake, POST para los mensajes). El panel admin sigue accesible por
loopback; no hace falta exponerlo para la demo (si lo expones, exige
`ADMIN_ALLOW_REMOTE=true` + túnel TLS + token ≥32 — ver
`docs/runbook/admin-dashboard.md`).

## 5. Alta del webhook en Meta

En **WhatsApp → Configuración → Webhook** de la app:

1. Callback URL: `https://<tu-túnel>/webhooks/whatsapp`
2. Verify token: el mismo `WHATSAPP_VERIFY_TOKEN` del `.env`
3. Guardar y verificar → Meta hace `GET /webhooks/whatsapp`; la app responde
   el challenge si el token coincide (`compare_digest`).
4. Suscríbete al campo **messages** (es el único que usa la demo).

## 6. Guión de demo (minuto a minuto)

Abre el panel en el navegador: `http://127.0.0.1:8000/admin/login`
(token = `ADMIN_TOKEN`; cookie de sesión HttpOnly/SameSite=Strict) y deja
visible `http://127.0.0.1:8000/admin/dashboard`.

1. **Texto → recordatorio**: desde tu WhatsApp envía
   `recuérdame llamar a Ana mañana a las 5 pm`.
   - Respuesta casi inmediata en WhatsApp (confirmación del recordatorio).
   - Panel: *Traces* muestra el run con `llm_called`; *Scheduler* el evento
     programado; *Events* la creación.
2. **Voz → transcripción → recordatorio**: envía una nota de voz
   («recuérdame pagar el arriendo el lunes a las 9 am»).
   - *Traces*: evento `tool_called` con `audio.transcribe` y el transcript
     truncado. El reply llega por WhatsApp igual que con texto.
3. **Entrega proactiva**: el worker (intervalo 15 s) entrega los recordatorios
   a la hora debida vía WhatsApp. Para no esperar, crea uno a 2-3 minutos
   vista. *Outbox* muestra la fila entregada; *Events* el `delivered`.
4. **Idempotencia**: en el panel de Webhooks de Meta (o reintentos de Meta),
   una re-entrega del mismo `wamid` se acusa sin duplicar el recordatorio —
   *Scheduler* sigue con un solo evento.
5. **Medio no soportado**: envía una imagen. La respuesta explícita
   `whatsapp_media_unsupported` llega por WhatsApp; no hay descarga ni
   transcripción (visible por la ausencia de trace de transcripción).
6. **Seguridad en vivo** (opcional): desde un número NO registrado, cualquier
   mensaje recibe 403 sin efectos (*Errors* no registra nada, no hay red
   saliente).

## 7. Troubleshooting

| Síntoma | Causa probable | Acción |
|---|---|---|
| Handshake de Meta falla | verify token distinto | Revisa `WHATSAPP_VERIFY_TOKEN` y reintenta |
| 401 en POST | `WHATSAPP_APP_SECRET` incorrecto | Firma HMAC fail-closed; copia el App Secret de nuevo |
| 403 en POST | número no allowlist o no registrado en sandbox | `WHATSAPP_ALLOWED_USER_IDS` y lista de destinatarios de Meta |
| Reply dice "transcripción no configurada" | `TRANSCRIPTION_PROVIDER=disabled` o sin API key | Configura Groq y reinicia |
| Reply "descarga fallida" de media | token expirado (24 h) o egress sin `lookaside.fbsbx.com` | Renovar token; revisar `EGRESS_ALLOWED_HOSTS` |
| No hay entregas proactivas | worker apagado o `REMINDER_WORKER_ENABLED=false` | Arranca el worker; revisa *Scheduler* |
| `/readyz` 503 | DB sin migrar | `migrations apply` (la app no aplica DDL sola) |

## 8. Captura de fixtures de regresión (post-demo, fase 27 etapa 2)

Tras la demo, en el panel de **Webhooks** de la app de Meta queda el historial
de entregas con el payload JSON completo. Copia el payload real de (a) un
mensaje de texto y (b) una nota de voz (`type: "audio"` con `voice: true`),
anonimiza números si hace falta, y entrégalos al equipo para fijarlos como
fixtures de regresión en los tests del webhook.

## 9. Seguridad

- El `.env` real jamás se commitea (está ignorado; el ejemplo lleva valores
  vacíos — SEC-001).
- La firma HMAC se verifica antes de cualquier otra lógica; un remitente no
  autorizado no provoca llamadas salientes (testeado en fase 25).
- Los binarios de audio viven solo en memoria durante la transcripción.
- Rota `WHATSAPP_ACCESS_TOKEN` y `ADMIN_TOKEN` después de la demo si se
  mostraron en pantalla.
