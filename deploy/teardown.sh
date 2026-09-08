#!/usr/bin/env bash
# =============================================================================
# deploy/teardown.sh
#
# Deletes ALL AWS resources created by deploy/infra.sh.
# Requires deploy/.env.infra (written by infra.sh).
#
# ⚠️  DESTRUCTIVE — this will permanently delete the EFS file system and all
#     stored data. Confirm the prompt before proceeding.
#
# Usage:
#   ./deploy/teardown.sh
#   FORCE=1 ./deploy/teardown.sh   # skip confirmation (CI use only)
# =============================================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
INFRA_ENV="${SCRIPT_DIR}/.env.infra"

if [[ ! -f "${INFRA_ENV}" ]]; then
  echo "❌  ${INFRA_ENV} not found. Nothing to tear down."
  exit 1
fi

# shellcheck source=deploy/.env.infra
source "${INFRA_ENV}"

section() { echo ""; echo "▶ $*"; }

APP_NAME="bookstore-agent"
SERVICE_NAME="${APP_NAME}"
EXEC_ROLE_NAME="ecsTaskExecutionRole"
TASK_ROLE_NAME="${APP_NAME}-task-role"

echo "========================================================"
echo "  Bookstore Agent — Teardown"
echo "  Region  : ${AWS_REGION}"
echo "  Account : ${AWS_ACCOUNT_ID}"
echo "========================================================"
echo ""
echo "  Resources to be DELETED:"
echo "    • ECS service        ${SERVICE_NAME}"
echo "    • ECS cluster        ${ECS_CLUSTER}"
echo "    • ECR repository     bookstore-agent (ALL images)"
echo "    • ALB                ${ALB_NAME}"
echo "    • Target group       ${TG_NAME}"
echo "    • EFS (ALL DATA)     ${EFS_FS_ID}"
echo "    • Security groups    alb-sg / task-sg / efs-sg"
echo "    • IAM roles/policies ${EXEC_ROLE_NAME} / ${TASK_ROLE_NAME}"
echo "    • Secrets Manager    bookstore-agent-secrets"
echo "    • CloudWatch logs    ${LOG_GROUP}"
echo ""

if [[ "${FORCE:-0}" != "1" ]]; then
  read -rp "Type 'yes' to confirm teardown: " CONFIRM
  if [[ "${CONFIRM}" != "yes" ]]; then
    echo "Aborted."
    exit 0
  fi
fi

# ── 1. Scale down and delete ECS service ──────────────────────────────────────
section "ECS service"
if aws ecs describe-services \
     --cluster "${ECS_CLUSTER}" \
     --services "${SERVICE_NAME}" \
     --region "${AWS_REGION}" \
     --query "services[0].status" --output text 2>/dev/null | grep -q "ACTIVE"; then
  aws ecs update-service \
    --cluster "${ECS_CLUSTER}" \
    --service "${SERVICE_NAME}" \
    --desired-count 0 \
    --region "${AWS_REGION}" > /dev/null
  echo "  Scaled to 0. Waiting for tasks to drain …"
  aws ecs wait services-stable \
    --cluster "${ECS_CLUSTER}" \
    --services "${SERVICE_NAME}" \
    --region "${AWS_REGION}"
  aws ecs delete-service \
    --cluster "${ECS_CLUSTER}" \
    --service "${SERVICE_NAME}" \
    --force \
    --region "${AWS_REGION}" > /dev/null
  echo "  Deleted service: ${SERVICE_NAME}"
else
  echo "  Service not found — skipping."
fi

# ── 2. Deregister all task definition revisions ───────────────────────────────
section "Task definition revisions"
TASK_DEF_ARNS=$(aws ecs list-task-definitions \
  --family-prefix "${APP_NAME}" \
  --region "${AWS_REGION}" \
  --query 'taskDefinitionArns[]' --output text 2>/dev/null || true)
