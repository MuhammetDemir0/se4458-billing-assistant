# Three independently deployable services on ECS Fargate, discovered through AWS Cloud Map.
#   billing-api : SRV record  -> target of the API Gateway private integration
#   agent       : SRV record  -> target of the API Gateway private integration
#   mcp-server  : A record    -> reached only by the agent at http://mcp.<namespace>:8000/mcp

locals {
  services = toset(["billing-api", "mcp-server", "agent"])
  ecr_url  = { for s in local.services : s => aws_ecr_repository.svc[s].repository_url }
  health_check = {
    command     = ["CMD-SHELL", "python -c \"import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health')\" || exit 1"]
    interval    = 30
    timeout     = 5
    retries     = 3
    startPeriod = 30
  }
  namespace = "billing.internal"
  log_config = { for s in local.services : s => {
    logDriver = "awslogs"
    options = {
      awslogs-group         = aws_cloudwatch_log_group.svc[s].name
      awslogs-region        = var.region
      awslogs-stream-prefix = s
    }
  } }
  capacity = var.use_fargate_spot ? "FARGATE_SPOT" : "FARGATE"
}

# ---------- images and logs ----------

resource "aws_ecr_repository" "svc" {
  for_each             = local.services
  name                 = "${local.name}/${each.key}"
  image_tag_mutability = "MUTABLE" # :latest is moved on each deploy; a git-SHA tag is pushed as well
  force_delete         = true
  image_scanning_configuration {
    scan_on_push = true
  }
}

resource "aws_ecr_lifecycle_policy" "svc" {
  for_each   = aws_ecr_repository.svc
  repository = each.value.name
  policy = jsonencode({
    rules = [{
      rulePriority = 1
      description  = "Keep the last 10 images"
      selection    = { tagStatus = "any", countType = "imageCountMoreThan", countNumber = 10 }
      action       = { type = "expire" }
    }]
  })
}

resource "aws_cloudwatch_log_group" "svc" {
  for_each          = local.services
  name              = "/ecs/${local.name}/${each.key}"
  retention_in_days = 7
}

# ---------- IAM ----------

data "aws_iam_policy_document" "ecs_tasks_assume" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["ecs-tasks.amazonaws.com"]
    }
  }
}

# Used by ECS itself: pull images, write logs, read the secrets injected as environment variables.
resource "aws_iam_role" "execution" {
  name               = "${local.name}-ecs-execution"
  assume_role_policy = data.aws_iam_policy_document.ecs_tasks_assume.json
}

resource "aws_iam_role_policy_attachment" "execution_managed" {
  role       = aws_iam_role.execution.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AmazonECSTaskExecutionRolePolicy"
}

resource "aws_iam_role_policy" "execution_secrets" {
  name = "read-injected-secrets"
  role = aws_iam_role.execution.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect = "Allow"
      Action = ["secretsmanager:GetSecretValue"]
      Resource = [
        aws_secretsmanager_secret.gemini_api_key.arn,
        aws_secretsmanager_secret.mcp_token.arn,
      ]
    }]
  })
}

# Roles used by the application code (least privilege, one per need).
#   task         : agent and mcp-server; they call no AWS APIs, only ECS Exec for debugging
#   billing_task : billing-api; additionally reads its RDS secret at connection time, because RDS rotates
#                  that password (a value injected once at start-up would break after a rotation)
locals {
  ecs_exec_statement = {
    Effect   = "Allow"
    Action   = ["ssmmessages:CreateControlChannel", "ssmmessages:CreateDataChannel", "ssmmessages:OpenControlChannel", "ssmmessages:OpenDataChannel"]
    Resource = "*"
  }
}

resource "aws_iam_role" "task" {
  name               = "${local.name}-ecs-task"
  assume_role_policy = data.aws_iam_policy_document.ecs_tasks_assume.json
}

resource "aws_iam_role_policy" "task_exec" {
  name   = "ecs-exec"
  role   = aws_iam_role.task.id
  policy = jsonencode({ Version = "2012-10-17", Statement = [local.ecs_exec_statement] })
}

resource "aws_iam_role" "billing_task" {
  name               = "${local.name}-billing-task"
  assume_role_policy = data.aws_iam_policy_document.ecs_tasks_assume.json
}

