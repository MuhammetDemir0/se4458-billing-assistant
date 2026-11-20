# AWS API Gateway (HTTP API): the only public entry point.
#   * versioned public routes under /api/v1 (Billing API) and /agent (assistant)
#   * JWT authorizer (Cognito) on every protected route
#   * throttling on the default stage, stricter on public routes
#   * private integration through a VPC link to the ECS services (Cloud Map), so the services
#     themselves have no public endpoint

resource "aws_apigatewayv2_api" "main" {
  name          = "${local.name}-gateway"
  protocol_type = "HTTP"
  description   = "Public entry point for the Billing API and the AI agent"
}

resource "aws_apigatewayv2_vpc_link" "main" {
  name               = "${local.name}-vpclink"
  subnet_ids         = aws_subnet.public[*].id
  security_group_ids = [aws_security_group.vpclink.id]
}

resource "aws_apigatewayv2_integration" "billing" {
  api_id                 = aws_apigatewayv2_api.main.id
  integration_type       = "HTTP_PROXY"
  integration_method     = "ANY"
  connection_type        = "VPC_LINK"
  connection_id          = aws_apigatewayv2_vpc_link.main.id
  integration_uri        = aws_service_discovery_service.billing.arn
  payload_format_version = "1.0"
  timeout_milliseconds   = 29000
}

resource "aws_apigatewayv2_integration" "agent" {
  api_id                 = aws_apigatewayv2_api.main.id
  integration_type       = "HTTP_PROXY"
  integration_method     = "ANY"
  connection_type        = "VPC_LINK"
  connection_id          = aws_apigatewayv2_vpc_link.main.id
  integration_uri        = aws_service_discovery_service.agent.arn
  payload_format_version = "1.0"
  timeout_milliseconds   = 29000 # LLM + tool round trips must finish within the HTTP API limit
}

resource "aws_apigatewayv2_authorizer" "cognito" {
  api_id           = aws_apigatewayv2_api.main.id
  name             = "cognito-jwt"
  authorizer_type  = "JWT"
  identity_sources = ["$request.header.Authorization"]
  jwt_configuration {
    issuer   = local.cognito_issuer
    audience = [aws_cognito_user_pool_client.agent.id] # matched against aud or client_id (access tokens)
  }
}

locals {
  bill = "/api/v1/subscribers/{subscriberNo}/bills/{year}/{month}"

  # route key => [integration, protected?]
  routes = {
    # ----- Billing API -----
    "GET ${local.bill}"                             = ["billing", false] # Query Bill: public by spec
    "GET ${local.bill}/details"                     = ["billing", true]  # Query Bill Detailed
    "POST ${local.bill}/payments"                   = ["billing", true]  # Pay Bill
    "POST ${local.bill}/calculate"                  = ["billing", true]  # Calculate Bill (admin)
    "POST /api/v1/subscribers/{subscriberNo}/usage" = ["billing", true]  # Add Usage (admin)
    "GET /api/v1/health"                            = ["billing", false]
    "GET /api/v1/docs"                              = ["billing", false] # Swagger UI
    "GET /api/v1/openapi.json"                      = ["billing", false]
    # ----- Agent -----
    "GET /agent"                                  = ["agent", false] # redirects to /agent/chat
    "GET /agent/chat"                             = ["agent", false] # web chat
    "GET /agent/v1/health"                        = ["agent", false]
    "POST /agent/v1/auth/login"                   = ["agent", false]
    "POST /agent/v1/chat"                         = ["agent", true]
    "POST /agent/v1/payments/{intentId}/{action}" = ["agent", true] # confirm / cancel
  }
  integrations = {
    billing = aws_apigatewayv2_integration.billing.id
    agent   = aws_apigatewayv2_integration.agent.id
  }
}

resource "aws_apigatewayv2_route" "all" {
  for_each           = local.routes
  api_id             = aws_apigatewayv2_api.main.id
  route_key          = each.key
  target             = "integrations/${local.integrations[each.value[0]]}"
  authorization_type = each.value[1] ? "JWT" : "NONE"
  authorizer_id      = each.value[1] ? aws_apigatewayv2_authorizer.cognito.id : null
}

resource "aws_cloudwatch_log_group" "gateway" {
  name              = "/aws/apigateway/${local.name}"
  retention_in_days = 7
}

resource "aws_apigatewayv2_stage" "default" {
  api_id      = aws_apigatewayv2_api.main.id
  name        = "$default"
  auto_deploy = true

  default_route_settings {
    throttling_burst_limit = 20
    throttling_rate_limit  = 10
  }

  # Stricter limits for unauthenticated routes.
  route_settings {
    route_key              = "GET ${local.bill}"
    throttling_burst_limit = 10
    throttling_rate_limit  = 5
  }
  route_settings {
    route_key              = "POST /agent/v1/auth/login"
    throttling_burst_limit = 5
    throttling_rate_limit  = 2
  }

  access_log_settings {
    destination_arn = aws_cloudwatch_log_group.gateway.arn
    format = jsonencode({
      requestId        = "$context.requestId"
      ip               = "$context.identity.sourceIp"
      routeKey         = "$context.routeKey"
      status           = "$context.status"
      latencyMs        = "$context.responseLatency"
      integrationError = "$context.integrationErrorMessage"
      authorizerError  = "$context.authorizer.error"
      user             = "$context.authorizer.claims.username"
    })
  }

  depends_on = [aws_apigatewayv2_route.all]
}
