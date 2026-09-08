#!/usr/bin/env bash
# =============================================================================
# deploy/infra.sh
#
# One-time infrastructure bootstrap for the Bookstore Agent on AWS.
# Idempotent — safe to re-run; existing resources are detected and skipped.
#
# What this script creates
# ────────────────────────
#  1. ECR repository
#  2. ECS cluster (FARGATE)
#  3. VPC security groups  (ALB + ECS task)
#  4. EFS file system + mount targets (one per AZ in the default VPC)
#  5. EFS access point
#  6. AWS Secrets Manager secret  (JWT_SECRET + GROQ_MODEL)
#  7. IAM execution role  (ecsTaskExecutionRole)
#  8. IAM task role        (bookstore-agent-task-role)
#  9. CloudWatch log group
# 10. Application Load Balancer + target group + HTTP listener
#
# Prerequisites:
#   - AWS CLI v2
#   - jq
#   - Sufficient IAM permissions (AdministratorAccess or a custom policy)
#
# Usage:
#   AWS_DEFAULT_REGION=ap-south-1 ./deploy/infra.sh
#
# Outputs written to deploy/.env.infra  — sourced by ecs_deploy.sh
# =============================================================================
set -euo pipefail

# ── Configuration ─────────────────────────────────────────────────────────────
AWS_REGION="${AWS_DEFAULT_REGION:-ap-south-1}"
AWS_ACCOUNT_ID="${AWS_ACCOUNT_ID:-$(aws sts get-caller-identity --query Account --output text)}"

APP_NAME="bookstore-agent"
ECS_CLUSTER="${APP_NAME}"
ECR_REPO="${APP_NAME}"
LOG_GROUP="/ecs/${APP_NAME}"
SECRET_NAME="${APP_NAME}-secrets"
EXEC_ROLE_NAME="ecsTaskExecutionRole"
TASK_ROLE_NAME="${APP_NAME}-task-role"
ALB_NAME="${APP_NAME}-alb"
TG_NAME="${APP_NAME}-tg"

# Groq shared secret ARN (already exists — do not overwrite)
GROQ_SECRET_ARN="arn:aws:secretsmanager:ap-south-1:913524927483:secret:inc-assistant-api-keys-AKmRZ2"

INFRA_ENV_FILE="$(dirname "${BASH_SOURCE[0]}")/.env.infra"

echo "========================================================"
echo "  Bookstore Agent — Infrastructure Bootstrap"
echo "  Region  : ${AWS_REGION}"
echo "  Account : ${AWS_ACCOUNT_ID}"
echo "========================================================"

# Helper: print section header
section() { echo ""; echo "▶ $*"; }

# ── 1. ECR Repository ─────────────────────────────────────────────────────────
section "ECR repository"
ECR_REPO_URI="${AWS_ACCOUNT_ID}.dkr.ecr.${AWS_REGION}.amazonaws.com/${ECR_REPO}"
if aws ecr describe-repositories --repository-names "${ECR_REPO}" \
     --region "${AWS_REGION}" &>/dev/null; then
  echo "  Already exists: ${ECR_REPO_URI}"
else
  aws ecr create-repository \
    --repository-name "${ECR_REPO}" \
    --image-scanning-configuration scanOnPush=true \
    --encryption-configuration encryptionType=AES256 \
    --region "${AWS_REGION}" \
    --query 'repository.repositoryUri' --output text
  echo "  Created: ${ECR_REPO_URI}"
fi

# ── 2. ECS Cluster ────────────────────────────────────────────────────────────
section "ECS cluster"
if aws ecs describe-clusters --clusters "${ECS_CLUSTER}" \
     --region "${AWS_REGION}" \
     --query "clusters[?status=='ACTIVE'] | [0].clusterArn" \
     --output text 2>/dev/null | grep -q "arn:"; then
  echo "  Already exists: ${ECS_CLUSTER}"
else
  aws ecs create-cluster \
    --cluster-name "${ECS_CLUSTER}" \
    --capacity-providers FARGATE FARGATE_SPOT \
    --region "${AWS_REGION}" \
    --query 'cluster.clusterArn' --output text
  echo "  Created: ${ECS_CLUSTER}"
fi

# ── 3. Default VPC & Subnets ──────────────────────────────────────────────────
section "VPC / subnets"
VPC_ID=$(aws ec2 describe-vpcs \
  --filters "Name=isDefault,Values=true" \
  --region "${AWS_REGION}" \
  --query 'Vpcs[0].VpcId' --output text)
