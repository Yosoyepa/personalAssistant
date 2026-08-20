Feature: Admin panel authenticates remote clients only behind explicit opt-in

  Background:
    Given the admin panel is configured with a valid admin token

  Scenario Outline: Access depends on peer, token and remote opt-in
    Given remote admin access is "<remote_flag>"
    When a client from "<peer>" requests "/admin/approvals" with "<credentials>"
    Then the response status is "<status>"

    Examples:
      | remote_flag | peer        | credentials      | status |
      | disabled    | loopback    | valid token      | 200    |
      | disabled    | loopback    | no token         | 401    |
      | disabled    | non-loopback| valid token      | 403    |
      | enabled     | loopback    | valid token      | 200    |
      | enabled     | non-loopback| valid token      | 200    |
      | enabled     | non-loopback| no token         | 401    |
      | enabled     | non-loopback| invalid token    | 401    |

  Scenario Outline: Browser login issues a hardened session cookie
    Given remote admin mode is configured as "enabled"
    When a client from "<peer>" posts the "<token_kind>" token to "/admin/login"
    Then the login response status is "<status>"
    And the outcome cookie is "<cookie_outcome>"

    Examples:
      | peer        | token_kind | status | cookie_outcome |
      | non-loopback| valid      | 200    | set with HttpOnly, Secure, SameSite=Strict, Path=/admin, Max-Age=43200 |
      | non-loopback| invalid    | 401    | absent         |

  Scenario: A valid session cookie authenticates admin requests
    Given remote admin mode is active
    And an operator has logged in from a remote peer
    When the operator requests "/admin/approvals" using the session cookie
    Then the session request returns status 200

  Scenario: Logout revokes the session cookie
    Given an authenticated session exists from a remote client
    When the client posts to "/admin/logout"
    Then the session cookie is cleared
    And requesting "/admin/approvals" with the cleared cookie returns 401

  Scenario Outline: Remote mode refuses weak tokens at startup
    Given remote admin setting is "enabled"
    When the app starts with admin token "<token>"
    Then startup "<outcome>"

    Examples:
      | token              | outcome                                |
      | short              | fails with a clear configuration error |
      | <32+ random chars> | succeeds                               |
