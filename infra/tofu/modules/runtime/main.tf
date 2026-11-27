variable "name"  { type = string }
variable "image_api"    { type = string }
variable "image_worker" { type = string }
variable "environment"  { type = map(string) }
variable "secret_arns"  { type = map(string) } # name -> secrets-manager ARN (injected as env, never baked in)

resource "aws_ecs_cluster" "this" { name = var.name }

locals {
  secrets = [for k, v in var.secret_arns : { name = k, valueFrom = v }]
  env     = [for k, v in var.environment : { name = k, value = v }]
}

resource "aws_ecs_task_definition" "api" {
  family                   = "${var.name}-api"
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = 512
  memory                   = 1024
  container_definitions = jsonencode([{
    name = "api", image = var.image_api, essential = true,
    portMappings = [{ containerPort = 8000 }], environment = local.env, secrets = local.secrets,
    readonlyRootFilesystem = true, user = "10001",
    healthCheck = { command = ["CMD-SHELL", "python -c \"import urllib.request as u;u.urlopen('http://127.0.0.1:8000/healthz',timeout=3)\""] }
  }])
}
resource "aws_ecs_task_definition" "worker" {
  family                   = "${var.name}-worker"
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = 1024
  memory                   = 2048
  container_definitions = jsonencode([{ name = "worker", image = var.image_worker, essential = true, environment = local.env, secrets = local.secrets, user = "10001" }])
}
