#!/usr/bin/env bash
# =============================================================================
# deploy/ecr_push.sh
#
# Builds the Docker image and pushes it to Amazon ECR.
#
# Prerequisites:
#   - AWS CLI v2 installed and configured (or running on an EC2/Cloud9 instance
#     with an IAM instance profile that has ECR push permissions)
#   - Docker installed and running
#   - The ECR repository already exists (created by deploy/infra.sh)
#
# Usage:
#   ./deploy/ecr_push.sh                  # uses defaults from config section
#   IMAGE_TAG=v1.2.3 ./deploy/ecr_push.sh # override the image tag
# =============================================================================
set -euo pipefail

# ── Configuration (override via environment variables) ───────────────────────
AWS_REGION="${AWS_DEFAULT_REGION:-ap-south-1}"
AWS_ACCOUNT_ID="${AWS_ACCOUNT_ID:-$(aws sts get-caller-identity --query Account --output text)}"
ECR_REPO_NAME="${ECR_REPO_NAME:-bookstore-agent}"
IMAGE_TAG="${IMAGE_TAG:-latest}"
PLATFORM="${PLATFORM:-linux/amd64}"   # Fargate requires amd64

ECR_REGISTRY="${AWS_ACCOUNT_ID}.dkr.ecr.${AWS_REGION}.amazonaws.com"
FULL_IMAGE="${ECR_REGISTRY}/${ECR_REPO_NAME}:${IMAGE_TAG}"

# Script lives in deploy/ — resolve repo root (one level up)
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

echo "========================================================"
echo "  Bookstore Agent — ECR Push"
echo "  Registry : ${ECR_REGISTRY}"
echo "  Image    : ${ECR_REPO_NAME}:${IMAGE_TAG}"
echo "  Platform : ${PLATFORM}"
echo "========================================================"

# ── 1. Authenticate Docker to ECR ────────────────────────────────────────────
echo ""
echo "▶ Logging in to ECR …"
aws ecr get-login-password --region "${AWS_REGION}" \
  | docker login --username AWS --password-stdin "${ECR_REGISTRY}"

# ── 2. Build the image ───────────────────────────────────────────────────────
echo ""
echo "▶ Building Docker image …"
docker build \
  --platform "${PLATFORM}" \
  --tag "${FULL_IMAGE}" \
  --tag "${ECR_REGISTRY}/${ECR_REPO_NAME}:latest" \
  --file "${REPO_ROOT}/Dockerfile" \
  "${REPO_ROOT}"

# ── 3. Push to ECR ───────────────────────────────────────────────────────────
echo ""
echo "▶ Pushing image to ECR …"
docker push "${FULL_IMAGE}"

# Also push the :latest tag so task definitions that reference :latest pick it up
if [[ "${IMAGE_TAG}" != "latest" ]]; then
  docker push "${ECR_REGISTRY}/${ECR_REPO_NAME}:latest"
fi

echo ""
echo "✅  Image pushed successfully:"
echo "    ${FULL_IMAGE}"
echo ""
echo "Next step: run  ./deploy/ecs_deploy.sh  to update the running service."
