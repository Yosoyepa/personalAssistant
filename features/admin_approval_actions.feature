Feature: Operator resolves pending approvals from the admin panel

  Background:
    Given the admin panel is served on loopback
    And a pending approval "<approval_id>" exists for tenant "<tenant_id>"

  Scenario Outline: Operator resolves a pending approval from the dashboard
    When the operator confirms "<decision>" for approval "<approval_id>" from the approvals section
    Then the runtime endpoint "POST /v1/runtime/approvals/<approval_id>/<decision>" is called with the operator bearer token
    And the dashboard shows the resulting status "<resulting_status>"
    And the approvals section no longer offers actions for "<approval_id>"

    Examples:
      | approval_id | tenant_id | decision | resulting_status |
      | ap-001      | personal  | approve  | approved         |
      | ap-002      | personal  | reject   | rejected         |

  Scenario Outline: Decision is rejected when the approval is no longer pending
    Given approval "<approval_id>" is already "<prior_status>"
    When the operator confirms "<decision>" for approval "<approval_id>"
    Then the dashboard shows the conflict message "<conflict_message>"
    And no calendar write or notification is scheduled

    Examples:
      | approval_id | prior_status | decision | conflict_message              |
      | ap-003      | rejected     | approve  | approval was already rejected |
      | ap-004      | approved     | reject   | approval was already approved |

  Scenario: Actions are never rendered for non-pending approvals
    Given approval "ap-005" has reached its final state "approved"
    When the operator opens the admin dashboard
    Then the approvals section shows "ap-005" without approve or reject actions