resource "aws_iam_role_policy" "billing_task" {
  name = "db-secret-and-ecs-exec"
  role = aws_iam_role.billing_task.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      local.ecs_exec_statement,
      {
        Effect   = "Allow"
        Action   = ["secretsmanager:GetSecretValue"]
        Resource = [aws_db_instance.main.master_user_secret[0].secret_arn]
      },
    ]
  })
}

# ---------- service discovery ----------

resource "aws_service_discovery_private_dns_namespace" "main" {
  name = local.namespace
  vpc  = aws_vpc.main.id
}

resource "aws_service_discovery_service" "billing" {
  name = "billing-api"
  dns_config {
    namespace_id   = aws_service_discovery_private_dns_namespace.main.id
    routing_policy = "MULTIVALUE"
    dns_records {
      type = "SRV"
      ttl  = 10
    }
  }
}

resource "aws_service_discovery_service" "agent" {
  name = "agent"
  dns_config {
    namespace_id   = aws_service_discovery_private_dns_namespace.main.id
    routing_policy = "MULTIVALUE"
    dns_records {
      type = "SRV"
      ttl  = 10
    }
  }
}

resource "aws_service_discovery_service" "mcp" {
  name = "mcp"
  dns_config {
    namespace_id   = aws_service_discovery_private_dns_namespace.main.id
    routing_policy = "MULTIVALUE"
    dns_records {
      type = "A"
      ttl  = 10
    }
  }
}

# ---------- cluster and task definitions ----------

resource "aws_ecs_cluster" "main" {
  name = "${local.name}-cluster"
  setting {
    name  = "containerInsights"
    value = "disabled" # cost; CloudWatch Logs are enough for this project
  }
}

resource "aws_ecs_cluster_capacity_providers" "main" {
  cluster_name       = aws_ecs_cluster.main.name
  capacity_providers = ["FARGATE", "FARGATE_SPOT"]
}

resource "aws_ecs_task_definition" "billing" {
  family                   = "${local.name}-billing-api"
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = "256"
  memory                   = "512"
  execution_role_arn       = aws_iam_role.execution.arn
  task_role_arn            = aws_iam_role.billing_task.arn
  runtime_platform {
    operating_system_family = "LINUX"
    cpu_architecture        = "X86_64"
  }
  container_definitions = jsonencode([{
    name         = "billing-api"
    image        = "${local.ecr_url["billing-api"]}:${var.image_tag}"
    essential    = true
    portMappings = [{ containerPort = 8000, protocol = "tcp" }]
    environment = [
      { name = "ENVIRONMENT", value = "aws" },
      { name = "AUTH_MODE", value = "cognito" },
      { name = "COGNITO_REGION", value = var.region },
      { name = "COGNITO_USER_POOL_ID", value = aws_cognito_user_pool.main.id },
      { name = "COGNITO_APP_CLIENT_ID", value = aws_cognito_user_pool_client.agent.id },
      { name = "DB_HOST", value = aws_db_instance.main.address },
      { name = "DB_PORT", value = tostring(aws_db_instance.main.port) },
      { name = "DB_NAME", value = aws_db_instance.main.db_name },
      { name = "DB_SSLMODE", value = "require" },
      # Credentials are read from this (rotated) secret for each new connection; see app/db/credentials.py.
      { name = "DB_SECRET_ARN", value = aws_db_instance.main.master_user_secret[0].secret_arn },
      { name = "AWS_REGION", value = var.region },
    ]
    healthCheck      = local.health_check
    logConfiguration = local.log_config["billing-api"]
  }])
}

resource "aws_ecs_task_definition" "mcp" {
  family                   = "${local.name}-mcp-server"
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = "256"
  memory                   = "512"
  execution_role_arn       = aws_iam_role.execution.arn
  task_role_arn            = aws_iam_role.task.arn
  runtime_platform {
    operating_system_family = "LINUX"
    cpu_architecture        = "X86_64"
  }
  container_definitions = jsonencode([{
    name             = "mcp-server"
    image            = "${local.ecr_url["mcp-server"]}:${var.image_tag}"
    essential        = true
    portMappings     = [{ containerPort = 8000, protocol = "tcp" }]
    secrets          = [{ name = "MCP_TOKEN", valueFrom = aws_secretsmanager_secret.mcp_token.arn }]
    healthCheck      = local.health_check
    logConfiguration = local.log_config["mcp-server"]
  }])
}