echo "  Default VPC: ${VPC_ID}"

SUBNET_IDS=$(aws ec2 describe-subnets \
  --filters "Name=vpc-id,Values=${VPC_ID}" \
  --region "${AWS_REGION}" \
  --query 'Subnets[*].SubnetId' --output text | tr '\t' ',')
echo "  Subnets: ${SUBNET_IDS}"

# ── 4. Security Groups ────────────────────────────────────────────────────────
section "Security groups"

# ALB SG — allow inbound HTTP 80 from anywhere
ALB_SG_ID=$(aws ec2 describe-security-groups \
  --filters "Name=group-name,Values=${APP_NAME}-alb-sg" "Name=vpc-id,Values=${VPC_ID}" \
  --region "${AWS_REGION}" \
  --query 'SecurityGroups[0].GroupId' --output text 2>/dev/null || echo "None")

if [[ "${ALB_SG_ID}" == "None" || -z "${ALB_SG_ID}" ]]; then
  ALB_SG_ID=$(aws ec2 create-security-group \
    --group-name "${APP_NAME}-alb-sg" \
    --description "ALB — allow HTTP inbound" \
    --vpc-id "${VPC_ID}" \
    --region "${AWS_REGION}" \
    --query 'GroupId' --output text)
  aws ec2 authorize-security-group-ingress \
    --group-id "${ALB_SG_ID}" \
    --protocol tcp --port 80 --cidr 0.0.0.0/0 \
    --region "${AWS_REGION}" > /dev/null
  echo "  Created ALB SG: ${ALB_SG_ID}"
else
  echo "  ALB SG already exists: ${ALB_SG_ID}"
fi

# ECS Task SG — allow inbound 8000 only from ALB SG
TASK_SG_ID=$(aws ec2 describe-security-groups \
  --filters "Name=group-name,Values=${APP_NAME}-task-sg" "Name=vpc-id,Values=${VPC_ID}" \
  --region "${AWS_REGION}" \
  --query 'SecurityGroups[0].GroupId' --output text 2>/dev/null || echo "None")

if [[ "${TASK_SG_ID}" == "None" || -z "${TASK_SG_ID}" ]]; then
  TASK_SG_ID=$(aws ec2 create-security-group \
    --group-name "${APP_NAME}-task-sg" \
    --description "ECS task — allow 8000 from ALB only" \
    --vpc-id "${VPC_ID}" \
    --region "${AWS_REGION}" \
    --query 'GroupId' --output text)
  aws ec2 authorize-security-group-ingress \
    --group-id "${TASK_SG_ID}" \
    --protocol tcp --port 8000 \
    --source-group "${ALB_SG_ID}" \
    --region "${AWS_REGION}" > /dev/null
  echo "  Created Task SG: ${TASK_SG_ID}"
else
  echo "  Task SG already exists: ${TASK_SG_ID}"
fi

# EFS SG — allow NFS 2049 from task SG
EFS_SG_ID=$(aws ec2 describe-security-groups \
  --filters "Name=group-name,Values=${APP_NAME}-efs-sg" "Name=vpc-id,Values=${VPC_ID}" \
  --region "${AWS_REGION}" \
  --query 'SecurityGroups[0].GroupId' --output text 2>/dev/null || echo "None")

if [[ "${EFS_SG_ID}" == "None" || -z "${EFS_SG_ID}" ]]; then
  EFS_SG_ID=$(aws ec2 create-security-group \
    --group-name "${APP_NAME}-efs-sg" \
    --description "EFS — allow NFS from ECS tasks" \
    --vpc-id "${VPC_ID}" \
    --region "${AWS_REGION}" \
    --query 'GroupId' --output text)
  aws ec2 authorize-security-group-ingress \
    --group-id "${EFS_SG_ID}" \
    --protocol tcp --port 2049 \
    --source-group "${TASK_SG_ID}" \
    --region "${AWS_REGION}" > /dev/null
  echo "  Created EFS SG: ${EFS_SG_ID}"
else
  echo "  EFS SG already exists: ${EFS_SG_ID}"
fi

# ── 5. EFS File System ────────────────────────────────────────────────────────
section "EFS file system"
EFS_FS_ID=$(aws efs describe-file-systems \
  --region "${AWS_REGION}" \
  --query "FileSystems[?Tags[?Key=='Name' && Value=='${APP_NAME}-db']].FileSystemId | [0]" \
  --output text 2>/dev/null || echo "None")

