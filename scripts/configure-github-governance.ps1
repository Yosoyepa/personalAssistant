[CmdletBinding()]
param(
    [Parameter()]
    [ValidatePattern('^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$')]
    [string] $Repository = 'Yosoyepa/personalAssistant',

    [Parameter()]
    [ValidatePattern('^[A-Za-z0-9._/-]+$')]
    [string] $Branch = 'main',

    [Parameter()]
    [ValidateSet('Plan', 'Verify', 'Apply')]
    [string] $Mode = 'Plan',

    [Parameter()]
    [switch] $ConfirmRemoteMutation
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$requiredChecks = @(
    'quality'
    'tests (3.11)'
    'tests (3.12)'
    'security'
    'postgres-integration'
)

$repositorySettings = [ordered]@{
    allow_merge_commit     = $true
    allow_squash_merge     = $false
    allow_rebase_merge     = $false
    delete_branch_on_merge = $true
}

# A zero-approval pull-request rule protects main without making a solo
# maintainer approve their own PR. CODEOWNERS still requests @Yosoyepa.
$branchProtection = [ordered]@{
    required_status_checks           = [ordered]@{
        strict   = $true
        contexts = $requiredChecks
    }
    enforce_admins                    = $true
    required_pull_request_reviews    = [ordered]@{
        dismiss_stale_reviews           = $true
        require_code_owner_reviews      = $false
        required_approving_review_count = 0
        require_last_push_approval      = $false
    }
    restrictions                      = $null
    required_linear_history           = $false
    allow_force_pushes                 = $false
    allow_deletions                    = $false
    block_creations                    = $false
    required_conversation_resolution  = $true
    lock_branch                        = $false
    allow_fork_syncing                 = $false
}

function ConvertTo-JsonDocument {
    param([Parameter(Mandatory = $true)] [object] $Value)

    return ($Value | ConvertTo-Json -Depth 20)
}

function Invoke-GitHubJson {
    param(
        [Parameter(Mandatory = $true)] [ValidateSet('GET', 'PATCH', 'PUT')] [string] $Method,
        [Parameter(Mandatory = $true)] [string] $Endpoint,
        [Parameter()] [AllowNull()] [object] $Body
    )

    Get-Command gh -ErrorAction Stop | Out-Null
    $arguments = @(
        'api',
        '--method', $Method,
        '--header', 'Accept: application/vnd.github+json',
        '--header', 'X-GitHub-Api-Version: 2022-11-28',
        $Endpoint
    )

    if ($PSBoundParameters.ContainsKey('Body')) {
        $json = ConvertTo-JsonDocument -Value $Body
        $output = @($json | & gh @arguments --input -)
    }
    else {
        $output = @(& gh @arguments)
    }

    if ($LASTEXITCODE -ne 0) {
        throw "gh api failed with exit code $LASTEXITCODE for $Method $Endpoint"
    }

    $document = ($output -join [Environment]::NewLine).Trim()
    if ([string]::IsNullOrWhiteSpace($document)) {
        return $null
    }
    return ($document | ConvertFrom-Json)
}

function Assert-BooleanSetting {
    param(
        [Parameter(Mandatory = $true)]
        [AllowEmptyCollection()]
        [System.Collections.Generic.List[string]] $Drift,
        [Parameter(Mandatory = $true)] [string] $Name,
        [Parameter(Mandatory = $true)] [bool] $Expected,
        [Parameter(Mandatory = $true)] [bool] $Actual
    )

    if ($Expected -ne $Actual) {
        $Drift.Add("$Name expected $Expected but found $Actual")
    }
}

function Test-DesiredState {
    $repositoryEndpoint = "repos/$Repository"
    $encodedBranch = [Uri]::EscapeDataString($Branch)
    $protectionEndpoint = "$repositoryEndpoint/branches/$encodedBranch/protection"
    $drift = New-Object 'System.Collections.Generic.List[string]'

    $currentRepository = Invoke-GitHubJson -Method GET -Endpoint $repositoryEndpoint
    foreach ($setting in $repositorySettings.Keys) {
        $actual = [bool] $currentRepository.PSObject.Properties[$setting].Value
        Assert-BooleanSetting -Drift $drift -Name "repository.$setting" `
            -Expected ([bool] $repositorySettings[$setting]) -Actual $actual
    }

    $currentProtection = Invoke-GitHubJson -Method GET -Endpoint $protectionEndpoint
    $actualChecks = @($currentProtection.required_status_checks.contexts | Sort-Object)
    $expectedChecks = @($requiredChecks | Sort-Object)
    $checkDifference = @(Compare-Object -ReferenceObject $expectedChecks -DifferenceObject $actualChecks)
    if ($checkDifference.Count -gt 0) {
        $drift.Add(
            "required checks expected [$($expectedChecks -join ', ')] but found [$($actualChecks -join ', ')]"
        )
    }

    Assert-BooleanSetting -Drift $drift -Name 'protection.required_status_checks.strict' `
        -Expected $true -Actual ([bool] $currentProtection.required_status_checks.strict)
    Assert-BooleanSetting -Drift $drift -Name 'protection.enforce_admins' `
        -Expected $true -Actual ([bool] $currentProtection.enforce_admins.enabled)
    Assert-BooleanSetting -Drift $drift -Name 'protection.required_linear_history' `
        -Expected $false -Actual ([bool] $currentProtection.required_linear_history.enabled)
    Assert-BooleanSetting -Drift $drift -Name 'protection.allow_force_pushes' `
        -Expected $false -Actual ([bool] $currentProtection.allow_force_pushes.enabled)
    Assert-BooleanSetting -Drift $drift -Name 'protection.allow_deletions' `
        -Expected $false -Actual ([bool] $currentProtection.allow_deletions.enabled)
    Assert-BooleanSetting -Drift $drift -Name 'protection.block_creations' `
        -Expected $false -Actual ([bool] $currentProtection.block_creations.enabled)
    Assert-BooleanSetting -Drift $drift -Name 'protection.required_conversation_resolution' `
        -Expected $true -Actual ([bool] $currentProtection.required_conversation_resolution.enabled)
    Assert-BooleanSetting -Drift $drift -Name 'protection.lock_branch' `
        -Expected $false -Actual ([bool] $currentProtection.lock_branch.enabled)
    Assert-BooleanSetting -Drift $drift -Name 'protection.allow_fork_syncing' `
        -Expected $false -Actual ([bool] $currentProtection.allow_fork_syncing.enabled)

    $reviews = $currentProtection.required_pull_request_reviews
    if ($null -eq $reviews) {
        $drift.Add('protection.required_pull_request_reviews is not configured')
    }
    else {
        if ([int] $reviews.required_approving_review_count -ne 0) {
            $drift.Add(
                "required approving reviews expected 0 but found $($reviews.required_approving_review_count)"
            )
        }
        Assert-BooleanSetting -Drift $drift -Name 'protection.dismiss_stale_reviews' `
            -Expected $true -Actual ([bool] $reviews.dismiss_stale_reviews)
        Assert-BooleanSetting -Drift $drift -Name 'protection.require_code_owner_reviews' `
            -Expected $false -Actual ([bool] $reviews.require_code_owner_reviews)
        Assert-BooleanSetting -Drift $drift -Name 'protection.require_last_push_approval' `
            -Expected $false -Actual ([bool] $reviews.require_last_push_approval)
    }

    if ($drift.Count -gt 0) {
        Write-Host 'GitHub governance drift detected:' -ForegroundColor Red
        foreach ($item in $drift) {
            Write-Host " - $item" -ForegroundColor Red
        }
        throw "GitHub governance verification failed with $($drift.Count) difference(s)."
    }

    Write-Host "GitHub governance matches the desired state for $Repository ($Branch)." -ForegroundColor Green
}

$plan = [ordered]@{
    repository         = $Repository
    branch             = $Branch
    required_checks    = $requiredChecks
    repository_settings = $repositorySettings
    branch_protection  = $branchProtection
}

switch ($Mode) {
    'Plan' {
        Write-Host 'Plan mode: no GitHub API calls were made.' -ForegroundColor Cyan
        ConvertTo-JsonDocument -Value $plan
    }
    'Verify' {
        Test-DesiredState
    }
    'Apply' {
        if (-not $ConfirmRemoteMutation) {
            throw 'Apply requires -ConfirmRemoteMutation. Run Plan and obtain maintainer approval first.'
        }

        $repositoryEndpoint = "repos/$Repository"
        $encodedBranch = [Uri]::EscapeDataString($Branch)
        $protectionEndpoint = "$repositoryEndpoint/branches/$encodedBranch/protection"

        Invoke-GitHubJson -Method PATCH -Endpoint $repositoryEndpoint -Body $repositorySettings | Out-Null
        Invoke-GitHubJson -Method PUT -Endpoint $protectionEndpoint -Body $branchProtection | Out-Null
        Test-DesiredState
    }
}
