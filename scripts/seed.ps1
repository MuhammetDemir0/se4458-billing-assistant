<#
.SYNOPSIS
  Load the demo data into RDS by running the billing-api image once as an ECS task ("seed" command).
  The database is private, so the seed has to run inside the VPC.
#>
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$out = terraform "-chdir=$(Join-Path $root 'infra/terraform')" output -json | ConvertFrom-Json
$region = $out.region.value
$cluster = $out.ecs_cluster.value
$subnets = ($out.task_subnets.value -join ",")
$sg = $out.billing_security_group.value

$network = "awsvpcConfiguration={subnets=[$subnets],securityGroups=[$sg],assignPublicIp=ENABLED}"
# Passed through a file: Windows PowerShell 5.1 mangles double quotes in native command arguments.
$overridesFile = Join-Path ([IO.Path]::GetTempPath()) "seed-overrides.json"
Set-Content -Path $overridesFile -Encoding ascii -Value '{"containerOverrides":[{"name":"billing-api","command":["seed"]}]}'

$taskArn = aws ecs run-task --cluster $cluster --launch-type FARGATE `
    --task-definition $out.billing_task_definition.value `
    --network-configuration $network --overrides "file://$overridesFile" `
    --region $region --query "tasks[0].taskArn" --output text
if ($LASTEXITCODE -ne 0 -or -not $taskArn -or $taskArn -eq "None") { throw "run-task failed" }

Write-Host "Seed task started: $taskArn" -ForegroundColor Cyan
aws ecs wait tasks-stopped --cluster $cluster --tasks $taskArn --region $region
$exit = aws ecs describe-tasks --cluster $cluster --tasks $taskArn --region $region `
    --query "tasks[0].containers[0].exitCode" --output text
Write-Host "Seed task exit code: $exit (logs: CloudWatch /ecs/billing-assistant/billing-api)"
if ($exit -ne "0") { exit 1 }