if [[ "${EFS_FS_ID}" == "None" || -z "${EFS_FS_ID}" ]]; then
  EFS_FS_ID=$(aws efs create-file-system \
    --encrypted \
    --performance-mode generalPurpose \
    --throughput-mode bursting \
    --tags "Key=Name,Value=${APP_NAME}-db" \
    --region "${AWS_REGION}" \
    --query 'FileSystemId' --output text)
  echo "  Created EFS: ${EFS_FS_ID}"

  # Wait until available
  echo "  Waiting for EFS to become available …"
  aws efs wait file-system-available \
    --file-system-id "${EFS_FS_ID}" \
    --region "${AWS_REGION}"

  # Create one mount target per subnet
  for SUBNET in $(echo "${SUBNET_IDS}" | tr ',' ' '); do
    aws efs create-mount-target \
      --file-system-id "${EFS_FS_ID}" \
      --subnet-id "${SUBNET}" \
      --security-groups "${EFS_SG_ID}" \
      --region "${AWS_REGION}" > /dev/null
    echo "  Mount target created in subnet ${SUBNET}"
  done
else
  echo "  EFS already exists: ${EFS_FS_ID}"
fi

# EFS Access Point
EFS_AP_ID=$(aws efs describe-access-points \
  --file-system-id "${EFS_FS_ID}" \
  --region "${AWS_REGION}" \
  --query "AccessPoints[?Tags[?Key=='Name' && Value=='${APP_NAME}-ap']].AccessPointId | [0]" \
  --output text 2>/dev/null || echo "None")

if [[ "${EFS_AP_ID}" == "None" || -z "${EFS_AP_ID}" ]]; then
  EFS_AP_ID=$(aws efs create-access-point \
    --file-system-id "${EFS_FS_ID}" \
    --posix-user Uid=1001,Gid=1001 \
    --root-directory "Path=/bookstore,CreationInfo={OwnerUid=1001,OwnerGid=1001,Permissions=755}" \
    --tags "Key=Name,Value=${APP_NAME}-ap" \
    --region "${AWS_REGION}" \
    --query 'AccessPointId' --output text)
  echo "  Created EFS Access Point: ${EFS_AP_ID}"
else
  echo "  EFS Access Point already exists: ${EFS_AP_ID}"
fi

# ── 6. Secrets Manager ────────────────────────────────────────────────────────
section "Secrets Manager — ${SECRET_NAME}"
if aws secretsmanager describe-secret \
     --secret-id "${SECRET_NAME}" \
     --region "${AWS_REGION}" &>/dev/null; then
  echo "  Already exists. To rotate JWT_SECRET run:"
  echo "    aws secretsmanager rotate-secret --secret-id ${SECRET_NAME}"
else
  JWT_SECRET=$(python3 -c "import secrets; print(secrets.token_hex(32))")
  aws secretsmanager create-secret \
    --name "${SECRET_NAME}" \
    --description "JWT_SECRET and GROQ_MODEL for ${APP_NAME}" \
    --secret-string "{\"JWT_SECRET\":\"${JWT_SECRET}\",\"GROQ_MODEL\":\"llama-3.3-70b-versatile\"}" \
    --region "${AWS_REGION}" \
    --query 'ARN' --output text
  echo "  Created secret: ${SECRET_NAME}"
  echo "  ⚠️  JWT_SECRET has been generated and stored in Secrets Manager."
fi

APP_SECRET_ARN=$(aws secretsmanager describe-secret \
  --secret-id "${SECRET_NAME}" \
  --region "${AWS_REGION}" \
  --query 'ARN' --output text)

# ── 7. IAM — Execution Role ───────────────────────────────────────────────────
section "IAM execution role: ${EXEC_ROLE_NAME}"
EXEC_ROLE_ARN="arn:aws:iam::${AWS_ACCOUNT_ID}:role/${EXEC_ROLE_NAME}"
if aws iam get-role --role-name "${EXEC_ROLE_NAME}" &>/dev/null; then
  echo "  Already exists: ${EXEC_ROLE_ARN}"
else
  aws iam create-role \
    --role-name "${EXEC_ROLE_NAME}" \
    --assume-role-policy-document '{
      "Version":"2012-10-17",
      "Statement":[{
        "Effect":"Allow",
        "Principal":{"Service":"ecs-tasks.amazonaws.com"},
        "Action":"sts:AssumeRole"
      }]
    }' > /dev/null
  aws iam attach-role-policy \
    --role-name "${EXEC_ROLE_NAME}" \
    --policy-arn "arn:aws:iam::aws:policy/service-role/AmazonECSTaskExecutionRolePolicy"
  echo "  Created: ${EXEC_ROLE_ARN}"
