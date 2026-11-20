terraform {
  required_version = ">= 1.6"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 6.0"
    }
    random = {
      source  = "hashicorp/random"
      version = "~> 3.6"
    }
  }

  # Local state for a single-person course project (the state file is git-ignored).
  # A team would use an S3 backend with state locking instead.
}

provider "aws" {
  region = var.region

  default_tags {
    tags = {
      Project   = var.project
      Course    = "SE4458"
      ManagedBy = "terraform"
    }
  }
}

data "aws_availability_zones" "available" {
  state = "available"
}

data "aws_caller_identity" "current" {}

locals {
  name = var.project
  azs  = slice(data.aws_availability_zones.available.names, 0, 2)
}
