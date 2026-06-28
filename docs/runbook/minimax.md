# MiniMax Token Plan LLM Runbook

MiniMax Token Plan is integrated as a bounded LLM provider behind
`LLMProvider`. The assistant remains an L2 deterministic workflow: MiniMax is
used only for fallback reminder extraction when deterministic parsing cannot
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
MINIMAX_BASE_URL="https://api.minimaxi.com/anthropic"
MINIMAX_MODEL="MiniMax-M3"
```

`MINIMAX_BASE_URL` and `MINIMAX_MODEL` are optional because the backend defaults
to the values above when `LLM_PROVIDER=minimax`.

Optional Telegram audio replies:

```bash
TTS_PROVIDER="minimax"
TTS_API_KEY="<your-token-plan-subscription-key>"
TTS_BASE_URL="https://api.minimaxi.com"
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

- MiniMax Token Plan subscription keys are distinct from pay-as-you-go API keys.
  They share quota across supported Token Plan resources and can be rate-limited
  by rolling windows.
- Token Plan covers text, image, speech, and music resources under a shared
  usage allowance. For a personal assistant, keep `TELEGRAM_AUDIO_REPLY_MODE` at
  `voice_only` and cap `TTS_MAX_REPLY_CHARACTERS` so voice replies do not
  consume quota for routine text chats.
- MiniMax recommends the Anthropic-compatible protocol for Claude-style tools.
  The documented base URL is `https://api.minimaxi.com/anthropic`.
- MiniMax also exposes an OpenAI-compatible API at `https://api.minimaxi.com/v1`.
  This project uses the Anthropic-compatible path to match the existing
  `LLMProvider` adapter shape and prompt-cache-friendly protocol.
- MiniMax synchronous TTS uses `POST https://api.minimaxi.com/v1/t2a_v2`.
  Non-streaming calls can return hex-encoded audio; the Telegram adapter sends
  the decoded MP3 with `sendAudio`.
- The public API docs list MiniMax speech capabilities as text-to-audio,
  voice-cloning, voice design, and voice management. They do not document an
  OpenAI-style speech-to-text endpoint. Telegram voice messages therefore still
  require `TRANSCRIPTION_PROVIDER=openai_compatible` with a compatible STT
  provider.

## Verification

After placing the key in `.env`, restart the API and send a message that the
deterministic parser cannot handle, for example:

```text
necesito que quede lo de almorzar con Ana a las tres treinta y tres
```

Expected behavior:

1. The reminder workflow writes an `llm.called` trace.
2. If MiniMax returns valid JSON with a confident date/time, the assistant asks
   for calendar approval.
3. Calendar writes still require `/aprobar <id>`.