fi

# Grant the execution role read access to the secrets it must inject
aws iam put-role-policy \
  --role-name "${EXEC_ROLE_NAME}" \
  --policy-name "${APP_NAME}-secrets-read" \
  --policy-document "{
    \"Version\":\"2012-10-17\",
    \"Statement\":[
      {
        \"Effect\":\"Allow\",
        \"Action\":[\"secretsmanager:GetSecretValue\",\"kms:Decrypt\"],
        \"Resource\":[
          \"${APP_SECRET_ARN}\",
          \"${GROQ_SECRET_ARN}\"
        ]
      }
    ]
  }" > /dev/null
echo "  Secrets-read policy attached to ${EXEC_ROLE_NAME}"

# ── 8. IAM — Task Role ────────────────────────────────────────────────────────
section "IAM task role: ${TASK_ROLE_NAME}"
TASK_ROLE_ARN="arn:aws:iam::${AWS_ACCOUNT_ID}:role/${TASK_ROLE_NAME}"
if aws iam get-role --role-name "${TASK_ROLE_NAME}" &>/dev/null; then
  echo "  Already exists: ${TASK_ROLE_ARN}"
else
  aws iam create-role \
    --role-name "${TASK_ROLE_NAME}" \
    --assume-role-policy-document '{
      "Version":"2012-10-17",
      "Statement":[{
        "Effect":"Allow",
        "Principal":{"Service":"ecs-tasks.amazonaws.com"},
        "Action":"sts:AssumeRole"
      }]
    }' > /dev/null
  echo "  Created: ${TASK_ROLE_ARN}"
fi

# Inline policy: Secrets Manager read + EFS client mount
aws iam put-role-policy \
  --role-name "${TASK_ROLE_NAME}" \
  --policy-name "${APP_NAME}-task-policy" \
  --policy-document "{
    \"Version\":\"2012-10-17\",
    \"Statement\":[
      {
        \"Effect\":\"Allow\",
        \"Action\":[\"secretsmanager:GetSecretValue\",\"kms:Decrypt\"],
        \"Resource\":[
          \"${APP_SECRET_ARN}\",
          \"${GROQ_SECRET_ARN}\"
        ]
      },
      {
        \"Effect\":\"Allow\",
        \"Action\":[
          \"elasticfilesystem:ClientMount\",
          \"elasticfilesystem:ClientWrite\",
          \"elasticfilesystem:DescribeMountTargets\"
        ],
        \"Resource\":\"arn:aws:elasticfilesystem:${AWS_REGION}:${AWS_ACCOUNT_ID}:file-system/${EFS_FS_ID}\"
      }
    ]
  }" > /dev/null
echo "  Task policy attached to ${TASK_ROLE_NAME}"

# ── 9. CloudWatch Log Group ───────────────────────────────────────────────────
section "CloudWatch log group: ${LOG_GROUP}"
if aws logs describe-log-groups \
     --log-group-name-prefix "${LOG_GROUP}" \
     --region "${AWS_REGION}" \
     --query "logGroups[?logGroupName=='${LOG_GROUP}'] | [0]" \
     --output text 2>/dev/null | grep -q "${LOG_GROUP}"; then
  echo "  Already exists: ${LOG_GROUP}"
else
  aws logs create-log-group \
    --log-group-name "${LOG_GROUP}" \
    --region "${AWS_REGION}"
  aws logs put-retention-policy \
    --log-group-name "${LOG_GROUP}" \
    --retention-in-days 90 \
    --region "${AWS_REGION}"
  echo "  Created: ${LOG_GROUP} (90-day retention)"
fi

# ── 10. ALB + Target Group + Listener ─────────────────────────────────────────
section "Application Load Balancer: ${ALB_NAME}"
ALB_ARN=$(aws elbv2 describe-load-balancers \
  --names "${ALB_NAME}" \
  --region "${AWS_REGION}" \
  --query 'LoadBalancers[0].LoadBalancerArn' --output text 2>/dev/null || echo "None")

if [[ "${ALB_ARN}" == "None" || -z "${ALB_ARN}" ]]; then
  SUBNET_LIST=$(echo "${SUBNET_IDS}" | tr ',' ' ')
  ALB_ARN=$(aws elbv2 create-load-balancer \
    --name "${ALB_NAME}" \
    --subnets ${SUBNET_LIST} \
    --security-groups "${ALB_SG_ID}" \
    --scheme internet-facing \
    --type application \
    --ip-address-type ipv4 \
    --region "${AWS_REGION}" \
    --query 'LoadBalancers[0].LoadBalancerArn' --output text)
  echo "  Created ALB: ${ALB_ARN}"
