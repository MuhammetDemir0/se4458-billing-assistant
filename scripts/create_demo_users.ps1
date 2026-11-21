<#
.SYNOPSIS
  Create the demo users in Amazon Cognito with permanent passwords you choose (nothing is stored in the repo).
    5551000001, 5551000002  -> group "subscriber"
    ops-admin               -> group "billing-admin"
  Password policy: at least 10 characters with upper case, lower case, digit and symbol.
  Safe to run again: existing users are kept and only their password and group are (re)applied.
#>
# Native commands are checked through $LASTEXITCODE. "Stop" is not used because Windows PowerShell 5.1
# turns any stderr line of a native command into a terminating error and hides the real message.
$ErrorActionPreference = "Continue"
$root = Split-Path -Parent $PSScriptRoot
$out = terraform "-chdir=$(Join-Path $root 'infra/terraform')" output -json | ConvertFrom-Json
$pool = $out.cognito_user_pool_id.value
$region = $out.region.value

function Read-Plain([string]$prompt) {
    $secure = Read-Host -AsSecureString $prompt
    return [Runtime.InteropServices.Marshal]::PtrToStringAuto([Runtime.InteropServices.Marshal]::SecureStringToBSTR($secure))
}

function Invoke-Aws([string[]]$arguments) {
    $output = & aws @arguments 2>&1 | ForEach-Object { "$_" }
    return @{ ok = ($LASTEXITCODE -eq 0); text = ($output -join " ") }
}

$subscriberPassword = Read-Plain "Password for the demo subscribers"
$adminPassword = Read-Plain "Password for ops-admin"

$users = @(
    @{ name = "5551000001"; group = "subscriber"; password = $subscriberPassword },
    @{ name = "5551000002"; group = "subscriber"; password = $subscriberPassword },
    @{ name = "ops-admin"; group = "billing-admin"; password = $adminPassword }
)

$failed = 0
foreach ($u in $users) {
    $exists = Invoke-Aws @("cognito-idp", "admin-get-user", "--user-pool-id", $pool, "--username", $u.name, "--region", $region)
    if (-not $exists.ok) {
        $created = Invoke-Aws @("cognito-idp", "admin-create-user", "--user-pool-id", $pool, "--username", $u.name,
                                "--message-action", "SUPPRESS", "--region", $region)
        if (-not $created.ok) { Write-Host "Could not create $($u.name): $($created.text)" -ForegroundColor Red; $failed++; continue }
    }

    $pw = Invoke-Aws @("cognito-idp", "admin-set-user-password", "--user-pool-id", $pool, "--username", $u.name,
                       "--password", $u.password, "--permanent", "--region", $region)
    if (-not $pw.ok) {
        Write-Host "Password rejected for $($u.name): $($pw.text)" -ForegroundColor Red
        Write-Host "  Rule: 10+ characters with an upper-case letter, a lower-case letter, a digit and a symbol." -ForegroundColor Yellow
        $failed++; continue
    }

    $grp = Invoke-Aws @("cognito-idp", "admin-add-user-to-group", "--user-pool-id", $pool, "--username", $u.name,
                        "--group-name", $u.group, "--region", $region)
    if (-not $grp.ok) { Write-Host "Could not add $($u.name) to $($u.group): $($grp.text)" -ForegroundColor Red; $failed++; continue }

    Write-Host "User $($u.name) ready (group $($u.group))" -ForegroundColor Green
}
if ($failed) { Write-Host "$failed user(s) need attention; fix the issue above and run the script again." -ForegroundColor Yellow; exit 1 }
