# Amazon Cognito user pool: OAuth 2.0 / OIDC JWT access tokens for subscribers and operators.
# Username = subscriber number. Groups map to roles used by the Billing API and the agent.
# Demo users are created by scripts/create_demo_users.ps1 (passwords are never stored in the repo).

resource "aws_cognito_user_pool" "main" {
  name = "${local.name}-users"

  username_configuration {
    case_sensitive = false
  }

  admin_create_user_config {
    allow_admin_create_user_only = true # no public self sign-up
  }

  password_policy {
    minimum_length    = 10
    require_lowercase = true
    require_uppercase = true
    require_numbers   = true
    require_symbols   = true
  }

  deletion_protection = "INACTIVE"
}

resource "aws_cognito_user_pool_client" "agent" {
  name         = "${local.name}-agent"
  user_pool_id = aws_cognito_user_pool.main.id

  generate_secret               = false # public client used by the agent's sign-in endpoint and the CLI
  explicit_auth_flows           = ["ALLOW_USER_PASSWORD_AUTH", "ALLOW_REFRESH_TOKEN_AUTH"]
  prevent_user_existence_errors = "ENABLED"

  access_token_validity  = 60
  id_token_validity      = 60
  refresh_token_validity = 1
  token_validity_units {
    access_token  = "minutes"
    id_token      = "minutes"
    refresh_token = "days"
  }
}

resource "aws_cognito_user_group" "subscriber" {
  name         = "subscriber"
  user_pool_id = aws_cognito_user_pool.main.id
  description  = "Mobile subscribers: read own bills, pay own bills, use the assistant"
}

resource "aws_cognito_user_group" "admin" {
  name         = "billing-admin"
  user_pool_id = aws_cognito_user_pool.main.id
  description  = "Operators of the mobile provider app: add usage, calculate bills"
}

locals {
  cognito_issuer = "https://cognito-idp.${var.region}.amazonaws.com/${aws_cognito_user_pool.main.id}"
}
