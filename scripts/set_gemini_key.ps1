<#
.SYNOPSIS
  Store the Gemini API key in AWS Secrets Manager (typed hidden; never written to disk or Terraform state),
  then restart the agent so it picks up the new value.
#>
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$out = terraform "-chdir=$(Join-Path $root 'infra/terraform')" output -json | ConvertFrom-Json
$secure = Read-Host -AsSecureString "Gemini API key"
$key = [Runtime.InteropServices.Marshal]::PtrToStringAuto([Runtime.InteropServices.Marshal]::SecureStringToBSTR($secure))

aws secretsmanager put-secret-value --secret-id $out.gemini_secret_name.value --secret-string $key --region $out.region.value | Out-Null
if ($LASTEXITCODE -ne 0) { throw "Could not store the secret" }
aws ecs update-service --cluster $out.ecs_cluster.value --service agent --force-new-deployment --region $out.region.value | Out-Null
Write-Host "Gemini key stored; agent service restarting." -ForegroundColor Green
