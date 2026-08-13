Feature: Operator resolves uncertain reminder deliveries from the admin panel

  Background:
    Given the admin panel is served on loopback
    And an outbox message "<message_id>" with status "uncertain" exists for tenant "<tenant_id>"

  Scenario Outline: Operator resolves an uncertain delivery from the outbox section
    When the operator confirms resolution "<resolution>" for message "<message_id>" from the outbox section
    Then the runtime endpoint "POST /v1/runtime/outbox/<message_id>/resolve" is called with the operator bearer token
    And the dashboard shows the resulting status "<resulting_status>"

    Examples:
      | message_id | tenant_id | resolution | resulting_status |
      | msg-001    | personal  | delivered  | published        |
      | msg-002    | personal  | retry      | pending          |

  Scenario: Resolution actions are only rendered for uncertain rows
    Given an outbox message "msg-003" has reached its final state "published"
    When the operator opens the admin dashboard
    Then the outbox section shows "msg-003" without resolve actions

  Scenario: Retry beyond the attempt limit is rejected without side effects
    Given message "msg-004" has reached the maximum delivery attempts
    When the operator attempts to retry delivery for message "msg-004"
    Then the dashboard shows an inline error and the message stays "uncertain"