for ARN in ${TASK_DEF_ARNS}; do
  aws ecs deregister-task-definition \
    --task-definition "${ARN}" \
    --region "${AWS_REGION}" > /dev/null
  echo "  Deregistered: ${ARN}"
done

# ── 3. Delete ECS cluster ─────────────────────────────────────────────────────
section "ECS cluster"
if aws ecs describe-clusters --clusters "${ECS_CLUSTER}" \
     --region "${AWS_REGION}" \
     --query "clusters[?status=='ACTIVE'] | [0]" --output text 2>/dev/null | grep -q "ACTIVE"; then
  aws ecs delete-cluster \
    --cluster "${ECS_CLUSTER}" \
    --region "${AWS_REGION}" > /dev/null
  echo "  Deleted cluster: ${ECS_CLUSTER}"
else
  echo "  Cluster not found — skipping."
fi

# ── 4. Delete ALB listener, target group, load balancer ───────────────────────
section "ALB listener"
for L_ARN in $(aws elbv2 describe-listeners \
  --load-balancer-arn "${ALB_ARN}" \
  --region "${AWS_REGION}" \
  --query 'Listeners[*].ListenerArn' --output text 2>/dev/null || true); do
  aws elbv2 delete-listener \
    --listener-arn "${L_ARN}" \
    --region "${AWS_REGION}" > /dev/null
  echo "  Deleted listener: ${L_ARN}"
done

section "ALB"
if aws elbv2 describe-load-balancers \
     --load-balancer-arns "${ALB_ARN}" \
     --region "${AWS_REGION}" &>/dev/null; then
  aws elbv2 delete-load-balancer \
    --load-balancer-arn "${ALB_ARN}" \
    --region "${AWS_REGION}" > /dev/null
  echo "  Deleted ALB: ${ALB_ARN}"
  echo "  Waiting for ALB to be removed …"
  aws elbv2 wait load-balancers-deleted \
    --load-balancer-arns "${ALB_ARN}" \
    --region "${AWS_REGION}"
else
  echo "  ALB not found — skipping."
fi

section "Target group"
if aws elbv2 describe-target-groups \
     --target-group-arns "${TG_ARN}" \
     --region "${AWS_REGION}" &>/dev/null; then
  aws elbv2 delete-target-group \
    --target-group-arn "${TG_ARN}" \
    --region "${AWS_REGION}" > /dev/null
  echo "  Deleted target group: ${TG_ARN}"
else
  echo "  Target group not found — skipping."
fi

# ── 5. Delete ECR repository (all images) ─────────────────────────────────────
section "ECR repository"
if aws ecr describe-repositories \
     --repository-names "${APP_NAME}" \
     --region "${AWS_REGION}" &>/dev/null; then
  aws ecr delete-repository \
    --repository-name "${APP_NAME}" \
    --force \
    --region "${AWS_REGION}" > /dev/null
  echo "  Deleted ECR repository: ${APP_NAME}"
else
  echo "  ECR repository not found — skipping."
fi

# ── 6. Delete EFS access points and mount targets, then file system ────────────
section "EFS"
for AP_ID in $(aws efs describe-access-points \
  --file-system-id "${EFS_FS_ID}" \
  --region "${AWS_REGION}" \
  --query 'AccessPoints[*].AccessPointId' --output text 2>/dev/null || true); do
  aws efs delete-access-point \
    --access-point-id "${AP_ID}" \
    --region "${AWS_REGION}" > /dev/null
  echo "  Deleted EFS access point: ${AP_ID}"
done

for MT_ID in $(aws efs describe-mount-targets \
  --file-system-id "${EFS_FS_ID}" \
  --region "${AWS_REGION}" \
  --query 'MountTargets[*].MountTargetId' --output text 2>/dev/null || true); do
  aws efs delete-mount-target \
    --mount-target-id "${MT_ID}" \
    --region "${AWS_REGION}" > /dev/null
  echo "  Deleted mount target: ${MT_ID}"
