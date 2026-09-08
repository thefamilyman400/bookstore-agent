#!/usr/bin/env bash
# =============================================================================
# deploy/ecs_deploy.sh
#
# Registers a new ECS task definition revision and creates or updates the
# Fargate service to run it behind the ALB.
#
# Must be run AFTER:
#   - deploy/infra.sh        (writes deploy/.env.infra)
#   - deploy/ecr_push.sh     (image exists in ECR)
#
# Usage:
#   ./deploy/ecs_deploy.sh
#   IMAGE_TAG=v1.2.3 ./deploy/ecs_deploy.sh   # pin a specific image tag
# =============================================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
INFRA_ENV="${SCRIPT_DIR}/.env.infra"

if [[ ! -f "${INFRA_ENV}" ]]; then
  echo "❌  ${INFRA_ENV} not found. Run deploy/infra.sh first."
  exit 1
fi

# shellcheck source=deploy/.env.infra
source "${INFRA_ENV}"

# ── Configuration ─────────────────────────────────────────────────────────────
APP_NAME="bookstore-agent"
SERVICE_NAME="${APP_NAME}"
IMAGE_TAG="${IMAGE_TAG:-latest}"
FULL_IMAGE="${ECR_REPO_URI}:${IMAGE_TAG}"
DESIRED_COUNT="${DESIRED_COUNT:-1}"

echo "========================================================"
echo "  Bookstore Agent — ECS Deploy"
echo "  Cluster : ${ECS_CLUSTER}"
echo "  Image   : ${FULL_IMAGE}"
echo "========================================================"

# ── 1. Build task definition JSON from live infrastructure values ──────────────
section() { echo ""; echo "▶ $*"; }

section "Generating task definition"
TASK_DEF_JSON=$(cat <<EOF
{
  "family": "${APP_NAME}",
  "networkMode": "awsvpc",
  "requiresCompatibilities": ["FARGATE"],
  "cpu": "512",
  "memory": "1024",
  "executionRoleArn": "${EXEC_ROLE_ARN}",
  "taskRoleArn": "${TASK_ROLE_ARN}",
  "containerDefinitions": [
    {
      "name": "${APP_NAME}",
      "image": "${FULL_IMAGE}",
      "essential": true,
      "portMappings": [
        {
          "containerPort": 8000,
          "protocol": "tcp"
        }
      ],
      "environment": [
        { "name": "AWS_DEFAULT_REGION", "value": "${AWS_REGION}" },
        { "name": "DB_PATH",            "value": "/data/db/bookstore.db" }
      ],
      "secrets": [
        {
          "name":      "JWT_SECRET",
          "valueFrom": "${APP_SECRET_ARN}:JWT_SECRET::"
        },
        {
          "name":      "GROQ_API_KEY",
          "valueFrom": "${GROQ_SECRET_ARN}:GROQ_API_KEY::"
        },
        {
          "name":      "GROQ_MODEL",
          "valueFrom": "${APP_SECRET_ARN}:GROQ_MODEL::"
        }
      ],
      "mountPoints": [
        {
          "sourceVolume":  "bookstore-db",
          "containerPath": "/data/db",
          "readOnly": false
        }
      ],
      "logConfiguration": {
        "logDriver": "awslogs",
        "options": {
          "awslogs-group":         "${LOG_GROUP}",
          "awslogs-region":        "${AWS_REGION}",
          "awslogs-stream-prefix": "ecs"
        }
      },
      "healthCheck": {
        "command":     ["CMD-SHELL", "curl -f http://localhost:8000/api/info || exit 1"],
        "interval":    30,
        "timeout":     5,
        "retries":     3,
        "startPeriod": 15
      }
    }
  ],
  "volumes": [
    {
      "name": "bookstore-db",
      "efsVolumeConfiguration": {
        "fileSystemId":      "${EFS_FS_ID}",
        "rootDirectory":     "/",
        "transitEncryption": "ENABLED",
        "authorizationConfig": {
          "accessPointId": "${EFS_AP_ID}",
          "iam":           "ENABLED"
        }
      }
    }
  ]
}
EOF
)

# ── 2. Register the task definition ───────────────────────────────────────────
section "Registering task definition"
TASK_DEF_ARN=$(echo "${TASK_DEF_JSON}" \
  | aws ecs register-task-definition \
      --cli-input-json file:///dev/stdin \
      --region "${AWS_REGION}" \
      --query 'taskDefinition.taskDefinitionArn' \
      --output text)
echo "  Registered: ${TASK_DEF_ARN}"

# ── 3. Create or update the ECS service ───────────────────────────────────────
section "ECS service: ${SERVICE_NAME}"
SERVICE_STATUS=$(aws ecs describe-services \
  --cluster "${ECS_CLUSTER}" \
  --services "${SERVICE_NAME}" \
  --region "${AWS_REGION}" \
  --query "services[0].status" --output text 2>/dev/null || echo "MISSING")

NETWORK_CONFIG="awsvpcConfiguration={subnets=[$(echo "${SUBNET_IDS}" | tr ',' ',')],securityGroups=[${TASK_SG_ID}],assignPublicIp=ENABLED}"

if [[ "${SERVICE_STATUS}" == "ACTIVE" ]]; then
  echo "  Service already exists — updating …"
  aws ecs update-service \
    --cluster "${ECS_CLUSTER}" \
    --service "${SERVICE_NAME}" \
    --task-definition "${TASK_DEF_ARN}" \
    --desired-count "${DESIRED_COUNT}" \
    --force-new-deployment \
    --region "${AWS_REGION}" \
    --query 'service.serviceArn' --output text
  echo "  Updated service with new task definition."
else
  echo "  Creating new service …"
  aws ecs create-service \
    --cluster "${ECS_CLUSTER}" \
    --service-name "${SERVICE_NAME}" \
    --task-definition "${TASK_DEF_ARN}" \
    --desired-count "${DESIRED_COUNT}" \
    --launch-type FARGATE \
    --network-configuration "${NETWORK_CONFIG}" \
    --load-balancers "targetGroupArn=${TG_ARN},containerName=${APP_NAME},containerPort=8000" \
    --health-check-grace-period-seconds 60 \
    --deployment-configuration "maximumPercent=200,minimumHealthyPercent=100" \
    --region "${AWS_REGION}" \
    --query 'service.serviceArn' --output text
  echo "  Service created."
fi

# ── 4. Wait for stability ─────────────────────────────────────────────────────
section "Waiting for service to stabilise (up to 5 min) …"
aws ecs wait services-stable \
  --cluster "${ECS_CLUSTER}" \
  --services "${SERVICE_NAME}" \
  --region "${AWS_REGION}"

echo ""
echo "════════════════════════════════════════════════════════"
echo "  ✅  Deployment complete"
echo ""
echo "  App URL : http://${ALB_DNS}"
echo "  Logs    : aws logs tail ${LOG_GROUP} --follow"
echo "════════════════════════════════════════════════════════"
