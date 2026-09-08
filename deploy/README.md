# Deploying Bookstore Agent to AWS

This guide walks through deploying the app on **AWS ECS Fargate** with ECR, EFS, ALB, and Secrets Manager — all in region `ap-south-1`.

---

## Architecture

```
Internet
   │  HTTP :80
   ▼
Application Load Balancer  (bookstore-agent-alb)
   │  HTTP :8000  (ALB SG → Task SG)
   ▼
ECS Fargate Task  (bookstore-agent)
   │  IAM role → AWS Secrets Manager  (JWT_SECRET, GROQ_MODEL, GROQ_API_KEY)
   │  EFS mount  → /data/db/bookstore.db
   └─ CloudWatch Logs  /ecs/bookstore-agent
```

---

## Prerequisites

| Tool | Min version | Install |
|---|---|---|
| AWS CLI v2 | 2.x | https://docs.aws.amazon.com/cli/latest/userguide/install-cliv2.html |
| Docker | 24.x | https://docs.docker.com/get-docker/ |
| jq | 1.6+ | `brew install jq` / `apt install jq` |
| Python 3 | 3.11+ | needed by infra.sh to generate JWT_SECRET |

Your AWS identity must have permissions to create: ECS, ECR, EFS, ALB, IAM roles, Secrets Manager, CloudWatch Logs, and EC2 security groups. An `AdministratorAccess` policy works for initial setup.

---

## One-time Setup

### 1. Configure AWS CLI

```bash
aws configure          # enter Access Key, Secret, region=ap-south-1, output=json
# or use an SSO profile:
aws sso login --profile my-profile
export AWS_PROFILE=my-profile
```

### 2. Make scripts executable

```bash
chmod +x deploy/infra.sh deploy/ecr_push.sh deploy/ecs_deploy.sh deploy/teardown.sh
```

---

## Deployment Steps

### Step 1 — Bootstrap infrastructure

```bash
./deploy/infra.sh
```

This creates (idempotent — safe to re-run):

- ECR repository `bookstore-agent`
- ECS cluster `bookstore-agent`
- VPC security groups (ALB, Task, EFS)
- EFS file system + mount targets + access point
- Secrets Manager secret `bookstore-agent-secrets` (auto-generates `JWT_SECRET`)
- IAM execution role `ecsTaskExecutionRole`
- IAM task role `bookstore-agent-task-role`
- CloudWatch log group `/ecs/bookstore-agent` (90-day retention)
- ALB `bookstore-agent-alb` + target group + HTTP listener on port 80

Outputs are written to `deploy/.env.infra` (gitignored).

> **Note on GROQ_API_KEY:** The Groq key is read from the existing secret
> `arn:aws:secretsmanager:ap-south-1:913524927483:secret:inc-assistant-api-keys-AKmRZ2`.
> No action required — IAM permissions are granted automatically by `infra.sh`.

### Step 2 — Build and push the Docker image

```bash
./deploy/ecr_push.sh
# or pin a version tag:
IMAGE_TAG=v1.0.0 ./deploy/ecr_push.sh
```

The image is built for `linux/amd64` (required by Fargate) from the repo root [`Dockerfile`](../Dockerfile).

### Step 3 — Register task definition and launch service

```bash
./deploy/ecs_deploy.sh
# or deploy a specific tag:
IMAGE_TAG=v1.0.0 ./deploy/ecs_deploy.sh
```

The script:
1. Renders the task definition JSON with live values from `deploy/.env.infra`
2. Registers a new task definition revision
3. Creates the ECS service (or updates it on re-run with `--force-new-deployment`)
4. Waits until the service is stable

### Step 4 — Verify

```bash
# Print the ALB URL
source deploy/.env.infra && echo "http://${ALB_DNS}"

# Tail logs
aws logs tail /ecs/bookstore-agent --follow --region ap-south-1

# Health check
curl http://<ALB_DNS>/api/info
```

---

## Updating the App

To ship a new version:

```bash
IMAGE_TAG=v1.1.0 ./deploy/ecr_push.sh
IMAGE_TAG=v1.1.0 ./deploy/ecs_deploy.sh
```

`ecs_deploy.sh` registers a new task definition revision and triggers a rolling deploy (`maximumPercent=200`, `minimumHealthyPercent=100` — zero downtime).

---

## Environment Variables & Secrets

| Variable | Source | Description |
|---|---|---|
| `JWT_SECRET` | Secrets Manager `bookstore-agent-secrets` | Signing key for JWT tokens |
| `GROQ_API_KEY` | Secrets Manager (shared ARN) | Groq LLM API key |
| `GROQ_MODEL` | Secrets Manager `bookstore-agent-secrets` | Model name (default `llama-3.3-70b-versatile`) |
| `AWS_DEFAULT_REGION` | Task environment | AWS region |
| `DB_PATH` | Task environment | SQLite path on EFS (`/data/db/bookstore.db`) |

Secrets are injected at task start via the **execution role** — they never appear in the container image or task definition as plaintext.

---

## IAM Roles Summary

| Role | Purpose |
|---|---|
| `ecsTaskExecutionRole` | Lets ECS pull images from ECR and inject secrets at startup |
| `bookstore-agent-task-role` | Runtime permissions: Secrets Manager read, EFS mount |

---

## Teardown

To delete **all** AWS resources created by this guide:

```bash
./deploy/teardown.sh
```

> ⚠️ This permanently deletes the EFS file system and all SQLite data.
> An interactive confirmation prompt is shown before anything is deleted.
> Use `FORCE=1 ./deploy/teardown.sh` to skip the prompt in CI.

---

## Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| Tasks fail to start | Image not in ECR | Run `ecr_push.sh` first |
| `CannotPullContainerError` | ECR login expired or wrong region | Re-run `ecr_push.sh` |
| `ResourceInitializationError` on EFS | Mount targets still provisioning | Wait 60s and retry |
| Health check failures | App not yet listening | Increase `startPeriod` in task definition |
| `AccessDeniedException` on secrets | Execution role missing policy | Re-run `infra.sh` |
| Mock LLM instead of Groq | `GROQ_API_KEY` not in secret | Verify the shared Groq ARN is accessible |

```bash
# Describe stopped tasks to see failure reason
aws ecs list-tasks --cluster bookstore-agent --desired-status STOPPED --region ap-south-1
aws ecs describe-tasks --cluster bookstore-agent --tasks <task-arn> --region ap-south-1 \
  --query 'tasks[0].stoppedReason'
```
