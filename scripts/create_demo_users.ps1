<#
.SYNOPSIS
  Create the demo users in Amazon Cognito with permanent passwords you choose (nothing is stored in the repo).
    5551000001, 5551000002  -> group "subscriber"
    ops-admin               -> group "billing-admin"
  Password policy: at least 10 characters with upper case, lower case, digit and symbol.
#>
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$out = terraform "-chdir=$(Join-Path $root 'infra/terraform')" output -json | ConvertFrom-Json
$pool = $out.cognito_user_pool_id.value
$region = $out.region.value

function Read-Plain([string]$prompt) {
    $secure = Read-Host -AsSecureString $prompt
    return [Runtime.InteropServices.Marshal]::PtrToStringAuto([Runtime.InteropServices.Marshal]::SecureStringToBSTR($secure))
}

$subscriberPassword = Read-Plain "Password for the demo subscribers"
$adminPassword = Read-Plain "Password for ops-admin"

$users = @(
    @{ name = "5551000001"; group = "subscriber"; password = $subscriberPassword },
    @{ name = "5551000002"; group = "subscriber"; password = $subscriberPassword },
    @{ name = "ops-admin"; group = "billing-admin"; password = $adminPassword }
)

foreach ($u in $users) {
    aws cognito-idp admin-create-user --user-pool-id $pool --username $u.name --message-action SUPPRESS --region $region 2>$null | Out-Null
    aws cognito-idp admin-set-user-password --user-pool-id $pool --username $u.name --password $u.password --permanent --region $region
    if ($LASTEXITCODE -ne 0) { throw "Could not set the password for $($u.name) (check the password policy)" }
    aws cognito-idp admin-add-user-to-group --user-pool-id $pool --username $u.name --group-name $u.group --region $region
    Write-Host "User $($u.name) ready (group $($u.group))" -ForegroundColor Green
}
