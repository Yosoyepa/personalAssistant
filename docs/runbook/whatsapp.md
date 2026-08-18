# WhatsApp Channel Runbook

Este runbook documenta la configuración, operación, prueba de humo local y diagnóstico del canal de WhatsApp en `personalAssistant` (inbound webhook y outbound replies/recordatorios). No contiene credenciales reales ni secretos (SEC-001).

---

## 1. Variables de entorno

El canal de WhatsApp se configura a través de las siguientes variables de entorno:

| Variable | Tipo | Obligatoria | Descripción |
|---|---|---|---|
| `WHATSAPP_ENABLED` | Booleano (`true`/`false`) | No (default: `false`) | Habilita el procesamiento de webhooks y envíos de WhatsApp. |
| `WHATSAPP_APP_SECRET` | String | Sí (si `ENABLED=true`) | App Secret de Meta utilizado para validar la firma HMAC SHA-256 (`X-Hub-Signature-256`). |
| `WHATSAPP_VERIFY_TOKEN` | String | Sí (si `ENABLED=true`) | Token secreto configurado en el portal de Meta para validar el challenge del webhook (`GET /webhooks/whatsapp`). |
| `WHATSAPP_ALLOWED_USER_IDS` | String (separado por comas) | Sí (si `ENABLED=true`) | Lista blanca de números telefónicos (E.164 sin `+`, ej. `573000000000`) autorizados para interactuar con el asistente. |
| `WHATSAPP_ACCESS_TOKEN` | String | No (opcional) | Access Token de Meta Graph API con permisos `whatsapp_business_messaging` para enviar replies y recordatorios. |
| `WHATSAPP_PHONE_NUMBER_ID` | String | Sí (si `ACCESS_TOKEN` configurado) | Identificador del número de teléfono en WhatsApp Business Cloud API. |

Ejemplo de configuración en `.env` (solo valores ficticios):

```bash
WHATSAPP_ENABLED=true
WHATSAPP_APP_SECRET=test_app_secret_value_32_chars_long
WHATSAPP_VERIFY_TOKEN=test_verify_token_custom_value
WHATSAPP_ALLOWED_USER_IDS=573000000001,573000000002
WHATSAPP_ACCESS_TOKEN=test_access_token_placeholder
WHATSAPP_PHONE_NUMBER_ID=100000000000001
```

---

## 2. Configuración en Meta for Developers