else
  echo "  ALB already exists: ${ALB_ARN}"
fi

ALB_DNS=$(aws elbv2 describe-load-balancers \
  --load-balancer-arns "${ALB_ARN}" \
  --region "${AWS_REGION}" \
  --query 'LoadBalancers[0].DNSName' --output text)

section "Target Group: ${TG_NAME}"
TG_ARN=$(aws elbv2 describe-target-groups \
  --names "${TG_NAME}" \
  --region "${AWS_REGION}" \
  --query 'TargetGroups[0].TargetGroupArn' --output text 2>/dev/null || echo "None")

if [[ "${TG_ARN}" == "None" || -z "${TG_ARN}" ]]; then
  TG_ARN=$(aws elbv2 create-target-group \
    --name "${TG_NAME}" \
    --protocol HTTP \
    --port 8000 \
    --vpc-id "${VPC_ID}" \
    --target-type ip \
    --health-check-protocol HTTP \
    --health-check-path "/api/info" \
    --health-check-interval-seconds 30 \
    --health-check-timeout-seconds 5 \
    --healthy-threshold-count 2 \
    --unhealthy-threshold-count 3 \
    --region "${AWS_REGION}" \
    --query 'TargetGroups[0].TargetGroupArn' --output text)
  echo "  Created TG: ${TG_ARN}"
else
  echo "  TG already exists: ${TG_ARN}"
fi

section "ALB HTTP listener (port 80)"
LISTENER_ARN=$(aws elbv2 describe-listeners \
  --load-balancer-arn "${ALB_ARN}" \
  --region "${AWS_REGION}" \
  --query "Listeners[?Port==\`80\`].ListenerArn | [0]" \
  --output text 2>/dev/null || echo "None")

if [[ "${LISTENER_ARN}" == "None" || -z "${LISTENER_ARN}" ]]; then
  LISTENER_ARN=$(aws elbv2 create-listener \
    --load-balancer-arn "${ALB_ARN}" \
    --protocol HTTP \
    --port 80 \
    --default-actions "Type=forward,TargetGroupArn=${TG_ARN}" \
    --region "${AWS_REGION}" \
    --query 'Listeners[0].ListenerArn' --output text)
  echo "  Created listener: ${LISTENER_ARN}"
else
  echo "  Listener already exists: ${LISTENER_ARN}"
fi

# ── Write .env.infra for downstream scripts ───────────────────────────────────
section "Writing ${INFRA_ENV_FILE}"
cat > "${INFRA_ENV_FILE}" <<EOF
# Auto-generated by deploy/infra.sh — DO NOT COMMIT
AWS_REGION=${AWS_REGION}
AWS_ACCOUNT_ID=${AWS_ACCOUNT_ID}
ECR_REPO_URI=${ECR_REPO_URI}
ECS_CLUSTER=${ECS_CLUSTER}
VPC_ID=${VPC_ID}
SUBNET_IDS=${SUBNET_IDS}
ALB_SG_ID=${ALB_SG_ID}
TASK_SG_ID=${TASK_SG_ID}
EFS_SG_ID=${EFS_SG_ID}
EFS_FS_ID=${EFS_FS_ID}
EFS_AP_ID=${EFS_AP_ID}
APP_SECRET_ARN=${APP_SECRET_ARN}
GROQ_SECRET_ARN=${GROQ_SECRET_ARN}
EXEC_ROLE_ARN=${EXEC_ROLE_ARN}
TASK_ROLE_ARN=${TASK_ROLE_ARN}
ALB_ARN=${ALB_ARN}
ALB_DNS=${ALB_DNS}
TG_ARN=${TG_ARN}
LISTENER_ARN=${LISTENER_ARN}
LOG_GROUP=${LOG_GROUP}
EOF
echo "  Written to ${INFRA_ENV_FILE}"

echo ""
echo "════════════════════════════════════════════════════════"
echo "  ✅  Infrastructure ready"
echo ""
echo "  App URL (after deploy): http://${ALB_DNS}"
echo ""
echo "  Next steps:"
echo "    1.  ./deploy/ecr_push.sh          # build & push Docker image"
echo "    2.  ./deploy/ecs_deploy.sh         # register task def + launch service"
echo "════════════════════════════════════════════════════════"
