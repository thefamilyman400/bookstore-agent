# JustBooks — Cloud-Native Agentic Bookstore Platform

> A FastAPI + agentic AI bookstore that grew into a full end-to-end cloud-native DevOps platform — containerised, CI/CD'd through GitHub Actions with OIDC authentication, deployed to Kubernetes on AWS EC2, and observed with Prometheus + Grafana.

---

## Table of Contents

- [What it does](#what-it-does)
- [Platform architecture](#platform-architecture)
- [Application architecture](#application-architecture)
- [Tech stack](#tech-stack)
- [The full journey](#the-full-journey)
  - [1. The application](#1-the-application)
  - [2. Containerisation](#2-containerisation)
  - [3. Source control — GitHub](#3-source-control--github)
  - [4. Container registry — Amazon ECR](#4-container-registry--amazon-ecr)
  - [5. CI/CD — GitHub Actions](#5-cicd--github-actions)
  - [6. Keyless auth — GitHub OIDC → AWS IAM](#6-keyless-auth--github-oidc--aws-iam)
  - [7. The EC2 host — RHEL on AWS](#7-the-ec2-host--rhel-on-aws)
  - [8. Kubernetes — Minikube on EC2](#8-kubernetes--minikube-on-ec2)
  - [9. Kubernetes manifests for JustBooks](#9-kubernetes-manifests-for-justbooks)
  - [10. Private ECR image pull](#10-private-ecr-image-pull)
  - [11. Application deployment](#11-application-deployment)
  - [12. Autoscaling — HPA](#12-autoscaling--hpa)
  - [13. Observability — Prometheus + Grafana](#13-observability--prometheus--grafana)
  - [14. Grafana persistence — PVC](#14-grafana-persistence--pvc)
  - [15. Cross-namespace routing problem](#15-cross-namespace-routing-problem)
  - [16. Fix — Grafana NodePort service](#16-fix--grafana-nodeport-service)
  - [17. Public exposure without a Load Balancer — socat](#17-public-exposure-without-a-load-balancer--socat)
  - [18. Persistent proxies — systemd](#18-persistent-proxies--systemd)
  - [19. Grafana sub-path configuration](#19-grafana-sub-path-configuration)
  - [20. Fully automated deployment](#20-fully-automated-deployment)
  - [21. What this project covers](#21-what-this-project-covers)
- [Quick start (local)](#quick-start-local)
- [Environment variables](#environment-variables)
- [API reference](#api-reference)
- [Running with Docker](#running-with-docker)
- [Deploying to Kubernetes (Minikube)](#deploying-to-kubernetes-minikube)
- [Project structure](#project-structure)

---

## What it does

JustBooks is a conversational bookstore. Users register, log in, and chat with an AI agent that can:

- **Search books** — semantic RAG search powered by `sentence-transformers` (falls back to TF-IDF)
- **Browse by genre** — list all available genres and filter the catalogue
- **View book details** — synopsis, author bio, tags, estimated delivery date
- **Manage a cart** — add, remove, view items with a running total
- **Place & cancel orders** — checkout from cart, view order history, cancel within 48 hours

All actions go through a natural language chat interface. No buttons required — though the UI provides quick-action buttons for convenience.

---

## Platform architecture

```
                         GitHub
                           │
                           │ git push
                           ▼
                    GitHub Actions
                           │
                    ┌──────┴──────┐
                    │             │
                 Docker          OIDC
                 Build           │
                    │             ▼
                    │          AWS IAM
                    │
                    ▼
                   ECR
                    │
                    │ image (Git SHA tag)
                    ▼
             AWS Systems Manager
                    │
                    │ kubectl set image
                    ▼
          ┌──────────────────────┐
          │   AWS EC2 (RHEL)     │
          │                      │
          │   Minikube           │
          │   Kubernetes         │
          │                      │
          │  ┌────────────────┐  │
          │  │   JustBooks    │  │
          │  │ Deployment     │  │
          │  │ Service        │  │
          │  │ Ingress        │  │
          │  │ HPA            │  │
          │  │ PVC            │  │
          │  └────────────────┘  │
          │                      │
          │  ┌────────────────┐  │
          │  │ Prometheus     │  │
          │  │ Grafana        │  │
          │  │ Alertmanager   │  │
          │  │ Node Exporter  │  │
          │  └────────────────┘  │
          └──────────────────────┘
```

---

## Application architecture

```
Browser / Client
      │
      ▼
FastAPI (app.py)          ← REST API + static UI server
  ├── /api/auth/*         ← Register, login, refresh, logout, me
  ├── /api/chat           ← Protected chat endpoint (requires Bearer token)
  ├── /api/books          ← Browse & search catalogue (no auth)
  ├── /api/cart           ← Direct cart endpoints (used by UI buttons)
  ├── /health             ← Liveness probe
  └── /api/info           ← Startup probe + system info
        │
        ▼
Orchestrator Agent        ← Classifies intent (Groq or rule-based)
  ├── CatalogueAgent      ← search_books, get_book_detail, list_genres
  ├── CartAgent           ← add_to_cart, remove_from_cart, view_cart
  └── OrderAgent          ← create_order, get_order_history, cancel_order
        │
        ▼
MCP Server (mcp_server.py)   ← JSON-RPC 2.0 over stdin/stdout
        │
        ▼
RAG Store (rag_store.py)     ← sentence-transformers or TF-IDF
SQLite (db.py)               ← users, cart, orders
```

The **Orchestrator** classifies each user message into one of four intents (`catalogue`, `cart`, `order`, `chitchat`) using Groq when an API key is present, or a regex rule-based classifier otherwise. The matching specialist agent then runs a tool-call loop against the MCP server.

---

## Tech stack

| Layer | Technology |
|---|---|
| API framework | FastAPI 0.141 |
| Server | Uvicorn 0.52 |
| LLM | Groq — `llama-3.3-70b-versatile` |
| Embeddings | `sentence-transformers` (all-MiniLM-L6-v2) / TF-IDF fallback |
| Database | SQLite (via Python `sqlite3`) |
| Auth | JWT (HS256) with access + refresh tokens, bcrypt password hashing |
| Tool protocol | MCP (JSON-RPC 2.0 over stdio) |
| Container | Red Hat UBI9 Python 3.11, non-root (UID 1001) |
| Container registry | Amazon ECR |
| CI/CD | GitHub Actions |
| Cloud | AWS EC2 (RHEL 10.2) |
| IAM / CI auth | GitHub OIDC → AWS IAM (no long-lived keys) |
| Remote deployment | AWS Systems Manager (SSM) |
| Orchestration | Kubernetes (Minikube) |
| Autoscaling | Kubernetes HPA + Metrics Server |
| Monitoring | kube-prometheus-stack (Prometheus, Grafana, Alertmanager, Node Exporter) |
| Secrets | AWS Secrets Manager (production) / env var (local dev) |
| Reverse proxy | NGINX Ingress Controller |
| Network proxy | socat (public exposure without a Load Balancer) |
| Init system | systemd (persistent proxy services) |

---

## The full journey

JustBooks started as an application. It became a cloud-native DevOps platform.

---

### 1. The application

JustBooks is an **agentic e-commerce bookstore**. The idea wasn't a simple search → query → result loop. It is built around an LLM-driven agent that understands natural language and invokes MCP-style tools:

- Search books and understand user intent
- Manage cart — add, remove, view
- Handle purchases and order history
- RAG/LLM-powered semantic search

Built with **Python / FastAPI** using the Groq LLM stack.

---

### 2. Containerisation

Instead of running Python directly on a server, the application was wrapped in a Docker image.

```
JustBooks source
      │
      ▼
 Dockerfile
      │
      ▼
Docker image (immutable)
      │
      ▼
Container
```

This was the first major DevOps transition: from *"I have a Python application"* to *"I have an immutable container image that can be deployed anywhere consistently."*

---

### 3. Source control — GitHub

The application lives at:

```
github.com/thefamilyman400/bookstore-agent
```

Git became the single source of truth:

```
Developer → Git → GitHub
```

---

### 4. Container registry — Amazon ECR

An ECR repository was created:

```
913524927483.dkr.ecr.ap-south-1.amazonaws.com/justbooks
```

Images are tagged with the **Git SHA** rather than `latest`:

```
justbooks:<git-sha>
```

This gives full traceability from a running Kubernetes pod back to the exact source commit:

```
Kubernetes deployment → Docker image → Git commit → Exact source code
```

---

### 5. CI/CD — GitHub Actions

A GitHub Actions pipeline builds and pushes on every `git push`:

```
git push
   │
   ▼
GitHub Actions
   │
   ├── Checkout
   ├── Authenticate to AWS (OIDC)
   ├── Build Docker image
   ├── Tag with Git SHA
   ├── Login to ECR
   └── Push image to ECR
             │
             ▼
            ECR
             │
             ▼
       SSM → EC2 → kubectl set image → Rolling update
```

---

### 6. Keyless auth — GitHub OIDC → AWS IAM

Instead of storing `AWS_ACCESS_KEY_ID` / `AWS_SECRET_ACCESS_KEY` as GitHub secrets, the pipeline uses **GitHub OIDC → AWS IAM**:

```
GitHub Actions
      │
      │ OIDC token
      ▼
AWS IAM Role
      │
      ▼
Temporary AWS credentials
```

The IAM role has only the permissions needed to push to ECR and invoke SSM. No long-lived AWS credentials exist in the CI pipeline at all.

---

### 7. The EC2 host — RHEL on AWS

Kubernetes runs on:

```
AWS EC2
RHEL 10.2
      │
      ▼
Docker
      │
      ▼
Minikube
      │
      ▼
Kubernetes
```

AWS networking and security group rules were configured to allow inbound traffic on the application and monitoring ports.

---

### 8. Kubernetes — Minikube on EC2

Minikube was chosen intentionally over EKS to learn Kubernetes fundamentals properly before moving to a managed service.

```bash
minikube start --driver=docker --cpus=2 --memory=3072
```

The resulting nesting:

```
EC2
 └── Docker
      └── Minikube container
           └── Kubernetes cluster
```

---

### 9. Kubernetes manifests for JustBooks

Proper manifests were created rather than ad-hoc `kubectl run` commands. The application runs as a genuine Kubernetes workload:

| Resource | Purpose |
|---|---|
| Namespace | `justbooks` — isolation |
| Deployment | Runs the JustBooks application pod(s) |
| Service | Stable internal DNS and networking |
| ConfigMap | Non-secret configuration |
| Secret | Sensitive configuration (JWT secret, API keys) |
| PersistentVolumeClaim | Persistent storage for the SQLite database |
| Ingress | HTTP routing via NGINX Ingress Controller |
| HPA | Horizontal Pod Autoscaler — automatic scaling |
| ImagePullSecret | Allows Kubernetes to pull the private ECR image |

All manifests live in [`k8s/`](k8s/).

---

### 10. Private ECR image pull

The cluster pulls images from a private ECR repository. A Kubernetes Docker registry secret (`ecr-registry`) was created and attached to the Deployment. The CI/CD pipeline refreshes ECR authentication credentials as part of every deployment.

---

### 11. Application deployment

The final deployed topology:

```
Deployment
     │
     ▼
JustBooks Pod
     │
     ▼
Service
     │
     ▼
Ingress
```

Kubernetes liveness and readiness probes (`/health`, `/api/info`) are configured so Kubernetes can determine pod health before routing traffic.

---

### 12. Autoscaling — HPA

The Metrics Server was installed and an HPA configured:

```
Minimum replicas: 1
Maximum replicas: 5
CPU target:       60%
```

Behaviour:

```
Normal load → 1 pod
High CPU    → 2 → 3 → 4 → 5 pods
Load drops  → scale back down
```

This was tested by deliberately generating CPU load inside a pod to verify the HPA actually responded — not just left as YAML.

---

### 13. Observability — Prometheus + Grafana

`kube-prometheus-stack` was installed, providing:

- Prometheus
- Grafana
- Alertmanager
- Node Exporter
- kube-state-metrics
- Prometheus Operator

Architecture:

```
Kubernetes
    │
    ├── Node metrics
    ├── Pod metrics
    ├── Deployment metrics
    ├── HPA metrics
    └── Cluster metrics
            │
            ▼
       Prometheus
            │
            ▼
         Grafana
```

---

### 14. Grafana persistence — PVC

During testing, an important failure was discovered: Grafana's data directory was ephemeral. After a pod restart, any UI-created dashboards disappeared.

This was fixed by enabling a **5 GiB PVC** for Grafana:

```
Grafana → PVC → Persistent storage
```

The fix was validated by rebooting the EC2 instance and confirming the PVC and dashboards survived.

---

### 15. Cross-namespace routing problem

The goal was to expose Grafana at:

```
http://<EC2-IP>:8081/grafana/
```

The problem: Grafana lives in the `monitoring` namespace while the JustBooks Ingress lives in `justbooks`. Kubernetes Services are namespace-scoped.

An initial workaround was created using a cross-namespace Service with a manually managed EndpointSlice pointing to the Grafana pod IP. It worked — until the Grafana pod restarted.

After a restart, the pod received a new IP (`10.244.0.10` instead of `10.244.0.16`) while the EndpointSlice still referenced the old IP:

```
Result: 504 Gateway Timeout
```

This was a real-world Kubernetes lesson: manually managing pod IPs breaks pod lifecycle assumptions.

---

### 16. Fix — Grafana NodePort service

Instead of tracking the pod IP manually, a NodePort service was created:

```
monitoring-grafana-nodeport → NodePort :32744 → Grafana Pods
```

Now Kubernetes handles pod endpoint updates automatically. If Grafana's pod IP changes, the NodePort stays at `32744` and Kubernetes updates the backend. That is the correct abstraction.

---

### 17. Public exposure without a Load Balancer — socat

An AWS Load Balancer was intentionally avoided for this lab environment. Instead, `socat` runs on the EC2 host to bridge public ports to the Minikube network:

**JustBooks:**
```
Internet → EC2 :8080 → socat → Minikube Ingress → JustBooks
```

**Grafana:**
```
Internet → EC2 :8081 → socat → 192.168.49.2:32744 → NodePort → Grafana
```

This works because the EC2 host can reach the Minikube Docker network directly (`192.168.49.2`).

---

### 18. Persistent proxies — systemd

Two systemd services were created to keep the proxies alive across reboots:

```
justbooks-proxy.service
grafana-proxy.service
```

Both use `Restart=always` and are enabled at boot. The full boot sequence:

```
EC2 starts
   ↓
Docker starts
   ↓
Minikube starts
   ↓
Kubernetes starts
   ↓
JustBooks / Grafana pods recover
   ↓
systemd proxies start
   ↓
Applications become reachable
```

The EC2 instance was deliberately rebooted to validate this end-to-end rather than assuming it would work.

---

### 19. Grafana sub-path configuration

Grafana runs at `/grafana/` rather than the domain root. Two configuration values were set:

```ini
serve_from_sub_path = true
root_url = http://<EC2-IP>:8081/grafana/
```

An NGINX rewrite annotation that was causing redirect loops was also identified and removed during this phase.

---

### 20. Fully automated deployment

The complete CI/CD chain on every `git push`:

```
GitHub
   │ git push
   ▼
GitHub Actions
   │ OIDC authentication
   ▼
AWS IAM (temporary credentials)
   │
   ▼
Docker build → ECR (Git SHA tag)
   │
   ▼
AWS SSM Command
   │
   ▼
EC2: kubectl set image
   │
   ▼
Kubernetes rolling update
   │
   ▼
JustBooks (new version live)
```

---

### 21. What this project covers

| Area | What was implemented |
|---|---|
| Application | FastAPI + Agentic AI (Groq / MCP) |
| Containers | Docker (UBI9, non-root) |
| Source control | Git / GitHub |
| CI/CD | GitHub Actions |
| Container registry | Amazon ECR (Git SHA tags) |
| Cloud | AWS EC2 (RHEL 10.2) |
| IAM | AWS IAM (least-privilege role) |
| CI authentication | GitHub OIDC (no long-lived keys) |
| Remote deployment | AWS Systems Manager (SSM) |
| Orchestration | Kubernetes |
| Kubernetes lab | Minikube |
| Networking | Services, Ingress, NodePort |
| Configuration | ConfigMaps |
| Secrets | Kubernetes Secrets / AWS Secrets Manager |
| Storage | PersistentVolumeClaim |
| Autoscaling | HPA + Metrics Server |
| Monitoring | Prometheus |
| Visualisation | Grafana |
| Alerting | Alertmanager |
| Linux | RHEL / systemd |
| Reverse proxy | NGINX Ingress Controller |
| Network proxy | socat |
| Reliability | Restart policies / rolling update checks |
| Deployment strategy | Rolling updates |
| Observability | Node, pod, cluster, HPA metrics |
| Troubleshooting | DNS, routing, EndpointSlices, 504s, pod restarts |
| Persistence testing | EC2 reboot validation |

These technologies were not learned in isolation. They are connected into a single working system:

```
JustBooks App
      │
   Docker
      │
   GitHub
      │
GitHub Actions
      │
  AWS OIDC
      │
    ECR
      │
    SSM
      │
    EC2
      │
Kubernetes
  ┌───┼───┐
Ingress HPA PVC
  └───┼───┘
      │
Observability
  ┌───┴───┐
Prometheus Grafana
```

The next logical step is migrating this architecture from **Minikube on EC2 → Amazon EKS**, replacing the lab-specific pieces (Minikube, socat, systemd proxies) with AWS-native managed infrastructure.

---

## Quick start (local)

### Prerequisites

- Python 3.11+
- A Groq API key (optional — app runs in mock mode without it)

### Steps

```bash
# 1. Clone and enter the repo
git clone https://github.com/thefamilyman400/bookstore-agent.git
cd bookstore-agent

# 2. Create and activate a virtual environment
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate

# 3. Install dependencies
pip install -r requirements.txt

# 4. Set up environment variables
cp .env.example .env
# Edit .env and fill in JWT_SECRET (required) and optionally GROQ_API_KEY
# Generate a strong JWT_SECRET:
python -c "import secrets; print(secrets.token_hex(32))"

# 5. Run the server
uvicorn app:app --host 127.0.0.1 --port 8000 --reload
```

Open **http://127.0.0.1:8000** in your browser.

---

## Environment variables

Copy `.env.example` to `.env` and fill in the values below.

| Variable | Required | Default | Description |
|---|---|---|---|
| `JWT_SECRET` | ✅ Yes | — | Secret key for signing JWTs. Generate with `secrets.token_hex(32)` |
| `GROQ_API_KEY` | No | — | Groq API key. If unset, the app uses a rule-based mock LLM |
| `GROQ_MODEL` | No | `llama-3.3-70b-versatile` | Override the Groq model |
| `DB_PATH` | No | `bookstore.db` (beside `app.py`) | Path to the SQLite database file |
| `LOG_LEVEL` | No | `INFO` | Log verbosity: `DEBUG`, `INFO`, `WARNING`, `ERROR` |
| `CORS_ORIGINS` | No | `*` | Comma-separated allowed origins. Set to your domain in production |
| `ACCESS_TOKEN_EXPIRE_MINUTES` | No | `60` | Access token TTL in minutes |
| `REFRESH_TOKEN_EXPIRE_DAYS` | No | `7` | Refresh token TTL in days |
| `AWS_DEFAULT_REGION` | No | `ap-south-1` | AWS region for Secrets Manager (production only) |

> **Production note:** In production the app fetches `GROQ_API_KEY` from AWS Secrets Manager automatically via the IAM role attached to the instance. Do not set `AWS_ACCESS_KEY_ID` / `AWS_SECRET_ACCESS_KEY` directly.

---

## API reference

All endpoints are prefixed with the base URL (e.g. `http://127.0.0.1:8000`).

### Auth

| Method | Path | Auth | Description |
|---|---|---|---|
| `POST` | `/api/auth/register` | None | Register a new user |
| `POST` | `/api/auth/login` | None | Login, returns access + refresh tokens |
| `POST` | `/api/auth/refresh` | Refresh token | Rotate tokens |
| `POST` | `/api/auth/logout` | Bearer token | Invalidate current token |
| `GET` | `/api/auth/me` | Bearer token | Get current user profile |

### Bookstore

| Method | Path | Auth | Description |
|---|---|---|---|
| `POST` | `/api/chat` | Bearer token | Send a chat message, get an agent reply |
| `GET` | `/api/session` | None | Generate a fresh session ID |
| `GET` | `/api/books` | None | List/search books (`?genre=&q=&limit=`) |
| `POST` | `/api/cart/add` | Bearer token | Add a book to cart |
| `GET` | `/api/cart` | Bearer token | View cart (`?session_id=`) |

### Ops

| Method | Path | Description |
|---|---|---|
| `GET` | `/health` | Liveness probe — returns `{"status":"ok"}` |
| `GET` | `/api/info` | Startup probe — returns book count, RAG backend, LLM name |

### MCP tools (via chat)

The following tools are available to the agent via the MCP server:

| Tool | Description |
|---|---|
| `search_books(query, top_k)` | Semantic RAG search |
| `get_book_detail(book_id)` | Full book info by ID (e.g. `B001`) |
| `list_genres()` | All available genres |
| `add_to_cart(session_id, book_id, quantity)` | Add to cart |
| `view_cart(session_id)` | View cart with total |
| `remove_from_cart(session_id, book_id)` | Remove item from cart |
| `create_order(session_id, delivery_address)` | Checkout |
| `get_order_history(session_id)` | Past orders |
| `cancel_order(session_id, order_id)` | Cancel within 48 hours |

---

## Running with Docker

```bash
# Build the image
docker build -t justbooks:latest .

# Run the container
docker run -p 8000:8000 \
  -e JWT_SECRET=$(python3 -c "import secrets; print(secrets.token_hex(32))") \
  -e GROQ_API_KEY=your_key_here \
  -v justbooks-db:/data/db \
  justbooks:latest
```

Open **http://127.0.0.1:8000**.

The image is based on `registry.access.redhat.com/ubi9/python-311` and runs as non-root user UID 1001.

---

## Deploying to Kubernetes (Minikube)

All manifests live in the [`k8s/`](k8s/) directory.

### Prerequisites

- [Docker Desktop](https://www.docker.com/products/docker-desktop)
- [Minikube](https://minikube.sigs.k8s.io/docs/start)
- [kubectl](https://kubernetes.io/docs/tasks/tools)

### Steps

```bash
# 1. Start Minikube
minikube start --driver=docker --cpus=2 --memory=4096

# 2. Enable required addons
minikube addons enable ingress
minikube addons enable metrics-server

# 3. Build the image inside Minikube's Docker daemon
eval $(minikube docker-env)          # macOS/Linux
# minikube docker-env | Invoke-Expression    # Windows PowerShell
docker build -t justbooks:latest .

# 4. Create the JWT secret (do NOT apply k8s/secrets.yaml as-is)
kubectl create secret generic justbooks-secret \
  --from-literal=JWT_SECRET=$(python3 -c "import secrets; print(secrets.token_hex(32))")

# 5. Apply all other manifests
kubectl apply -f k8s/

# 6. Add hosts entry so your browser resolves justbooks.local
echo "127.0.0.1 justbooks.local" | sudo tee -a /etc/hosts
# Windows (run as Administrator):
# Add-Content C:\Windows\System32\drivers\etc\hosts "127.0.0.1 justbooks.local"

# 7. Start the tunnel (keep this terminal open)
minikube tunnel
```

Open **http://justbooks.local**.

### Verify the deployment

```bash
kubectl get pods -n justbooks
kubectl get services -n justbooks
kubectl get ingress -n justbooks
```

### Useful kubectl commands

```bash
# Stream logs from a pod
kubectl logs -f <pod-name> -n justbooks

# Open a shell inside a running pod
kubectl exec -it <pod-name> -n justbooks -- /bin/bash

# Describe a pod (events, probe failures, OOM kills)
kubectl describe pod <pod-name> -n justbooks

# Port-forward as a fallback (bypasses Ingress)
kubectl port-forward svc/justbooks 8080:80 -n justbooks
# then open http://localhost:8080
```

---

## Project structure

```
bookstore-agent/
├── app.py                  # FastAPI app — routes, CORS, startup secrets loader
├── auth_service.py         # JWT auth — register, login, refresh, logout, me
├── db.py                   # SQLite helpers — users, cart, orders
├── mcp_server.py           # MCP server — JSON-RPC 2.0 over stdio, 9 tools
├── rag_store.py            # RAG vector store — sentence-transformers / TF-IDF
├── agents/
│   ├── orchestrator.py     # Intent classifier → routes to specialist agents
│   ├── base_agent.py       # MCPClient + BaseAgent (Groq tool-call loop)
│   ├── catalogue_agent.py  # Handles search, details, genre queries
│   ├── cart_agent.py       # Handles cart add/remove/view
│   └── order_agent.py      # Handles checkout, history, cancellation
├── static/
│   └── index.html          # Single-page chat UI
├── data/
│   └── books.json          # Book catalogue (loaded at startup)
├── k8s/
│   ├── namespace.yaml
│   ├── configmap.yaml
│   ├── secrets.yaml
│   ├── pvc.yaml
│   ├── deployment.yaml
│   ├── service.yaml
│   ├── ingress.yaml
│   └── hpa.yaml
├── .github/
│   └── workflows/
│       └── deploy.yml      # GitHub Actions CI/CD pipeline
├── Dockerfile              # Multi-stage, UBI9 Python 3.11, non-root UID 1001
├── requirements.txt
└── .env.example
```
