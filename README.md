# VisionAI Aegis

Enterprise-grade AI-powered video surveillance and analytics platform. VisionAI Aegis provides real-time intelligent video analysis with computer vision, face recognition, license plate recognition, anomaly detection, and comprehensive reporting -- all orchestrated through a modern web dashboard.

**Author:** Sherin Joseph Roy

---

## Table of Contents

- [Overview](#overview)
- [Architecture](#architecture)
- [Technology Stack](#technology-stack)
- [Features](#features)
- [Project Structure](#project-structure)
- [Prerequisites](#prerequisites)
- [Installation](#installation)
- [Configuration](#configuration)
- [Running the Platform](#running-the-platform)
- [API Documentation](#api-documentation)
- [Computer Vision Pipeline](#computer-vision-pipeline)
- [Frontend Dashboard](#frontend-dashboard)
- [Background Processing](#background-processing)
- [Infrastructure Services](#infrastructure-services)
- [GPU Support](#gpu-support)
- [Environment Variables](#environment-variables)
- [License](#license)

---

## Overview

VisionAI Aegis is a full-stack, production-ready video surveillance platform that combines deep learning-based computer vision with a scalable microservices backend and a responsive web dashboard. The system is designed for organizations that require intelligent monitoring, automated alerting, and data-driven insights from their camera infrastructure.

Key capabilities:

- **Real-time object detection** using YOLOv8 with ONNX Runtime inference
- **Face recognition** with enrollment, search, and watchlist management
- **License plate recognition** (ANPR) with vehicle tracking and classification
- **Anomaly detection** using statistical and ML-based behavioral analysis
- **Person re-identification** across multiple cameras using deep embeddings
- **Semantic search** powered by CLIP embeddings and pgvector
- **Predictive analytics** with trend forecasting and pattern recognition
- **Multi-site federation** for distributed camera networks
- **AI Copilot** powered by Anthropic Claude for natural language querying
- **Edge device management** for distributed deployments

---

## Architecture

```
                         +------------------+
                         |     Nginx        |
                         |  Reverse Proxy   |
                         |  (SSL/TLS, LB)   |
                         +--------+---------+
                                  |
                     +------------+------------+
                     |                         |
              +------+------+          +-------+-------+
              |   FastAPI   |          |    Next.js    |
              |   Backend   |          |   Dashboard   |
              |  (Port 8000)|          |  (Port 3000)  |
              +------+------+          +---------------+
                     |
         +-----------+-----------+-----------+
         |           |           |           |
   +-----+----+ +---+----+ +---+----+ +----+-----+
   |PostgreSQL | | Redis  | | MinIO  | | MediaMTX |
   | + pgvector| | Cache  | | Object | |  RTSP/   |
   |           | | Queue  | | Storage| |   HLS    |
   +----------+  +--------+ +--------+ +----------+
         |
   +-----+----+     +-----------+
   |  Celery   |     |  Celery   |
   |  Worker   |     |   Beat    |
   | (CV Tasks)|     | (Scheduler)|
   +----------+      +-----------+
```

The platform runs natively as microservices connecting FastAPI (Backend) and Next.js (Frontend) to infrastructure services:

| Service      | Role                                              | Port(s)         |
|-------------|---------------------------------------------------|-----------------|
| **api**      | FastAPI REST API and WebSocket server              | 8000            |
| **frontend** | Next.js server-side rendered dashboard             | 3000            |
| **postgres** | PostgreSQL 16 with pgvector extension              | 5432            |
| **redis**    | Caching, Celery message broker, pub/sub            | 6379            |
| **minio**    | S3-compatible object storage (images, video, models) | 9000, 9001    |
| **mediamtx** | RTSP/HLS/WebRTC media streaming server             | 8554, 8888, 8889 |
| **worker**   | Celery workers for CV inference and background jobs | --              |
| **beat**     | Celery Beat periodic task scheduler                | --              |

---

## Technology Stack

### Backend (Python 3.11+)

| Category             | Technology                                           |
|---------------------|------------------------------------------------------|
| Web Framework        | FastAPI with async/await, OpenAPI auto-docs          |
| ORM / Database       | SQLAlchemy 2.0 (async), asyncpg, Alembic migrations |
| Vector Search        | pgvector for face/CLIP embedding similarity          |
| Authentication       | JWT (python-jose), bcrypt password hashing           |
| Task Queue           | Celery 5.4+ with Redis broker                       |
| Object Storage       | MinIO (miniopy-async SDK)                            |
| Structured Logging   | structlog with JSON output in production             |
| Monitoring           | Prometheus client metrics                            |
| Email                | aiosmtplib for async SMTP                            |
| Reports              | WeasyPrint (PDF), ReportLab, OpenPyXL (Excel)       |
| AI Integration       | Anthropic SDK for Claude-based copilot               |

### Computer Vision (Python)

| Category             | Technology                                           |
|---------------------|------------------------------------------------------|
| Inference Engine     | ONNX Runtime (CPU and CUDA execution providers)      |
| Object Detection     | YOLOv8 (Ultralytics)                                |
| Face Detection       | RetinaFace                                           |
| Face Recognition     | InsightFace (ArcFace embeddings)                     |
| OCR                  | PaddleOCR for license plate characters               |
| Semantic Search      | OpenAI CLIP for visual-semantic embeddings           |
| Multi-Object Tracking| ByteTrack and SORT algorithms                        |
| Image Processing     | OpenCV, Pillow, NumPy, SciPy                        |

### Frontend (Node.js 20+)

| Category             | Technology                                           |
|---------------------|------------------------------------------------------|
| Framework            | Next.js 14 with App Router (SSR/SSG)                |
| UI Library           | React 18                                             |
| Type System          | TypeScript 5.6                                       |
| Component Library    | Radix UI primitives (shadcn/ui)                      |
| Styling              | Tailwind CSS 3.4                                     |
| State Management     | Zustand 5                                            |
| Data Fetching        | TanStack React Query 5                               |
| HTTP Client          | Axios                                                |
| Forms                | React Hook Form + Zod validation                     |
| Video Streaming      | HLS.js for HTTP Live Streaming                       |
| Charts               | Recharts                                             |
| Icons                | Lucide React                                         |
| Theming              | next-themes (light/dark mode)                        |

### Infrastructure

| Category             | Technology                                           |
|---------------------|------------------------------------------------------|
| Containerization     | Docker, Docker Compose                               |
| Reverse Proxy        | Nginx with TLS, gzip, rate limiting                  |
| Database             | PostgreSQL 16 + pgvector                             |
| Cache / Queue        | Redis 7 (Alpine) with AOF persistence                |
| Object Storage       | MinIO (S3-compatible)                                |
| Media Server         | MediaMTX (RTSP, HLS, WebRTC)                        |
| GPU Support          | NVIDIA CUDA with Docker GPU passthrough              |

---

## Features

### Camera Management
- ONVIF auto-discovery and configuration
- RTSP/HLS live streaming through MediaMTX
- MJPEG snapshot streaming
- PTZ (pan-tilt-zoom) controls
- Camera health monitoring with heartbeat checks
- Multi-camera grid view with real-time status

### Detection and Recognition
- Real-time object detection (people, vehicles, objects) via YOLOv8
- Face detection, recognition, enrollment, and watchlist alerts
- License plate recognition (ANPR) with vehicle classification
- PPE compliance detection (helmet, vest)
- Fire and smoke detection
- Camera tampering detection

### Analytics and Intelligence
- Footfall counting with hourly/daily/weekly breakdowns
- Activity heatmap generation per camera
- Dwell time analytics
- Crowd density estimation and flow analysis
- Occupancy monitoring with threshold alerts
- Behavioral anomaly detection
- Predictive trend analysis and forecasting
- Person re-identification across camera feeds
- CLIP-based semantic search across recorded footage
- Emotion classification from facial analysis
- Attendance tracking with department-level reporting

### Alert and Rule Engine
- 22 configurable detection rule types across 7 categories:
  - **Security:** Intrusion detection, loitering, crowd formation, object left behind, object removed, wrong direction, no entry zone, tailgating
  - **Counting:** Line crossing
  - **Face Recognition:** Known face, unknown face, blacklisted face
  - **Vehicle:** Blacklisted vehicle, unknown vehicle, speed violation, illegal parking
  - **Safety:** PPE violation, fire/smoke, fall detection, violence detection
  - **Occupancy:** Threshold breach
  - **System:** Camera tampering
- Five severity levels: critical, high, medium, low, info
- Zone-based rule binding with polygon regions
- Configurable cooldown periods and cron-based schedules
- Multi-channel alert dispatch (email, webhook, WebSocket, in-app)
- Alert batching and acknowledgment workflows

### Zone Management
- Interactive polygon drawing on camera snapshots
- Zone types: detection, exclusion, counting line, intrusion, loitering, PPE
- Visual zone editor with drag-to-adjust vertices
- Color-coded zone overlays

### Reporting
- PDF and Excel report generation
- Scheduled recurring reports via cron expressions
- Attendance export (CSV)
- Audit log export (CSV)
- Incident report management with severity tracking

### Administration
- Role-based access control (viewer, operator, manager, org_admin, super_admin)
- Multi-tenant organization support
- User management with bulk operations and status toggling
- API key management for programmatic access
- Comprehensive audit logging
- System configuration management
- Third-party integration management (webhooks, external APIs)
- Edge device registration and management

### AI Copilot
- Natural language interface for querying surveillance data
- Powered by Anthropic Claude
- Context-aware responses based on camera feeds and analytics

### Real-Time Communication
- WebSocket connections for live alert streaming
- Redis pub/sub for cross-service event propagation
- Server-sent events for dashboard updates

### Multi-Site Federation
- Distributed camera network management
- Cross-site analytics aggregation
- Federated search across sites

---

## Project Structure

```
VisionAI/
|
|-- backend/                          # FastAPI backend application
|   |-- app/
|   |   |-- api/v1/                   # API routers (28 modules)
|   |   |   |-- auth.py              # Authentication (login, refresh, JWT)
|   |   |   |-- admin.py             # Admin operations (users, config, audit)
|   |   |   |-- cameras.py           # Camera CRUD, streaming, ONVIF
|   |   |   |-- alerts.py            # Alert management and dispatch
|   |   |   |-- analytics.py         # Analytics (footfall, heatmap, dwell, attendance)
|   |   |   |-- recordings.py        # Video recording management
|   |   |   |-- faces.py             # Face enrollment, recognition, search
|   |   |   |-- vehicles.py          # Vehicle detection, ANPR
|   |   |   |-- rules.py             # Detection rule configuration
|   |   |   |-- zones.py             # Zone polygon management
|   |   |   |-- anomalies.py         # Anomaly detection results
|   |   |   |-- predictions.py       # Predictive analytics
|   |   |   |-- reid.py              # Person re-identification
|   |   |   |-- search.py            # CLIP-based semantic search
|   |   |   |-- reports.py           # Report generation and scheduling
|   |   |   |-- incident_reports.py  # Incident management
|   |   |   |-- floor_plans.py       # Floor plan management
|   |   |   |-- notifications.py     # Notification rules
|   |   |   |-- webhooks.py          # Webhook configuration
|   |   |   |-- websocket.py         # WebSocket real-time connections
|   |   |   |-- copilot.py           # AI assistant (Anthropic Claude)
|   |   |   |-- federation.py        # Multi-site federation
|   |   |   |-- edge.py              # Edge device management
|   |   |   |-- integrations.py      # Third-party integrations
|   |   |   |-- dashboard.py         # Dashboard summary
|   |   |   |-- departments.py       # Department management
|   |   |   |-- events.py            # Event streaming
|   |   |   +-- router.py            # Router aggregator
|   |   |
|   |   |-- models/                   # SQLAlchemy ORM models (25 tables)
|   |   |   |-- user.py, organization.py, camera.py, recording.py
|   |   |   |-- face.py, person.py, vehicle.py, alert.py
|   |   |   |-- anomaly.py, analytics.py, prediction.py, attendance.py
|   |   |   |-- zone.py, rule.py, floor_plan.py, webhook.py
|   |   |   |-- integration.py, incident_report.py, federation.py
|   |   |   |-- edge_device.py, clip_search.py
|   |   |   +-- ...
|   |   |
|   |   |-- services/                 # Business logic layer (32 services)
|   |   |   |-- auth_service.py      # JWT, password hashing, sessions
|   |   |   |-- camera_service.py    # Camera lifecycle, ONVIF
|   |   |   |-- stream_manager.py    # Video stream management
|   |   |   |-- storage_service.py   # MinIO/S3 operations
|   |   |   |-- face_service.py      # Face recognition pipeline
|   |   |   |-- vehicle_service.py   # Vehicle detection and ANPR
|   |   |   |-- rule_engine.py       # Rule evaluation and execution
|   |   |   |-- alert_dispatcher.py  # Alert routing and delivery
|   |   |   |-- analytics_service.py # Analytics aggregation
|   |   |   |-- anomaly_service.py   # Anomaly detection algorithms
|   |   |   |-- predictive_service.py# Predictive analytics engine
|   |   |   |-- reid_service.py      # Person re-identification
|   |   |   |-- clip_search_service.py# CLIP semantic search
|   |   |   |-- report_service.py    # PDF/Excel report generation
|   |   |   |-- email_service.py     # SMTP email
|   |   |   |-- notification_service.py
|   |   |   |-- webhook_service.py
|   |   |   |-- copilot_service.py   # AI assistant with context
|   |   |   |-- federation_service.py
|   |   |   |-- encryption_service.py
|   |   |   |-- audit_service.py
|   |   |   +-- ...
|   |   |
|   |   |-- cv/                       # Computer vision pipeline (28 modules)
|   |   |   |-- pipeline.py          # End-to-end processing pipeline
|   |   |   |-- inference_engine.py  # ONNX Runtime inference
|   |   |   |-- model_registry.py    # Model management and versioning
|   |   |   |-- object_detector.py   # YOLOv8 detection
|   |   |   |-- face_detector.py     # RetinaFace detection
|   |   |   |-- face_recognizer.py   # ArcFace embeddings
|   |   |   |-- plate_detector.py    # License plate detection
|   |   |   |-- plate_ocr.py         # Plate character recognition
|   |   |   |-- pose_detector.py     # Skeleton detection
|   |   |   |-- ppe_detector.py      # PPE compliance
|   |   |   |-- fire_detector.py     # Fire/smoke detection
|   |   |   |-- reid_encoder.py      # Re-ID embeddings
|   |   |   |-- clip_encoder.py      # CLIP embeddings
|   |   |   |-- vehicle_classifier.py# Vehicle type/color
|   |   |   |-- emotion_classifier.py# Facial emotion detection
|   |   |   |-- byte_tracker.py      # ByteTrack MOT
|   |   |   |-- sort_tracker.py      # SORT tracker
|   |   |   |-- anomaly_detector.py  # Behavioral anomaly detection
|   |   |   |-- crowd_analyzer.py    # Crowd density and flow
|   |   |   |-- heatmap_generator.py # Activity heatmaps
|   |   |   |-- tamper_detector.py   # Camera tamper detection
|   |   |   |-- zone_analyzer.py     # Zone intrusion/counting
|   |   |   +-- behavior_analyzer.py # Behavior analysis
|   |   |
|   |   |-- workers/                  # Celery background tasks (17 modules)
|   |   |   |-- celery_app.py        # Celery configuration
|   |   |   |-- video_tasks.py       # Frame extraction and processing
|   |   |   |-- alert_tasks.py       # Alert generation and dispatch
|   |   |   |-- recording_tasks.py   # Recording segmentation
|   |   |   |-- analytics_tasks.py   # Analytics computation
|   |   |   |-- anomaly_tasks.py     # Anomaly detection jobs
|   |   |   |-- prediction_tasks.py  # Model inference
|   |   |   |-- clip_tasks.py        # CLIP embedding generation
|   |   |   |-- reid_tasks.py        # Re-identification jobs
|   |   |   |-- webhook_tasks.py     # Webhook delivery
|   |   |   |-- report_tasks.py      # Report generation
|   |   |   |-- federation_tasks.py  # Federation sync
|   |   |   |-- edge_tasks.py        # Edge device tasks
|   |   |   |-- maintenance_tasks.py # Cleanup and maintenance
|   |   |   +-- incident_report_tasks.py
|   |   |
|   |   |-- schemas/                  # Pydantic request/response schemas (21 files)
|   |   |-- middleware/               # CORS, auth, logging, rate limiting
|   |   |-- utils/                    # Validators, image/video utils, geometry
|   |   |-- config.py                # Application settings (Pydantic Settings)
|   |   |-- database.py              # SQLAlchemy async engine
|   |   |-- dependencies.py          # FastAPI dependency injection
|   |   |-- exceptions.py            # Custom exception handlers
|   |   +-- main.py                  # Application factory
|   |
|   |-- alembic/                      # Database migrations
|   |   +-- versions/
|   |       +-- 001_initial_schema.py
|   |
|   |-- tests/                        # pytest test suite
|   +-- requirements.txt              # Python dependencies (81 packages)
|
|-- frontend/                         # Next.js dashboard application
|   |-- src/
|   |   |-- app/                      # Next.js App Router pages
|   |   |   |-- (auth)/              # Authentication pages
|   |   |   |   |-- login/page.tsx
|   |   |   |   +-- forgot-password/page.tsx
|   |   |   |
|   |   |   +-- dashboard/           # Protected dashboard pages
|   |   |       |-- page.tsx                          # Dashboard home
|   |   |       |-- live/page.tsx                     # Live camera feeds
|   |   |       |-- cameras/page.tsx                  # Camera list
|   |   |       |-- cameras/[id]/page.tsx             # Camera detail
|   |   |       |-- alerts/page.tsx                   # Alert management
|   |   |       |-- recordings/page.tsx               # Recording library
|   |   |       |-- incident-reports/page.tsx         # Incident management
|   |   |       |-- reports/page.tsx                  # Report generation
|   |   |       |-- faces/page.tsx                    # Face database
|   |   |       |-- faces/enroll/page.tsx             # Face enrollment
|   |   |       |-- faces/search/page.tsx             # Face search
|   |   |       |-- vehicles/page.tsx                 # Vehicle tracking
|   |   |       |-- vehicles/logs/page.tsx            # Vehicle logs
|   |   |       |-- floor-plan/page.tsx               # Floor plan editor
|   |   |       |-- search/page.tsx                   # Semantic search
|   |   |       |-- copilot/page.tsx                  # AI assistant
|   |   |       |-- federation/page.tsx               # Multi-site management
|   |   |       |-- analytics/
|   |   |       |   |-- footfall/page.tsx             # Footfall analytics
|   |   |       |   |-- heatmap/page.tsx              # Heatmap visualization
|   |   |       |   |-- attendance/page.tsx           # Attendance tracking
|   |   |       |   |-- reid/page.tsx                 # Re-identification
|   |   |       |   |-- ppe/page.tsx                  # PPE compliance
|   |   |       |   |-- anomalies/page.tsx            # Anomaly detection
|   |   |       |   |-- patterns/page.tsx             # Behavior patterns
|   |   |       |   +-- predictions/page.tsx          # Predictive analytics
|   |   |       |
|   |   |       +-- admin/
|   |   |           |-- settings/page.tsx             # System settings
|   |   |           |-- users/page.tsx                # User management
|   |   |           |-- audit/page.tsx                # Audit logs
|   |   |           |-- integrations/page.tsx         # Integration config
|   |   |           |-- notifications/page.tsx        # Notification rules
|   |   |           +-- edge/page.tsx                 # Edge devices
|   |   |
|   |   |-- components/               # React components (39 files)
|   |   |   |-- ui/                   # shadcn/ui primitives (17 components)
|   |   |   |-- layout/              # Header, sidebar, breadcrumb
|   |   |   |-- camera/              # Stream player, PTZ, zone editor, dialogs
|   |   |   |-- analytics/           # Heatmap, footfall chart, PPE gauge
|   |   |   |-- alerts/              # Alert card, feed, detail modal
|   |   |   +-- common/              # Data table, date picker, export button
|   |   |
|   |   |-- hooks/                    # Custom React hooks
|   |   |   |-- use-auth.ts          # Authentication state
|   |   |   |-- use-camera-stream.ts # Camera stream management
|   |   |   +-- use-websocket.ts     # WebSocket connection
|   |   |
|   |   |-- stores/                   # Zustand state management
|   |   |   |-- auth-store.ts        # Auth state and tokens
|   |   |   |-- camera-store.ts      # Camera list state
|   |   |   +-- alert-store.ts       # Alert state
|   |   |
|   |   |-- lib/                      # Utility libraries
|   |   |   |-- api-client.ts        # Axios HTTP client with auth interceptors
|   |   |   |-- auth.ts              # Token storage and JWT parsing
|   |   |   |-- websocket.ts         # WebSocket client with reconnection
|   |   |   +-- utils.ts             # Formatting and validation helpers
|   |   |
|   |   +-- types/                    # TypeScript type definitions
|   |       |-- api.ts, camera.ts, alert.ts, analytics.ts
|   |       +-- ...
|   |
|   |-- package.json                  # Node.js dependencies
|   |-- next.config.js                # Next.js configuration
|   |-- tailwind.config.js            # Tailwind CSS theming
|   +-- tsconfig.json                 # TypeScript configuration
|
|-- nginx/
|   +-- nginx.conf                    # Nginx reverse proxy configuration
|
|-- docker/
|   +-- Dockerfile.edge               # Edge device deployment image
|
|-- scripts/
|   |-- download_models.py            # Download pre-trained CV models
|   |-- run_migrations.sh             # Database migration runner
|   +-- seed_data.py                  # Test data population
|
|-- models/                           # Pre-trained model weights (volume mount)
|
|-- Dockerfile.api                    # API server Docker image
|-- Dockerfile.worker                 # Celery worker Docker image (with CV deps)
|-- Dockerfile.frontend               # Next.js Docker image
|-- docker-compose.yml                # Main orchestration (9 services)
|-- docker-compose.gpu.yml            # GPU-accelerated configuration
|-- .env.example                      # Environment variable template
+-- .gitignore                        # Git ignore rules
```

---

## Prerequisites

- **Python** 3.11+ (Python 3.13 supported)
- **Node.js** 20+ (Node 22 supported) & npm
- **Git** 2.30+
- (Optional) **PostgreSQL 16 with pgvector**, **Redis**, **MinIO**, **MediaMTX**
- Minimum 8 GB RAM (16 GB recommended for production)
- Minimum 20 GB disk space

---

## Installation & Native Execution

### 1. Run Automatic Local Setup Script (Windows PowerShell)

```powershell
.\scripts\setup_env.ps1
```
This script will:
- Create a Python virtual environment (`venv`)
- Install all backend dependencies from `backend/requirements.txt`
- Install all frontend dependencies via `npm install` inside `frontend/`
- Generate `.env` from `.env.example`

### 2. Configure Environment

Edit `.env` and update the required values for your local setup:
- `DATABASE_URL` (e.g. `postgresql+asyncpg://visionai:visionai_secret@localhost:5432/visionai`)
- `REDIS_URL` (e.g. `redis://:visionai_redis@localhost:6379/0`)
- `SECRET_KEY` & `JWT_SECRET_KEY`
- `ADMIN_EMAIL` & `ADMIN_PASSWORD`

### 3. Run Database Migrations

```powershell
# Activate venv and run Alembic migrations
.\venv\Scripts\Activate.ps1
cd backend
alembic upgrade head
cd ..
```

### 4. Start Backend Server

```powershell
.\scripts\start_backend.ps1
```
The FastAPI backend server will start at `http://localhost:8000`. Swagger API docs will be available at `http://localhost:8000/docs`.

### 5. Start Frontend Server

Open a second PowerShell terminal window:
```powershell
.\scripts\start_frontend.ps1
```
The Next.js frontend application will start at `http://localhost:3000`.

---

## Access the Platform

| Interface           | URL                          |
|--------------------|------------------------------|
| Dashboard UI       | http://localhost:3000         |
| API Documentation  | http://localhost:8000/docs    |
| ReDoc API Specs    | http://localhost:8000/redoc   |

Default login credentials are defined by `ADMIN_EMAIL` and `ADMIN_PASSWORD` in your `.env` file.

---

## Configuration

### Environment Variables

The platform is configured entirely through environment variables. See `.env.example` for the full list. Key sections:

| Section              | Variables                                                |
|---------------------|----------------------------------------------------------|
| Application          | `APP_ENV`, `SECRET_KEY`, `JWT_SECRET_KEY`, `LOG_LEVEL`  |
| Database             | `DATABASE_URL`, `POSTGRES_USER`, `POSTGRES_PASSWORD`    |
| Redis                | `REDIS_URL`, `REDIS_PASSWORD`                           |
| Celery               | `CELERY_BROKER_URL`, `CELERY_WORKER_CONCURRENCY`       |
| MinIO                | `MINIO_ENDPOINT`, `MINIO_ACCESS_KEY`, `MINIO_SECRET_KEY`|
| MediaMTX             | `MEDIAMTX_API_URL`, `RTSP_PORT`, `HLS_PORT`           |
| CV / GPU             | `CV_USE_GPU`, `CV_MODELS_PATH`, `CV_GPU_DEVICE_ID`    |
| Detection Thresholds | `DETECTION_CONFIDENCE_THRESHOLD`, `FACE_RECOGNITION_THRESHOLD` |
| Alerts               | `ALERT_COOLDOWN_SECONDS`, `ALERT_MAX_PER_CAMERA_PER_HOUR` |
| SMTP                 | `SMTP_HOST`, `SMTP_PORT`, `SMTP_USER`, `SMTP_PASSWORD` |
| AI Copilot           | `ANTHROPIC_API_KEY`                                     |

---

## Running the Platform

### Start All Services

```bash
docker compose up -d
```

### View Logs

```bash
# All services
docker compose logs -f

# Specific service
docker compose logs -f api
docker compose logs -f worker
docker compose logs -f frontend
```

### Stop All Services

```bash
docker compose down
```

### Restart a Single Service

```bash
docker compose restart api
```

### Rebuild After Code Changes

```bash
docker compose build api frontend
docker compose up -d api frontend
```

---

## API Documentation

The backend exposes a comprehensive REST API at `/api/v1/` with 28 router modules and over 200 endpoints. Full interactive documentation is available at `/docs` (Swagger UI) and `/redoc` (ReDoc) when running in development mode.

### Core Endpoint Groups

| Prefix                    | Description                              |
|--------------------------|------------------------------------------|
| `/api/v1/auth/`          | Login, token refresh, password management |
| `/api/v1/admin/`         | User management, system config, audit logs|
| `/api/v1/cameras/`       | Camera CRUD, streaming, snapshots        |
| `/api/v1/cameras/{id}/zones/` | Zone polygon management per camera   |
| `/api/v1/rules/`         | Detection rule configuration             |
| `/api/v1/alerts/`        | Alert listing, acknowledgment, batching  |
| `/api/v1/recordings/`    | Recording management and download        |
| `/api/v1/analytics/`     | Footfall, heatmaps, dwell time, occupancy|
| `/api/v1/faces/`         | Face enrollment, recognition, search     |
| `/api/v1/vehicles/`      | Vehicle tracking, ANPR, classification   |
| `/api/v1/anomalies/`     | Anomaly detection results and baselines  |
| `/api/v1/predictions/`   | Predictive analytics and trend forecasts |
| `/api/v1/reid/`          | Person re-identification tracking        |
| `/api/v1/search/`        | CLIP-based semantic search               |
| `/api/v1/reports/`       | Report generation and scheduling         |
| `/api/v1/incident-reports/` | Incident management                   |
| `/api/v1/notifications/` | Notification rule management             |
| `/api/v1/webhooks/`      | Outbound webhook configuration           |
| `/api/v1/copilot/`       | AI assistant queries                     |
| `/api/v1/federation/`    | Multi-site management                    |
| `/api/v1/edge/`          | Edge device registration                 |
| `/api/v1/integrations/`  | Third-party integration config           |
| `/api/v1/floor-plans/`   | Floor plan upload and zone mapping       |
| `/api/v1/ws/`            | WebSocket real-time connections           |

### Health Check Endpoints

| Endpoint         | Description                    |
|-----------------|--------------------------------|
| `/health`        | Basic application health       |
| `/health/db`     | Database connectivity          |
| `/health/redis`  | Redis connectivity             |
| `/health/gpu`    | GPU availability and memory    |

### Authentication

All API endpoints (except `/health` and `/api/v1/auth/login`) require a JWT bearer token:

```bash
# Obtain token
curl -X POST https://localhost/api/v1/auth/login \
  -H "Content-Type: application/json" \
  -d '{"email": "admin@visionai.local", "password": "your-password"}'

# Use token
curl https://localhost/api/v1/cameras/ \
  -H "Authorization: Bearer <access_token>"
```

---

## Computer Vision Pipeline

The CV pipeline is located in `backend/app/cv/` and processes video frames through a modular architecture:

```
Video Frame
    |
    v
[Inference Engine] -- ONNX Runtime (CPU/CUDA)
    |
    +-- [Object Detector] -- YOLOv8 (people, vehicles, objects)
    |       |
    |       +-- [ByteTrack/SORT] -- Multi-object tracking
    |       +-- [Zone Analyzer] -- Intrusion, counting, loitering
    |       +-- [Crowd Analyzer] -- Density estimation
    |       +-- [Behavior Analyzer] -- Event detection
    |
    +-- [Face Detector] -- RetinaFace
    |       |
    |       +-- [Face Recognizer] -- ArcFace embeddings + matching
    |       +-- [Emotion Classifier] -- Facial emotion detection
    |
    +-- [Plate Detector] -- License plate localization
    |       |
    |       +-- [Plate OCR] -- Character recognition (PaddleOCR)
    |       +-- [Vehicle Classifier] -- Type, color, make
    |
    +-- [PPE Detector] -- Helmet, vest detection
    +-- [Fire Detector] -- Fire and smoke detection
    +-- [Pose Detector] -- Skeleton detection (fall detection)
    +-- [CLIP Encoder] -- Semantic embeddings for search
    +-- [ReID Encoder] -- Person re-identification embeddings
    +-- [Tamper Detector] -- Camera obstruction/movement
    +-- [Anomaly Detector] -- Behavioral anomaly scoring
    +-- [Heatmap Generator] -- Activity accumulation maps
```

### Supported Models

| Model               | Task                      | Format | Default Precision |
|---------------------|---------------------------|--------|------------------|
| YOLOv8n/s/m/l       | Object detection          | ONNX   | FP32 / FP16      |
| RetinaFace           | Face detection            | ONNX   | FP32             |
| ArcFace (R50/R100)   | Face recognition          | ONNX   | FP32             |
| CLIP ViT-B/32        | Semantic embeddings       | ONNX   | FP32             |
| OSNet                | Person re-identification  | ONNX   | FP32             |
| PaddleOCR            | License plate OCR         | ONNX   | FP32             |
| Custom YOLOv8        | PPE detection             | ONNX   | FP32             |
| Custom YOLOv8        | Fire/smoke detection      | ONNX   | FP32             |

---

## Frontend Dashboard

The frontend is a Next.js 14 application with 30+ pages organized into functional areas:

### Page Categories

**Monitoring:**
Live camera feeds, camera detail views with streaming, PTZ controls, recording playback.

**Analytics:**
Footfall analysis with time-series charts, heatmap visualization overlaid on camera views, attendance tracking with department breakdowns, person re-identification timelines, PPE compliance dashboards, anomaly detection displays, behavioral pattern analysis, and predictive trend forecasting.

**Management:**
Alert management with filtering and acknowledgment, incident report creation and tracking, face database with enrollment and search, vehicle tracking with license plate logs, recording library with timeline navigation, report generation with scheduling.

**Configuration:**
Camera configuration with ONVIF setup, zone polygon editor with interactive drawing canvas, detection rule builder with 22 rule types, floor plan editor with zone overlays, notification rule configuration, webhook management, third-party integration setup.

**Administration:**
User management with role-based access, system settings, audit log viewer with CSV export, edge device management, multi-site federation dashboard.

### Component Architecture

The UI is built on shadcn/ui (Radix UI primitives styled with Tailwind CSS) providing consistent, accessible components. State is managed through Zustand stores for global state and TanStack React Query for server state with automatic cache invalidation. Real-time updates flow through WebSocket connections managed by a custom hook with automatic reconnection.

---

## Background Processing

### Celery Workers

The platform uses Celery with Redis as the message broker for asynchronous task processing. Workers handle computationally intensive operations off the main API thread:

| Task Module           | Responsibility                              |
|----------------------|---------------------------------------------|
| `video_tasks`        | Frame extraction and CV pipeline dispatch    |
| `alert_tasks`        | Alert generation, routing, and delivery      |
| `recording_tasks`    | Video segment creation and MinIO upload      |
| `analytics_tasks`    | Hourly/daily analytics aggregation           |
| `anomaly_tasks`      | Anomaly detection batch processing           |
| `prediction_tasks`   | Predictive model inference                   |
| `clip_tasks`         | CLIP embedding generation for search index   |
| `reid_tasks`         | Person re-identification across cameras      |
| `webhook_tasks`      | Webhook delivery with exponential retry      |
| `report_tasks`       | Scheduled PDF/Excel report generation        |
| `federation_tasks`   | Multi-site data synchronization              |
| `edge_tasks`         | Edge device health checks and sync           |
| `maintenance_tasks`  | Data cleanup, retention enforcement           |
| `incident_report_tasks` | Automated incident report generation      |

### Celery Beat Scheduler

Periodic tasks are configured through Celery Beat for recurring operations such as:

- Camera health checks (every 60 seconds)
- Analytics aggregation (hourly)
- Anomaly baseline recalculation (daily)
- Recording retention cleanup (daily)
- Prediction model retraining (weekly)
- Federation sync (every 5 minutes)

---

## Infrastructure Services

### PostgreSQL with pgvector

PostgreSQL 16 serves as the primary datastore with the pgvector extension enabling efficient vector similarity search for face embeddings and CLIP semantic search. The schema includes 25+ tables managed through Alembic migrations.

### Redis

Redis 7 serves three roles:
1. **Celery Message Broker** -- task queue for background workers
2. **Cache Layer** -- query result caching and session storage
3. **Pub/Sub** -- real-time event propagation for WebSocket alerts

Configured with 512 MB memory limit and AOF persistence.

### MinIO

S3-compatible object storage for:
- Camera snapshots
- Video recordings (segmented MP4)
- Face enrollment images
- Pre-trained model weights
- Generated reports and exports

### MediaMTX

Multi-protocol media server handling:
- **RTSP** (port 8554) -- camera stream ingestion
- **HLS** (port 8888) -- browser-compatible live streaming
- **WebRTC** (port 8889) -- low-latency browser streaming

### Nginx

Reverse proxy providing:
- TLS/SSL termination with configurable certificates
- Rate limiting (auth: 10 req/min, API: 100 req/sec, WebSocket: 5 req/sec)
- Gzip compression
- WebSocket upgrade support
- HLS streaming proxy with CORS headers
- Static asset caching (365 days for Next.js builds)
- Security headers (X-Frame-Options, CSP, HSTS)

---

## GPU Support

For GPU-accelerated inference, use the GPU-specific Compose file:

```bash
docker compose -f docker-compose.yml -f docker-compose.gpu.yml up -d
```

Requirements:
- NVIDIA GPU with CUDA 11.8+
- nvidia-container-toolkit installed on the host
- Set `CV_USE_GPU=true` in `.env`

The GPU configuration enables CUDA execution providers in ONNX Runtime, significantly accelerating inference for all CV models.

---

## Environment Variables

Full reference of environment variables (see `.env.example`):

### Application
| Variable                     | Default              | Description                          |
|-----------------------------|----------------------|--------------------------------------|
| `APP_ENV`                   | `production`         | Environment: production, development |
| `SECRET_KEY`                | --                   | Application secret key               |
| `JWT_SECRET_KEY`            | --                   | JWT signing key                      |
| `JWT_ACCESS_TOKEN_EXPIRE_MINUTES` | `30`          | Access token TTL                     |
| `JWT_REFRESH_TOKEN_EXPIRE_DAYS`   | `7`           | Refresh token TTL                    |
| `LOG_LEVEL`                 | `info`               | Logging level                        |

### Database
| Variable                     | Default              | Description                          |
|-----------------------------|----------------------|--------------------------------------|
| `DATABASE_URL`              | --                   | PostgreSQL async connection string   |
| `POSTGRES_USER`             | `visionai`           | Database username                    |
| `POSTGRES_PASSWORD`         | --                   | Database password                    |
| `POSTGRES_DB`               | `visionai`           | Database name                        |

### Computer Vision
| Variable                           | Default    | Description                     |
|-----------------------------------|------------|---------------------------------|
| `CV_USE_GPU`                      | `false`    | Enable GPU inference            |
| `CV_MODELS_PATH`                  | `/app/models` | Model weights directory      |
| `DETECTION_CONFIDENCE_THRESHOLD`  | `0.5`      | Object detection threshold      |
| `FACE_RECOGNITION_THRESHOLD`      | `0.6`      | Face matching threshold         |

### Alerts and Notifications
| Variable                          | Default    | Description                      |
|----------------------------------|------------|----------------------------------|
| `ALERT_COOLDOWN_SECONDS`         | `60`       | Minimum alert interval           |
| `ALERT_MAX_PER_CAMERA_PER_HOUR`  | `100`      | Alert rate limit per camera      |
| `SMTP_HOST`                      | --         | SMTP server for email alerts     |
| `ANTHROPIC_API_KEY`              | --         | API key for AI Copilot           |

---

## License

This project is proprietary software developed by Sherin Joseph Roy. All rights reserved.

---

## Author

**Sherin Joseph Roy**

GitHub: [Sherin-SEF-AI](https://github.com/Sherin-SEF-AI)