resource "aws_ecs_task_definition" "agent" {
  family                   = "${local.name}-agent"
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = "256"
  memory                   = "512"
  execution_role_arn       = aws_iam_role.execution.arn
  task_role_arn            = aws_iam_role.task.arn
  runtime_platform {
    operating_system_family = "LINUX"
    cpu_architecture        = "X86_64"
  }
  container_definitions = jsonencode([{
    name         = "agent"
    image        = "${local.ecr_url["agent"]}:${var.image_tag}"
    essential    = true
    portMappings = [{ containerPort = 8000, protocol = "tcp" }]
    environment = [
      { name = "ENVIRONMENT", value = "aws" },
      { name = "AUTH_MODE", value = "cognito" },
      { name = "COGNITO_REGION", value = var.region },
      { name = "COGNITO_USER_POOL_ID", value = aws_cognito_user_pool.main.id },
      { name = "COGNITO_APP_CLIENT_ID", value = aws_cognito_user_pool_client.agent.id },
      # All REST tool calls go through the public API Gateway endpoint.
      { name = "GATEWAY_URL", value = aws_apigatewayv2_api.main.api_endpoint },
      # MCP is reached privately; it is not behind the gateway.
      { name = "MCP_URL", value = "http://mcp.${local.namespace}:8000/mcp" },
      { name = "GEMINI_MODEL", value = var.gemini_model },
      { name = "GEMINI_FALLBACK_MODEL", value = var.gemini_fallback_model },
    ]
    secrets = [
      { name = "GEMINI_API_KEY", valueFrom = aws_secretsmanager_secret.gemini_api_key.arn },
      { name = "MCP_TOKEN", valueFrom = aws_secretsmanager_secret.mcp_token.arn },
    ]
    healthCheck      = local.health_check
    logConfiguration = local.log_config["agent"]
  }])
}

# ---------- services ----------

resource "aws_ecs_service" "billing" {
  name                   = "billing-api"
  cluster                = aws_ecs_cluster.main.id
  task_definition        = aws_ecs_task_definition.billing.arn
  desired_count          = var.desired_count
  enable_execute_command = true
  deployment_circuit_breaker {
    enable   = true # a new task set that cannot become healthy is rolled back automatically
    rollback = true
  }
  capacity_provider_strategy {
    capacity_provider = local.capacity
    weight            = 1
  }
  network_configuration {
    subnets          = aws_subnet.public[*].id
    security_groups  = [aws_security_group.billing.id]
    assign_public_ip = true
  }
  service_registries {
    registry_arn   = aws_service_discovery_service.billing.arn
    container_name = "billing-api"
    container_port = 8000
  }
  depends_on = [aws_ecs_cluster_capacity_providers.main]
}

resource "aws_ecs_service" "mcp" {
  name                   = "mcp-server"
  cluster                = aws_ecs_cluster.main.id
  task_definition        = aws_ecs_task_definition.mcp.arn
  desired_count          = var.desired_count
  enable_execute_command = true
  deployment_circuit_breaker {
    enable   = true # a new task set that cannot become healthy is rolled back automatically
    rollback = true
  }
  capacity_provider_strategy {
    capacity_provider = local.capacity
    weight            = 1
  }
  network_configuration {
    subnets          = aws_subnet.public[*].id
    security_groups  = [aws_security_group.mcp.id]
    assign_public_ip = true
  }
  service_registries {
    registry_arn = aws_service_discovery_service.mcp.arn
  }
  depends_on = [aws_ecs_cluster_capacity_providers.main]
}

resource "aws_ecs_service" "agent" {
  name                   = "agent"
  cluster                = aws_ecs_cluster.main.id
  task_definition        = aws_ecs_task_definition.agent.arn
  desired_count          = var.desired_count
  enable_execute_command = true
  deployment_circuit_breaker {
    enable   = true # a new task set that cannot become healthy is rolled back automatically
    rollback = true
  }
  capacity_provider_strategy {
    capacity_provider = local.capacity
    weight            = 1
  }
  network_configuration {
    subnets          = aws_subnet.public[*].id
    security_groups  = [aws_security_group.agent.id]
    assign_public_ip = true
  }
  service_registries {
    registry_arn   = aws_service_discovery_service.agent.arn
    container_name = "agent"
    container_port = 8000
  }
  depends_on = [aws_ecs_cluster_capacity_providers.main]
}
