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
