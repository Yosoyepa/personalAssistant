Feature: WhatsApp outbound replies and reminder delivery

  Background:
    Given the WhatsApp channel is enabled for outbound delivery
    And the WhatsApp number "<wa_number>" is allowed for tenant "<tenant_id>"

  Scenario Outline: Assistant reply is delivered back over WhatsApp
    When a signed WhatsApp webhook delivers the text "<text>" from "<wa_number>"
    Then the assistant reply is sent to "<wa_number>" through the Graph API
    And the webhook response reports sent "true"

    Examples:
      | wa_number    | tenant_id | text                          |
      | 573001112233 | personal  | remind me to call Ana at 5 pm |
      | 573004445566 | personal  | what do I have for today      |

  Scenario: Due reminder created from WhatsApp is delivered to WhatsApp
    Given a pending reminder for "<wa_number>" that originated from WhatsApp
    When the reminder reaches its due time
    Then the worker delivers the notification through the Graph API
    And the outbox entry is marked "published"

  Scenario: Transient provider error keeps the delivery pending for retry
    Given the Graph API answers with a transient error
    When the worker attempts a due WhatsApp delivery
    Then the outbox entry remains "pending" for retry

  Scenario: Ambiguous provider outcome is reconciled as uncertain
    Given the Graph API connection drops after a delivery attempt
    When the worker cannot confirm the WhatsApp delivery
    Then the outbox entry is marked "uncertain" without leaking message content

  Scenario: Without access token the reply is skipped gracefully
    Given the WhatsApp channel has no access token configured
    When a signed WhatsApp webhook delivers any text
    Then the webhook responds successfully reporting that no reply was sent
