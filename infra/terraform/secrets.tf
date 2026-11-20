# Secrets live in AWS Secrets Manager and are injected into containers by ECS at start-up.

# Gemini API key: Terraform creates only the empty secret. Put the value yourself so it never enters the
# Terraform state:  scripts/set_gemini_key.ps1
resource "aws_secretsmanager_secret" "gemini_api_key" {
  name                    = "${local.name}/gemini-api-key"
  description             = "Google Gemini API key used by the agent"
  recovery_window_in_days = 0 # course project: allow immediate re-creation after destroy
}

# Shared bearer token between the agent and the MCP server (generated, never typed by a human).
resource "random_password" "mcp_token" {
  length  = 48
  special = false
}

resource "aws_secretsmanager_secret" "mcp_token" {
  name                    = "${local.name}/mcp-token"
  description             = "Bearer token protecting the MCP server"
  recovery_window_in_days = 0
}

resource "aws_secretsmanager_secret_version" "mcp_token" {
  secret_id     = aws_secretsmanager_secret.mcp_token.id
  secret_string = random_password.mcp_token.result
}