1. **Creación de la App**:
   - En [Meta for Developers](https://developers.facebook.com/), crea o selecciona una app de tipo **Business**.
   - Agrega el producto **WhatsApp**.
2. **Configuración del Webhook**:
   - URL de devolución de llamada: `https://<tu-dominio>/webhooks/whatsapp`
   - Identificador de verificación: El valor exacto configurado en `WHATSAPP_VERIFY_TOKEN`.
   - En **Campos de webhook**, suscríbete a `messages`.
3. **Credenciales de API**:
   - Copia el **App Secret** desde *Configuración básica* hacia `WHATSAPP_APP_SECRET`.
   - Obtén un **Token de acceso del sistema** con el permiso `whatsapp_business_messaging` y asígnalo a `WHATSAPP_ACCESS_TOKEN`.
   - Copia el **Identificador de número de teléfono** a `WHATSAPP_PHONE_NUMBER_ID`.

---

## 3. Seguridad y Verificación Criptográfica

- **Verificación Inbound (HMAC-SHA256)**: Cada petición `POST /webhooks/whatsapp` debe incluir la cabecera `X-Hub-Signature-256: sha256=<hex_digest>`. El digest se calcula usando `WHATSAPP_APP_SECRET` como clave sobre el cuerpo crudo en bytes de la petición.
- **Protección contra Replay**: Los identificadores de mensaje (`wamid...`) se almacenan en caché; mensajes duplicados devuelven respuesta inmediata `status: duplicate` sin reejecutar lógica de negocio.
- **Aislamiento de Remitentes**: Remitentes fuera de `WHATSAPP_ALLOWED_USER_IDS` son rechazados silenciosamente (`status: skipped`, HTTP 200) para no filtrar metadatos ni permitir ataques de amplificación.
- **Egress Allowlist**: Si `WHATSAPP_ACCESS_TOKEN` está presente, el servidor valida que el host `graph.facebook.com` esté autorizado en el allowlist de salida antes de iniciar conexiones de red.

---

## 4. Prueba de Humo Local con Payload Sintético

Puedes verificar el funcionamiento del endpoint localmente utilizando `curl` y `openssl` para calcular la firma HMAC correspondiente:

### Paso 1: Iniciar el servidor local

```bash
export WHATSAPP_ENABLED=true
export WHATSAPP_APP_SECRET="test_app_secret_value"
export WHATSAPP_VERIFY_TOKEN="test_verify_token_value"
export WHATSAPP_ALLOWED_USER_IDS="573000000001"
export WHATSAPP_ACCESS_TOKEN="test_access_token_value"
export WHATSAPP_PHONE_NUMBER_ID="100000000000001"
export PERSISTENCE_BACKEND=memory

uv run uvicorn personal_assistant.infrastructure.http:app --port 8000
```

### Paso 2: Verificar el Challenge (GET)

```bash
curl -X GET "http://127.0.0.1:8000/webhooks/whatsapp?hub.mode=subscribe&hub.verify_token=test_verify_token_value&hub.challenge=1158201444"
```

*Respuesta esperada:* `1158201444` (HTTP 200).

### Paso 3: Enviar Mensaje Sintético Firmado (POST)

```bash
PAYLOAD='{
  "object": "whatsapp_business_account",
  "entry": [{
    "id": "100000000000001",
    "changes": [{
      "value": {
        "messaging_product": "whatsapp",
        "metadata": {"display_phone_number": "15550000000", "phone_number_id": "100000000000001"},
        "contacts": [{"profile": {"name": "Usuario Test"}, "wa_id": "573000000001"}],
        "messages": [{
          "from": "573000000001",
          "id": "wamid.HBgLNTczMDAwMDAwMDAVAgASGBQzQT...",
          "timestamp": "1723980000",
          "type": "text",
          "text": {"body": "/agenda"}
        }]
      },
      "field": "messages"
    }]
  }]
}'

SECRET="test_app_secret_value"
SIGNATURE=$(printf '%s' "$PAYLOAD" | openssl dgst -sha256 -hmac "$SECRET" | sed 's/^.* //')

curl -s -X POST "http://127.0.0.1:8000/webhooks/whatsapp" \
  -H "Content-Type: application/json" \
  -H "X-Hub-Signature-256: sha256=$SIGNATURE" \
  -d "$PAYLOAD"
```

*Respuesta esperada:* `{"status":"ok","channel":"whatsapp","sent":false}` (o `sent:true` si se configuró un token válido de Graph API).

---

## 5. Diagnóstico y Troubleshooting

| Síntoma | Causa Probable | Solución |
|---|---|---|
| `GET /webhooks/whatsapp` retorna 403 | `hub.verify_token` no coincide con `WHATSAPP_VERIFY_TOKEN` o `hub.mode != "subscribe"`. | Verificar el token configurado en Meta y en la variable de entorno. |
| `POST /webhooks/whatsapp` retorna 401 | Cabecera `X-Hub-Signature-256` ausente o firma HMAC no coincide con `WHATSAPP_APP_SECRET`. | Asegurarse de que el secret en Meta coincide exactamente con la variable de entorno. |
| `POST /webhooks/whatsapp` retorna `status: skipped` con HTTP 200 | El número remitente (`from`) no está en `WHATSAPP_ALLOWED_USER_IDS` o `WHATSAPP_ENABLED=false`. | Añadir el número en formato E.164 (sin signos `+`) a la lista blanca. |
| El webhook responde `sent: false` | `WHATSAPP_ACCESS_TOKEN` no está definido, o Graph API rechazó el envío (4xx/5xx/rate limit). | Revisar los logs del servidor y verificar permisos del token en el Business Manager de Meta. |
| Recordatorios quedan en estado `uncertain` en Outbox | Caída de conexión o respuesta ambigua de Graph API durante el envío en el worker. | Reconciliar la entrega desde el panel `/admin/dashboard` o mediante `personal-assistant-worker --resolve <id>`. |
