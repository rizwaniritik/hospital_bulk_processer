# 🏥 Hospital Bulk Processor

A production-grade **bulk hospital processing API** built with **FastAPI** that integrates with the [Hospital Directory API](https://hospital-directory.onrender.com/docs) to handle CSV uploads and process hospital records at scale.

---

## 📋 Table of Contents

- [Overview](#overview)
- [Architecture](#architecture)
- [Project Structure](#project-structure)
- [Design Patterns Used](#design-patterns-used)
- [API Endpoints](#api-endpoints)
- [Getting Started](#getting-started)
- [Running with Docker](#running-with-docker)
- [Running Tests](#running-tests)
- [Configuration](#configuration)
- [Deployment on Render](#deployment-on-render)

---

## Overview

This service accepts a CSV file (up to 20 hospitals), creates each hospital concurrently via the external API, then activates the entire batch in one call. It tracks progress in-memory so clients can poll for real-time updates.

**Tech Stack:**
| Layer | Technology |
|---|---|
| Language | Python 3.11+ |
| Framework | FastAPI |
| HTTP Client | httpx (async) |
| Validation | Pydantic v2 |
| Server | Uvicorn |
| Testing | pytest + pytest-asyncio |
| Containerization | Docker + docker-compose |

---

## Architecture

```
CSV Upload → Validation → Batch ID Generated → Concurrent Hospital Creation
               ↓                                        ↓
          CSVValidationError                    BatchRepository (progress)
                                                        ↓
                                              Batch Activation on External API
                                                        ↓
                                               BulkCreateResponse returned
```

The app is organized in **clean layers** that each have a single job:

```
[ HTTP Routes ] → [ Service Layer ] → [ API Client ] → External Hospital API
                        ↓
                  [ Repository ]   ← in-memory batch state
                        ↑
                  [ CSV Parser ]   ← validates uploaded file
```

---

## Project Structure

```
hospital-bulk-processor/
│
├── app/
│   ├── main.py                  # FastAPI app setup, middleware, lifespan
│   │
│   ├── api/
│   │   ├── dependencies.py      # Dependency injection providers
│   │   └── routes/
│   │       └── hospitals.py     # All HTTP route handlers
│   │
│   ├── core/
│   │   ├── config.py            # Singleton settings (env vars)
│   │   └── exceptions.py        # Custom domain exceptions
│   │
│   ├── models/
│   │   └── hospital.py          # Pydantic request/response models + enums
│   │
│   ├── repositories/
│   │   └── batch_repository.py  # In-memory batch state store (Repository pattern)
│   │
│   ├── services/
│   │   ├── bulk_processor.py    # Core business logic (orchestrator)
│   │   └── hospital_api_client.py # External API adapter (Gateway pattern)
│   │
│   └── utils/
│       └── csv_parser.py        # CSV parsing & validation (SRP)
│
├── tests/
│   ├── test_csv_parser.py       # Unit tests for CSV parsing (16 tests)
│   ├── test_bulk_processor.py   # Unit tests for business logic (15 tests)
│   └── test_api.py              # Integration tests for HTTP routes (14 tests)
│
├── sample_data/
│   ├── hospitals_sample.csv     # 15 hospitals with phone numbers
│   └── hospitals_no_phone.csv   # 3 hospitals without phone (phone is optional)
│
├── Dockerfile                   # Multi-stage Docker build
├── docker-compose.yml           # One-command local run
├── requirements.txt             # Python dependencies
├── pytest.ini                   # Test configuration
├── run.py                       # Dev server entry point
└── .env.example                 # Environment variable template
```

---

## Design Patterns Used

### 1. Singleton (`config.py`, `batch_repository.py`, `hospital_api_client.py`)
> **What:** Only one instance is ever created.
> **Why:** Settings and the shared HTTP connection pool should be created once and reused. Creating a new HTTP client per request wastes TCP handshakes.

```python
@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()  # Only called once; subsequent calls return cached instance
```

### 2. Repository Pattern (`batch_repository.py`)
> **What:** Isolates data access behind a clean interface.
> **Why:** Routes and services never know *how* data is stored. Swap in-memory for Redis/PostgreSQL by changing only this file.

### 3. Adapter / Gateway Pattern (`hospital_api_client.py`)
> **What:** Wraps the external API in a typed Python interface.
> **Why:** Business logic calls `create_hospital(name, address)` — it never touches raw HTTP. If the external API changes its URL or shape, only this adapter changes.

### 4. Dependency Injection (`dependencies.py` + FastAPI `Depends()`)
> **What:** Services receive their dependencies from the outside rather than creating them.
> **Why:** Enables clean unit testing — swap real clients for mocks without touching production code.

### 5. Concurrency via `asyncio.Semaphore` (`bulk_processor.py`)
> **What:** All hospital creation requests run in parallel, bounded by a configurable limit.
> **Why:** Sequential processing of 20 hospitals would be ~20x slower. The semaphore prevents flooding the external API.

---

## API Endpoints

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/api/v1/hospitals/bulk` | Upload CSV → create & activate all hospitals |
| `POST` | `/api/v1/hospitals/bulk/validate` | Dry-run validate CSV without processing |
| `GET` | `/api/v1/hospitals/bulk/{batch_id}/progress` | Poll real-time batch progress |
| `GET` | `/api/v1/hospitals/bulk` | List all submitted batches |
| `GET` | `/health` | Liveness probe |
| `GET` | `/docs` | Interactive Swagger UI |

### Bulk Create Response

```json
{
  "batch_id": "550e8400-e29b-41d4-a716-446655440000",
  "total_hospitals": 15,
  "processed_hospitals": 15,
  "failed_hospitals": 0,
  "processing_time_seconds": 12.4,
  "batch_activated": true,
  "hospitals": [
    {
      "row": 1,
      "hospital_id": 101,
      "name": "General Hospital",
      "status": "created_and_activated",
      "error": null
    }
  ]
}
```

### CSV Format

```csv
name,address,phone
General Hospital,123 Main St New York NY 10001,212-555-0101
City Clinic,456 Oak Ave Los Angeles CA 90001,
```

> **Rules:**
> - `name` and `address` are **required**
> - `phone` is **optional** (can be blank or omitted entirely)
> - Maximum **20 rows** per upload
> - File must be **UTF-8** encoded

---

## Getting Started

### Prerequisites
- Python 3.11+
- pip

### 1. Clone and set up

```bash
git clone <your-repo-url>
cd hospital-bulk-processor

# Create virtual environment (recommended)
python -m venv venv
venv\Scripts\activate   # Windows
# source venv/bin/activate  # Mac/Linux

pip install -r requirements.txt
```

### 2. Configure environment (optional)

```bash
cp .env.example .env
# Edit .env as needed
```

### 3. Run the server

```bash
python run.py
```

The API is now live at **http://localhost:8000**
- Swagger UI: http://localhost:8000/docs
- ReDoc: http://localhost:8000/redoc

### 4. Quick test with sample data

#### Option A: Web Dashboard (Interactive UI)
Visit **http://localhost:8000/dashboard** in your browser.
- Select `sample_data/hospitals_sample.csv`
- Click **Validate CSV First** or **Bulk Create & Activate** to see live results and stats!

#### Option B: Interactive OpenAPI Docs (Swagger)
Visit **http://localhost:8000/docs** in your browser, select `POST /api/v1/hospitals/bulk`, click **Try it out**, upload `sample_data/hospitals_sample.csv`, and click **Execute**.

#### Option C: Postman Collection
Import `Hospital_Bulk_Processor.postman_collection.json` into Postman.
- Pre-configured requests for CSV validation, bulk creation, and progress polling.
- Automatically saves `batch_id` into collection variables upon creation!

#### Option D: curl
```bash
# Validate first (no side effects)
curl -X POST http://localhost:8000/api/v1/hospitals/bulk/validate \
  -F "file=@sample_data/hospitals_sample.csv"

# Bulk create and activate
curl -X POST http://localhost:8000/api/v1/hospitals/bulk \
  -F "file=@sample_data/hospitals_sample.csv"
```

---

## Running with Docker

```bash
# Build and start
docker-compose up --build

# Run in background
docker-compose up -d --build

# Stop
docker-compose down
```

---

## Running Tests

```bash
# All 41 tests
python -m pytest tests/ -v

# With coverage report
python -m pytest tests/ -v --cov=app --cov-report=term-missing

# Specific test file
python -m pytest tests/test_csv_parser.py -v
python -m pytest tests/test_bulk_processor.py -v
python -m pytest tests/test_api.py -v
```

**Test breakdown:**
| File | Tests | What's covered |
|------|-------|----------------|
| `test_csv_parser.py` | 16 | CSV validation, edge cases, encoding |
| `test_bulk_processor.py` | 15 | Business logic, partial failures, activation errors |
| `test_api.py` | 14 | HTTP status codes, request/response shapes |
| **Total** | **45** | **All critical paths** |

---

## Configuration

All settings are environment variables with defaults:

| Variable | Default | Description |
|----------|---------|-------------|
| `HOSPITAL_API_BASE_URL` | `https://hospital-directory.onrender.com` | External API base URL |
| `API_TIMEOUT_SECONDS` | `30` | HTTP call timeout |
| `MAX_CSV_ROWS` | `20` | Maximum hospitals per CSV |
| `MAX_CONCURRENT_REQUESTS` | `5` | Parallel API calls limit |
| `PORT` | `8000` | Server port |
| `DEBUG` | `false` | Enable hot-reload |

---

## Deployment on Render

1. Push this repository to GitHub
2. Create a new **Web Service** on [render.com](https://render.com)
3. Set **Build Command**: `pip install -r requirements.txt`
4. Set **Start Command**: `uvicorn app.main:app --host 0.0.0.0 --port $PORT`
5. Add environment variables from `.env.example` in the Render dashboard
6. Deploy 🚀

