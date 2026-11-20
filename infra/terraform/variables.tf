variable "project" {
  description = "Name prefix for all resources"
  type        = string
  default     = "billing-assistant"
}

variable "region" {
  description = "AWS region (Frankfurt is closest to Turkey)"
  type        = string
  default     = "eu-central-1"
}

variable "image_tag" {
  description = "Container image tag deployed by the ECS services (scripts/deploy.ps1 pushes :latest and a git SHA)"
  type        = string
  default     = "latest"
}

variable "gemini_model" {
  description = "Primary Gemini model used by the agent"
  type        = string
  default     = "gemini-3.6-flash"
}

variable "gemini_fallback_model" {
  description = "Gemini model used when the primary one is overloaded or rate-limited (empty = none)"
  type        = string
  default     = "gemini-3.5-flash-lite"
}

variable "desired_count" {
  description = "Tasks per ECS service (1 keeps the cost low; set 0 to pause without destroying)"
  type        = number
  default     = 1
}

variable "use_fargate_spot" {
  description = "Run tasks on Fargate Spot (~70% cheaper, may be interrupted)"
  type        = bool
  default     = false
}

variable "alert_email" {
  description = "E-mail address for AWS Budgets cost alerts (set in terraform.tfvars, not committed)"
  type        = string
}

variable "monthly_budget_usd" {
  description = "Monthly cost budget; alerts at 50% and 100% (actual) and 100% (forecast)"
  type        = number
  default     = 25
}
