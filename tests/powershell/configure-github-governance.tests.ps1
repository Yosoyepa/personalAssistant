[CmdletBinding()]
param()

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$testsRoot = Join-Path $PSScriptRoot '..'
$repositoryRoot = (Resolve-Path -LiteralPath (Join-Path $testsRoot '..')).Path
$governanceScript = Join-Path (Join-Path $repositoryRoot 'scripts') 'configure-github-governance.ps1'

# Dot-source in Plan mode so the production functions are available without
# requiring gh or making any GitHub API call.
. $governanceScript -Mode Plan *> $null

if ([bool] $branchProtection.lock_branch -or [bool] $branchProtection.allow_fork_syncing) {
    throw 'Unlocked main must keep both lock_branch and allow_fork_syncing disabled.'
}

$emptyDrift = New-Object 'System.Collections.Generic.List[string]'
Assert-BooleanSetting -Drift $emptyDrift -Name 'regression.empty' -Expected $true -Actual $true
if ($emptyDrift.Count -ne 0) {
    throw "An equal setting unexpectedly produced $($emptyDrift.Count) drift item(s)."
}

Assert-BooleanSetting -Drift $emptyDrift -Name 'regression.real' -Expected $true -Actual $false
if ($emptyDrift.Count -ne 1 -or $emptyDrift[0] -notmatch '^regression\.real expected') {
    throw "A real setting difference was not recorded: $($emptyDrift -join ', ')"
}

$script:MockGovernanceDrift = $false

function Invoke-GitHubJson {
    param(
        [Parameter(Mandatory = $true)] [string] $Method,
        [Parameter(Mandatory = $true)] [string] $Endpoint,
        [Parameter()] [AllowNull()] [object] $Body
    )

    if ($Method -ne 'GET') {
        throw "The regression mock only permits read-only GET calls, received $Method."
    }

    if ($Endpoint -eq 'repos/Yosoyepa/personalAssistant') {
        return [pscustomobject]@{
            allow_merge_commit     = $true
            allow_squash_merge     = $script:MockGovernanceDrift
            allow_rebase_merge     = $false
            delete_branch_on_merge = $true
        }
    }

    if ($Endpoint -ne 'repos/Yosoyepa/personalAssistant/branches/main/protection') {
        throw "Unexpected mocked endpoint: $Endpoint"
    }

    return [pscustomobject]@{
        required_status_checks          = [pscustomobject]@{
            strict   = $true
            contexts = @(
                'quality'
                'tests (3.11)'
                'tests (3.12)'
                'security'
                'postgres-integration'
            )
        }
        enforce_admins                   = [pscustomobject]@{ enabled = $true }
        required_linear_history          = [pscustomobject]@{ enabled = $false }
        allow_force_pushes                = [pscustomobject]@{ enabled = $false }
        allow_deletions                   = [pscustomobject]@{ enabled = $false }
        block_creations                   = [pscustomobject]@{ enabled = $false }
        required_conversation_resolution = [pscustomobject]@{ enabled = $true }
        lock_branch                       = [pscustomobject]@{ enabled = $false }
        allow_fork_syncing                = [pscustomobject]@{ enabled = $false }
        required_pull_request_reviews     = [pscustomobject]@{
            required_approving_review_count = 0
            dismiss_stale_reviews           = $true
            require_code_owner_reviews      = $false
            require_last_push_approval      = $false
        }
    }
}

# Verify and the post-Apply verification share Test-DesiredState. An exact
# remote state must leave the drift collection empty and complete successfully.
Test-DesiredState *> $null

# A real mismatch must still be accumulated and reported as a verification
# failure rather than being silently accepted.
$script:MockGovernanceDrift = $true
$driftWasRejected = $false
try {
    Test-DesiredState *> $null
}
catch {
    if ($_.Exception.Message -notmatch 'verification failed with 1 difference') {
        throw
    }
    $driftWasRejected = $true
}

if (-not $driftWasRejected) {
    throw 'A real GitHub governance drift was unexpectedly accepted.'
}

Write-Output 'GitHub governance PowerShell regression checks passed without remote calls.'
