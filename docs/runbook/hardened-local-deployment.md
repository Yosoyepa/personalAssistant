# Hardened Local Deployment Runbook

This runbook exposes a local Telegram bot safely enough for development: the
application binds to loopback and the public HTTPS edge forwards exactly one
request shape. It is not a SaaS deployment guide and it does not promise
exactly-once delivery. Telegram may retry; the application handles accepted
updates idempotently where supported.

## Boundary Contract

Run the application only on `127.0.0.1` (or `::1`), using the explicit bind
arguments in the launch command below.
The only public route is:

```text
POST /webhooks/telegram
```

An HTTPS reverse proxy or tunnel may forward that route to
`http://127.0.0.1:8000` and **must deny every other path and method**. Never
publish `/healthz`, `/readyz`, `/v1/runtime/*`, `/admin`, or `/admin/*`.

Telegram authenticates the webhook with
`X-Telegram-Bot-Api-Secret-Token`, which must match
`TELEGRAM_WEBHOOK_SECRET`. Telegram actors come only from Telegram `from.id`.
`TELEGRAM_ALLOWED_USER_IDS` is default-deny: an empty value denies every actor.

`/v1/runtime/*`, `/admin`, and `/admin/*` remain loopback-only and require
`Authorization: Bearer <ADMIN_TOKEN>`. Their tenant, principal, and permission
tier are fixed by `ASSISTANT_TENANT_ID`, `LOCAL_AUTH_PRINCIPAL_ID`, and
`LOCAL_AUTH_PERMISSION_TIER` on the server. `X-Principal-Id`, `X-Tenant-Id`,
`X-Permission-Tier`, `X-Admin-Token`, and `tenant_id`/`principal_id` query
parameters do not grant or change authority.

## Local Secret Setup (PowerShell)

Do not put credentials in a shell profile, committed file, issue, screenshot,
or command transcript. Run this from the repository root to create `.env` from
`.env.example` when it does not exist, or explicitly replace the two generated
secrets after a backup. It aborts unless `.env` is ignored by git and each
target assignment occurs exactly once. It writes the values directly to `.env`
without printing them.

```powershell
# Default is safe: do not change an existing .env. After reviewing it, change
# only this local flag to $true to rotate the two secrets and create a backup.
$replaceExisting = $false

$envFile = Join-Path (Get-Location) '.env'
$exampleFile = Join-Path (Get-Location) '.env.example'
git check-ignore -q -- .env
if ($LASTEXITCODE -ne 0) { throw '.env is not ignored by git; refusing to write secrets.' }
if (-not (Test-Path -LiteralPath $exampleFile)) { throw '.env.example is missing.' }

if (Test-Path -LiteralPath $envFile) {
  if (-not $replaceExisting) {
    throw '.env already exists; review it, then set $replaceExisting = $true.'
  }
  $backupName = ".env.backup-$(Get-Date -Format 'yyyyMMddHHmmss')"
  git check-ignore -q -- $backupName
  if ($LASTEXITCODE -ne 0) {
    throw 'The generated .env backup would not be ignored; refusing to copy secrets.'
  }
  $backupFile = Join-Path (Get-Location) $backupName
  Copy-Item -LiteralPath $envFile -Destination $backupFile -ErrorAction Stop
  Write-Output "Created ignored rollback backup: $backupFile"
} else {
  Copy-Item -LiteralPath $exampleFile -Destination $envFile -ErrorAction Stop
}

$lines = @(Get-Content -LiteralPath $envFile -ErrorAction Stop)
function New-UrlSafeSecret {
  $bytes = [byte[]]::new(32)
  [Security.Cryptography.RandomNumberGenerator]::Fill($bytes)
  try {
    return [Convert]::ToBase64String($bytes).TrimEnd('=').Replace('+', '-').Replace('/', '_')
  } finally {
    [Array]::Clear($bytes, 0, $bytes.Length)
  }
}
foreach ($name in 'ADMIN_TOKEN', 'TELEGRAM_WEBHOOK_SECRET') {
  $pattern = '^{0}=(?:"[^"]*"|[^\s#]*)\s*$' -f [regex]::Escape($name)
  $indexes = @(
    for ($index = 0; $index -lt $lines.Count; $index++) {
      if ($lines[$index] -match $pattern) { $index }
    }
  )
  if ($indexes.Count -ne 1) {
    throw "Expected exactly one assignment for setting: $name"
  }
  $secret = New-UrlSafeSecret
  $lines[$indexes[0]] = "$name=`"$secret`""
  Remove-Variable secret
}
$utf8NoBom = New-Object System.Text.UTF8Encoding($false)
[IO.File]::WriteAllLines($envFile, [string[]]$lines, $utf8NoBom)
Write-Output 'Generated ADMIN_TOKEN and TELEGRAM_WEBHOOK_SECRET in ignored .env.'
```

The generated rollback backup contains the old secrets. Use it only to recover
the short rotation window, then delete it after the new deployment verifies.

Set `TELEGRAM_BOT_TOKEN` from BotFather and a deliberate, non-empty list in
`TELEGRAM_ALLOWED_USER_IDS`. Keep the placeholder values in `.env.example`
empty. Before starting, verify without revealing values:

```powershell
function Test-RequiredDotenv {
  param([Parameter(Mandatory)][string]$EnvFile)

  $envLines = Get-Content -LiteralPath $EnvFile -ErrorAction Stop
  $required = 'ADMIN_TOKEN','TELEGRAM_BOT_TOKEN','TELEGRAM_WEBHOOK_SECRET'
  $required | ForEach-Object {
    $name = $_
    $pattern = '^{0}=(?<value>"[^"]*"|[^\s#]*)\s*$' -f [regex]::Escape($name)
    $assignments = @($envLines | Where-Object { $_ -match $pattern })
    if ($assignments.Count -ne 1) {
      throw "Expected exactly one assignment for required setting: $name"
    }
    $value = ([regex]::Match($assignments[0], $pattern)).Groups['value'].Value.Trim('"')
    if ([string]::IsNullOrWhiteSpace($value)) {
      throw "Required setting is empty: $name"
    }
  }

  $name = 'TELEGRAM_ALLOWED_USER_IDS'
  $pattern = '^{0}=(?<value>"[^"]*"|[^\s#]*)\s*$' -f [regex]::Escape($name)
  $assignments = @($envLines | Where-Object { $_ -match $pattern })
  if ($assignments.Count -ne 1) {
    throw "Expected exactly one assignment for setting: $name"
  }
  $value = ([regex]::Match($assignments[0], $pattern)).Groups['value'].Value.Trim('"')
  if ([string]::IsNullOrWhiteSpace($value)) {
    throw 'TELEGRAM_ALLOWED_USER_IDS is empty: all Telegram actors will be denied.'
  }
}

