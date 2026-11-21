<#
.SYNOPSIS
  Build the three service images, push them to Amazon ECR and roll the ECS services.
.EXAMPLE
  ./scripts/deploy.ps1                 # all services
  ./scripts/deploy.ps1 -Services agent # one service
#>
param(
    [string[]]$Services = @("billing-api", "mcp-server", "agent")
)
$ErrorActionPreference = "Stop"
# "powershell -File deploy.ps1 -Services a,b" passes one string "a,b"; accept both forms.
$Services = @($Services | ForEach-Object { $_ -split "," } | ForEach-Object { $_.Trim() } | Where-Object { $_ })
$known = @("billing-api", "mcp-server", "agent")
$unknown = $Services | Where-Object { $known -notcontains $_ }
if ($unknown) { throw "Unknown service(s): $($unknown -join ', '). Use: $($known -join ', ')" }
$root = Split-Path -Parent $PSScriptRoot
$tf = Join-Path $root "infra/terraform"

$out = terraform "-chdir=$tf" output -json | ConvertFrom-Json
$region = $out.region.value
$cluster = $out.ecs_cluster.value
$registry = ($out.ecr_repositories.value.'billing-api' -split "/")[0]
$tag = (git -C $root rev-parse --short HEAD 2>$null)
if (-not $tag) { $tag = Get-Date -Format "yyyyMMddHHmmss" }

Write-Host "Logging in to $registry" -ForegroundColor Cyan
# Piped through cmd: Windows PowerShell 5.1 re-encodes text piped between native commands,
# which corrupts the long ECR token (docker answers "400 Bad Request").
cmd /c "aws ecr get-login-password --region $region | docker login --username AWS --password-stdin $registry"
if ($LASTEXITCODE -ne 0) { throw "ECR login failed" }

foreach ($svc in $Services) {
    $repo = $out.ecr_repositories.value.$svc
    Write-Host "Building $svc -> ${repo}:$tag" -ForegroundColor Cyan
    docker build --platform linux/amd64 -t "${repo}:$tag" -t "${repo}:latest" (Join-Path $root "services/$svc")
    if ($LASTEXITCODE -ne 0) { throw "docker build failed for $svc" }
    foreach ($ref in @("${repo}:$tag", "${repo}:latest")) {
        docker push $ref
        if ($LASTEXITCODE -ne 0) { throw "docker push failed for $ref" }
    }
    aws ecs update-service --cluster $cluster --service $svc --force-new-deployment --region $region | Out-Null
}

Write-Host "Waiting for services to become stable (a few minutes)..." -ForegroundColor Cyan
aws ecs wait services-stable --cluster $cluster --services $Services --region $region
if ($LASTEXITCODE -ne 0) { throw "Services did not stabilise; check CloudWatch Logs /ecs/billing-assistant/*" }
Write-Host "Deployed image tag $tag" -ForegroundColor Green
Write-Host "Agent UI : $($out.agent_ui_url.value)"
Write-Host "Swagger  : $($out.swagger_url.value)"
