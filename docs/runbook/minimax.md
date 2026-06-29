# MiniMax Token Plan LLM Runbook

MiniMax Token Plan is integrated as a bounded LLM provider behind
`LLMProvider`. The assistant remains an L2 deterministic workflow: MiniMax is
used only for structured intent routing when deterministic command rules do not
match, and for fallback reminder extraction when deterministic parsing cannot
extract a clear date/time.

MiniMax can also be used as an optional text-to-speech provider for Telegram
audio replies. TTS remains an outbound adapter: workflows still produce text,
then infrastructure may synthesize a short audio copy when local policy allows
it.

## Configuration

Use a local ignored `.env` file:

```bash
LLM_PROVIDER="minimax"
MINIMAX_API_KEY="<your-token-plan-subscription-key>"
MINIMAX_BASE_URL="https://api.minimax.io/anthropic"
MINIMAX_MODEL="MiniMax-M3"
```

`MINIMAX_BASE_URL` and `MINIMAX_MODEL` are optional because the backend defaults
to the values above when `LLM_PROVIDER=minimax`.

Optional Telegram audio replies:

```bash
TTS_PROVIDER="minimax"
TTS_API_KEY="<your-token-plan-subscription-key>"
TTS_BASE_URL="https://api.minimax.io"
TTS_MODEL="speech-2.8-turbo"
TTS_VOICE_ID="male-qn-qingse"
TTS_AUDIO_FORMAT="mp3"
TTS_LANGUAGE_BOOST="Spanish"
TTS_MAX_REPLY_CHARACTERS="280"
TELEGRAM_AUDIO_REPLY_MODE="voice_only"
```

`TTS_API_KEY` is optional when `MINIMAX_API_KEY` is already present because the
runtime reads the MiniMax key as a fallback.

## Source Notes

- Official MiniMax sources used for this runbook:
  <https://platform.minimax.io/docs/token-plan/quickstart>,
  <https://platform.minimax.io/docs/token-plan/other-tools>,
  <https://platform.minimax.io/docs/api-reference/text-chat-anthropic>,
  <https://platform.minimax.io/docs/api-reference/text-openai-api>, and
  <https://platform.minimax.io/docs/api-reference/speech-t2a-http>.
- MiniMax Token Plan subscription keys are distinct from pay-as-you-go API keys.
  They share quota across supported Token Plan resources and can be rate-limited
  by rolling windows.
- Token Plan covers text, image, speech, and music resources under a shared
  usage allowance. For a personal assistant, keep `TELEGRAM_AUDIO_REPLY_MODE` at
  `voice_only` and cap `TTS_MAX_REPLY_CHARACTERS` so voice replies do not
  consume quota for routine text chats.
- MiniMax documents `https://api.minimax.io/anthropic` as the international
  Token Plan Anthropic-compatible base URL for Claude-style tools.
- MiniMax also exposes an OpenAI-compatible API at `https://api.minimax.io/v1`.
  This project uses the Anthropic-compatible path to match the existing
  `LLMProvider` adapter shape and prompt-cache-friendly protocol.
- MiniMax synchronous TTS uses `POST https://api.minimax.io/v1/t2a_v2`.
  Non-streaming calls can return hex-encoded audio; the Telegram adapter sends
  the decoded MP3 with `sendAudio`.
- The public API docs list MiniMax speech capabilities as text-to-audio,
  voice-cloning, voice design, and voice management. They do not document an
  OpenAI-style speech-to-text endpoint. Telegram voice messages therefore still
  require `TRANSCRIPTION_PROVIDER=openai_compatible` with a compatible STT
  provider.

## Verification

After placing the key in `.env`, restart the API and send a message that the
deterministic router or parser cannot handle, for example:

```text
necesito que quede lo de almorzar con Ana a las tres treinta y tres
```

For Telegram-style relative reminders, this is also supported deterministically:

```text
recuérdame en 2 minutos pagar el arriendo
```

Relative reminders notify at the requested time. `REMINDER_MINUTES_BEFORE`
applies only when the user creates a calendar event at a specific date/time and
expects an advance notice.

Expected behavior:

1. If deterministic routing fails, MiniMax returns a `conversation_intent`
   structured result from a closed intent set.
2. If deterministic extraction fails, the reminder workflow writes an
   `llm.called` trace.
3. If MiniMax returns valid JSON with a confident date/time, the assistant asks
   for calendar approval.
4. Calendar writes still require `/aprobar <id>`.
