Feature: WhatsApp inbound webhook processes text messages

  Background:
    Given the WhatsApp channel is enabled with verify token "<verify_token>"
    And the WhatsApp number "<wa_number>" is allowed for tenant "<tenant_id>"

  Scenario: Meta webhook handshake succeeds with the configured verify token
    When Meta requests webhook verification with the configured token
    Then the endpoint echoes the challenge as plain text

  Scenario: Meta webhook handshake is rejected with an unknown verify token
    When Meta requests webhook verification with token "wrong-token"
    Then the endpoint rejects the verification request

  Scenario Outline: Signed inbound text message is handled as a command
    When a signed WhatsApp webhook delivers the text "<text>" from "<wa_number>"
    Then the message is handled for tenant "<tenant_id>"
    And the response carries the assistant reply with sent flag "false"

    Examples:
      | wa_number    | tenant_id | text                          |
      | 573001112233 | personal  | remind me to call Ana at 5 pm |
      | 573004445566 | personal  | what do I have for today      |

  Scenario: Webhook with invalid signature is rejected without side effects
    When a WhatsApp webhook arrives with an invalid signature
    Then the endpoint rejects it and no reminder, reply, or outbox entry is created

  Scenario: Status-only callbacks are acknowledged as no-ops
    When a signed WhatsApp webhook contains only delivery statuses
    Then the endpoint acknowledges it without handling any command

  Scenario: Replayed delivery of the same message creates no duplicate
    When a signed WhatsApp webhook delivers message "<message_id>" a second time
    Then the endpoint acknowledges it and the reminder exists exactly once

  Scenario: Webhook stays closed while the channel is disabled
    Given the WhatsApp channel is disabled
    When a signed WhatsApp webhook delivers any text
    Then the endpoint responds that the channel is unavailable

  Scenario Outline: Signed voice note is transcribed and handled as a reminder
    When a signed WhatsApp webhook delivers a "<media_kind>" audio of <size_kb> KB from "<wa_number>"
    Then the audio is downloaded and transcribed for tenant "<tenant_id>"
    And the transcribed text is handled by the conversation pipeline
    And the voice response carries the assistant reply with sent flag "false"

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
    Then no media download or transcription is attempted
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
    Then the endpoint acknowledges the audio replay and the reminder exists exactly once