done

echo "  Waiting 15s for mount targets to finish deleting …"
sleep 15

if aws efs describe-file-systems \
     --file-system-id "${EFS_FS_ID}" \
     --region "${AWS_REGION}" &>/dev/null; then
  aws efs delete-file-system \
    --file-system-id "${EFS_FS_ID}" \
    --region "${AWS_REGION}" > /dev/null
  echo "  Deleted EFS file system: ${EFS_FS_ID}"
else
  echo "  EFS not found — skipping."
fi

# ── 7. Delete security groups (EFS SG → Task SG → ALB SG order matters) ───────
section "Security groups"
for SG_ID in "${EFS_SG_ID}" "${TASK_SG_ID}" "${ALB_SG_ID}"; do
  if aws ec2 describe-security-groups \
       --group-ids "${SG_ID}" \
       --region "${AWS_REGION}" &>/dev/null; then
    aws ec2 delete-security-group \
      --group-id "${SG_ID}" \
      --region "${AWS_REGION}" > /dev/null
    echo "  Deleted SG: ${SG_ID}"
  else
    echo "  SG ${SG_ID} not found — skipping."
  fi
done

# ── 8. IAM — detach/delete policies, then delete roles ────────────────────────
section "IAM roles"
for ROLE in "${EXEC_ROLE_NAME}" "${TASK_ROLE_NAME}"; do
  if aws iam get-role --role-name "${ROLE}" &>/dev/null; then
    # Detach managed policies
    for POLICY_ARN in $(aws iam list-attached-role-policies \
      --role-name "${ROLE}" \
      --query 'AttachedPolicies[*].PolicyArn' --output text 2>/dev/null || true); do
      aws iam detach-role-policy \
        --role-name "${ROLE}" \
        --policy-arn "${POLICY_ARN}" > /dev/null
    done
    # Delete inline policies
    for POLICY_NAME in $(aws iam list-role-policies \
      --role-name "${ROLE}" \
      --query 'PolicyNames[]' --output text 2>/dev/null || true); do
      aws iam delete-role-policy \
        --role-name "${ROLE}" \
        --policy-name "${POLICY_NAME}" > /dev/null
    done
    aws iam delete-role --role-name "${ROLE}" > /dev/null
    echo "  Deleted IAM role: ${ROLE}"
  else
    echo "  IAM role ${ROLE} not found — skipping."
  fi
done

# ── 9. Delete Secrets Manager secret ──────────────────────────────────────────
section "Secrets Manager"
if aws secretsmanager describe-secret \
     --secret-id "${APP_NAME}-secrets" \
     --region "${AWS_REGION}" &>/dev/null; then
  aws secretsmanager delete-secret \
    --secret-id "${APP_NAME}-secrets" \
    --force-delete-without-recovery \
    --region "${AWS_REGION}" > /dev/null
  echo "  Deleted secret: ${APP_NAME}-secrets"
else
  echo "  Secret not found — skipping."
fi

# ── 10. Delete CloudWatch log group ───────────────────────────────────────────
section "CloudWatch log group"
if aws logs describe-log-groups \
     --log-group-name-prefix "${LOG_GROUP}" \
     --region "${AWS_REGION}" \
     --query "logGroups[?logGroupName=='${LOG_GROUP}'] | [0]" \
     --output text 2>/dev/null | grep -q "${LOG_GROUP}"; then
  aws logs delete-log-group \
    --log-group-name "${LOG_GROUP}" \
    --region "${AWS_REGION}" > /dev/null
  echo "  Deleted log group: ${LOG_GROUP}"
else
  echo "  Log group not found — skipping."
fi

# ── Clean up local infra env file ─────────────────────────────────────────────
rm -f "${INFRA_ENV}"
echo ""
echo "════════════════════════════════════════════════════════"
echo "  ✅  Teardown complete — all resources removed."
echo "════════════════════════════════════════════════════════"
