output "gateway_url" {
  description = "Public API Gateway base URL"
  value       = aws_apigatewayv2_api.main.api_endpoint
}

output "agent_ui_url" {
  value = "${aws_apigatewayv2_api.main.api_endpoint}/agent/chat"
}

output "swagger_url" {
  value = "${aws_apigatewayv2_api.main.api_endpoint}/api/v1/docs"
}

output "region" {
  value = var.region
}

output "cognito_user_pool_id" {
  value = aws_cognito_user_pool.main.id
}

output "cognito_app_client_id" {
  value = aws_cognito_user_pool_client.agent.id
}

output "ecr_repositories" {
  value = local.ecr_url
}

output "ecs_cluster" {
  value = aws_ecs_cluster.main.name
}

output "ecs_services" {
  value = [aws_ecs_service.billing.name, aws_ecs_service.mcp.name, aws_ecs_service.agent.name]
}

output "billing_task_definition" {
  value = aws_ecs_task_definition.billing.family
}

output "task_subnets" {
  value = aws_subnet.public[*].id
}

output "billing_security_group" {
  value = aws_security_group.billing.id
}

output "gemini_secret_name" {
  value = aws_secretsmanager_secret.gemini_api_key.name
}

output "rds_endpoint" {
  description = "Private endpoint (not reachable from the internet)"
  value       = aws_db_instance.main.address
}