Test-RequiredDotenv -EnvFile '.env'
```

The function neither writes a matched line nor returns a value. To reproduce
the validation before using a real `.env`, run the same function above followed
by this isolated self-test (the values are placeholders only):

```powershell
$tempEnv = New-TemporaryFile
@'
ADMIN_TOKEN="placeholder-admin-token"
TELEGRAM_BOT_TOKEN="000000:placeholder-bot-token"
TELEGRAM_WEBHOOK_SECRET="placeholder-webhook-secret"
TELEGRAM_ALLOWED_USER_IDS="123456789"
'@ | Set-Content -LiteralPath $tempEnv -NoNewline
try {
  Test-RequiredDotenv -EnvFile $tempEnv
  Write-Output 'dotenv validation self-test passed'
} finally {
  Remove-Item -LiteralPath $tempEnv -Force -ErrorAction SilentlyContinue
}
```

To verify the Windows PowerShell 5.1-safe UTF-8 write path against the
application's real dotenv reader, use a temporary file with placeholders. This
prints no settings or secret values:

```powershell
$tempEnv = New-TemporaryFile
$checkFile = New-TemporaryFile
$utf8NoBom = New-Object System.Text.UTF8Encoding($false)
$lines = @(
  'ADMIN_TOKEN="placeholder-admin-token"',
  'TELEGRAM_BOT_TOKEN="000000:placeholder-bot-token"',
  'TELEGRAM_WEBHOOK_SECRET="placeholder-webhook-secret"',
  'TELEGRAM_ALLOWED_USER_IDS="123456789"'
)
[IO.File]::WriteAllLines($tempEnv, [string[]]$lines, $utf8NoBom)
$previousEnvFile = $env:APP_ENV_FILE
$secretNames = 'ADMIN_TOKEN', 'TELEGRAM_BOT_TOKEN', 'TELEGRAM_WEBHOOK_SECRET'
$previousProcessValues = @{}
foreach ($name in $secretNames) {
  $previousProcessValues[$name] = [Environment]::GetEnvironmentVariable($name, 'Process')
  [Environment]::SetEnvironmentVariable($name, $null, 'Process')
}
try {
  $env:APP_ENV_FILE = $tempEnv
  $check = @'
from personal_assistant.infrastructure.config import AppSettings
settings = AppSettings.from_env()
assert settings.admin_token == "placeholder-admin-token"
assert settings.telegram_bot_token == "000000:placeholder-bot-token"
assert settings.telegram_webhook_secret == "placeholder-webhook-secret"
print("AppSettings dotenv read passed")
'@
  [IO.File]::WriteAllText($checkFile, $check, $utf8NoBom)
  uv run python $checkFile
} finally {
  $env:APP_ENV_FILE = $previousEnvFile
  foreach ($name in $secretNames) {
    [Environment]::SetEnvironmentVariable($name, $previousProcessValues[$name], 'Process')
  }
  Remove-Item -LiteralPath $tempEnv -Force -ErrorAction SilentlyContinue
  Remove-Item -LiteralPath $checkFile -Force -ErrorAction SilentlyContinue
}
```

Start the server on loopback:

```powershell
$env:APP_ENV_FILE = '.env'
$env:PYTHONPATH = 'src'
uv run python -m uvicorn personal_assistant.infrastructure.http:app --host 127.0.0.1 --port 8000
```

## Exact Proxy Allowlist

Configure the chosen HTTPS proxy/tunnel with the equivalent of this policy;
adapt syntax to the product but do not broaden it:

```text
public listener: HTTPS only
allow: POST /webhooks/telegram -> http://127.0.0.1:8000/webhooks/telegram
deny: every other method and path (404 or 403)
do not forward: /healthz, /readyz, /v1/runtime/*, /admin, /admin/*
```

The tunnel/proxy must terminate or enforce HTTPS at its public listener. Do not
use an unrestricted `http 8000` tunnel configuration: it exposes more than the
webhook. Inspect its request log only for path/method/status; redact headers and
bodies because they can contain secrets and user content.

## Register and Verify the Telegram Webhook

Set the webhook with a public HTTPS URL ending exactly in `/webhooks/telegram`.
The secret is supplied as Telegram's `secret_token` field, never in the URL.
Use a local script or secure terminal workflow that reads the values from the
ignored `.env`; do not paste its expanded command into logs. Verify the public
URL and delivery state with `getWebhookInfo`, without printing secret values.

Expected checks:

- `getWebhookInfo` reports the intended HTTPS URL with no secret in it.
- The proxy accepts `POST /webhooks/telegram` and rejects `GET` on that path.
- The proxy rejects `/healthz`, `/readyz`, `/v1/runtime/reminders`, `/admin`,
  and `/admin/snapshot` from its public address.
- A Telegram test update succeeds only with a configured allowlisted `from.id`.

For the BotFather and Telegram API procedure, see [telegram.md](telegram.md).

## Rotation and Rollback

Rotate after suspected exposure and periodically according to the local
operator's policy. Do not print old or new values while rotating.

1. Generate a new `ADMIN_TOKEN` and `TELEGRAM_WEBHOOK_SECRET` using the
   PowerShell method above, and update only the ignored local `.env` or secret
   manager. Record the change time in a private operational record, not git.
2. Restart the loopback application so it reads the new values. While it is
   restarting, Telegram may retry deliveries; this is a short, expected window.
3. Call Telegram `setWebhook` with the same HTTPS URL and the new
   `secret_token`. Do not place either secret in the URL.
4. Verify `getWebhookInfo`, the proxy's exact allowlist, a permitted Telegram
   test update, and an unauthorized public path. Check only statuses/metadata.
5. For `ADMIN_TOKEN`, verify a loopback runtime/admin request succeeds with
   `Authorization: Bearer <ADMIN_TOKEN>` and fails without it. Do not test via
   the public proxy.
6. Delete the ignored `.env.backup-*` file after verification. It contains the
   old secrets and is only for short rollback recovery.

There is no safe dual-secret acceptance window for the webhook header. If
Telegram has not accepted the replacement configuration or the new deployment
fails verification, roll back by restoring the previously working local secret
configuration, restarting the loopback server, and immediately setting that
previous webhook secret again. Keep the rollback window short; once the new
secret is verified, retire the old values from local storage. If a secret was
exposed, treat rollback only as availability recovery and rotate again to a
fresh value as soon as service is restored.

## Incident Minimum

If a bot token, webhook secret, or admin token appears in a log, shell history,
or committed file: stop public forwarding, rotate the affected value(s),
re-register the Telegram webhook when applicable, remove the exposure through
the approved repository process, and verify the exact proxy deny rules before
re-enabling the public route.
