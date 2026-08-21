# RecliQ SaaS — Technical Architecture & Engineering Guide

> **Project Name:** RecliQ (Web SaaS)  
> **Source Repository:** [https://github.com/jagdevs646/RecliQ](https://github.com/jagdevs646/RecliQ)  
> **Engineering Origin:** Re-architected from Python Desktop Application using **Codex** & **Antigravity IDE**

---

## 1. System Overview & Architectural Paradigm

**RecliQ** is a high-performance, asynchronous data reconciliation platform designed as a web-native SaaS. It bridges the gap between raw, heterogeneous tabular datasets (Excel/CSV) and deterministic discrepancy analysis.

### High-Level Architecture Diagram

```mermaid
flowchart TB
    subgraph ClientLayer["Frontend Layer (React 18 + TypeScript + Vite)"]
        UI["Modern Glassmorphic SPA"]
        Dashboard["Executive Dashboard & Metrics"]
        Upload["File Upload & Column Selector"]
        JobStatus["Asynchronous Job Poller"]
        Results["Visual Exception & Diff Viewer"]
    end

    subgraph APILayer["API & Orchestration Layer (FastAPI / ASGI)"]
        Router["FastAPI REST Router (/api)"]
        SessionMgr["Anonymous Session Manager (UUIDv4)"]
        FileSvc["File Ingestion & Stream Service"]
        JobOrch["Background Task Orchestrator"]
    end

    subgraph CoreEngine["Reconciliation & Analytics Engine (Python 3.12)"]
        UniversalMapper["Universal Column & Orientation Mapper"]
        GenericEngine["Generic Multi-Rule Engine"]
        GSTEngine["GST & Tax Invoice Engine"]
        Matcher["RapidFuzz / Levenshtein & Numeric Matchers"]
        ExcelWriter["XlsxWriter / OpenPyXL Report Engine"]
    end

    subgraph DataStorage["Persistence & Storage Layer"]
        DB[("PostgreSQL / SQLite via SQLAlchemy 2.0")]
        Storage[("Local Ephemeral Storage / Azure Blob Storage")]
    end

    UI -->|HTTP / REST + JSON| Router
    Router --> SessionMgr
    Router --> FileSvc
    Router --> JobOrch
    FileSvc --> Storage
    JobOrch --> DB
    JobOrch --> CoreEngine
    CoreEngine --> UniversalMapper
    CoreEngine --> Matcher
    CoreEngine --> ExcelWriter
    ExcelWriter --> Storage
    JobStatus -->|Poll /api/jobs/{id}| Router
```

---

## 2. Engineering Genesis: Desktop-to-SaaS Re-engineering

### The Legacy Baseline ("RecliQ Desktop")
The initial software version was a standalone desktop application developed using Python with a wxPython/Tkinter GUI framework. While the math and matching heuristics were effective, the desktop architecture had architectural limits:
- **Tight Coupling:** UI event loops were entangled with heavy data processing routines.
- **Single-Thread Bottlenecks:** Large datasets blocked the UI rendering thread.
- **Client-Side Footprint:** Required local execution environments and lacked centralized telemetry or automated update pipelines.

### The Conversion using Codex & Antigravity IDE
The transition from a monolithic desktop codebase to a modern, decoupled cloud SaaS was accelerated using **AI-assisted engineering with Codex** and the **Antigravity IDE**:
1. **Domain-Driven Decoupling:** The proprietary reconciliation algorithms in `matchers.py` and `engine.py` were extracted into a stateless, framework-agnostic Python library.
2. **RESTful Contract Design:** Defined strict Pydantic schemas for column mapping, rule configurations, job states, and result payloads.
3. **Async Web Architecture:** Wrapped processing logic in FastAPI's asynchronous task pipelines to ensure non-blocking I/O during heavy dataframe operations.
4. **Modern Frontend Scaffolding:** Generated a type-safe TypeScript React frontend using Vite and custom CSS token systems.

---

## 3. Technology Stack & Language Ecosystem

| Layer | Technology | Language | Purpose / Rationale |
| :--- | :--- | :--- | :--- |
| **Frontend Framework** | React 18 | TypeScript | Component-driven UI with strong compile-time type safety. |
| **Build & Tooling** | Vite 5 | TypeScript / JS | Instant Hot Module Replacement (HMR) and optimized rollup bundle builds. |
| **Icons & Styling** | Lucide React + Vanilla CSS | CSS3 / TSX | Ultra-responsive, lightweight custom glassmorphic styling without bloated frameworks. |
| **Backend Framework** | FastAPI 0.111 | Python 3.12 | Ultra-fast ASGI async framework with automatic OpenAPI documentation. |
| **Data Processing** | Pandas 2.2 | Python | Vectorized tabular operations, matrix slicing, and dataframe transformations. |
| **Fuzzy Matching** | RapidFuzz 3.9 | C++ / Python | C++ accelerated Levenshtein, Token Sort, and Partial Ratio calculations. |
| **Database & ORM** | SQLAlchemy 2.0 + Alembic | Python | Declarative ORM with automated schema migrations; supports SQLite & PostgreSQL. |
| **Spreadsheet Engine** | XlsxWriter + OpenPyXL | Python | High-performance multi-tab Excel generation with custom formatting and styles. |
| **Cloud Storage** | Azure Storage Blob SDK | Python | Scalable object storage for enterprise deployments (pluggable with local storage). |
| **Containers & Deploy** | Docker, Render, Azure | YAML / Bash | Multi-stage containerization with one-click deployment pipelines. |

---

## 4. Deep Dive: Reconciliation Engine & Matching Heuristics

### 4.1 Data Ingestion & Universal Column Mapper
RecliQ handles diverse and chaotic real-world spreadsheets:
- **Orientation Normalization:** Detects whether data is arranged in standard vertical rows or horizontal time-series columns, transposing internal matrices seamlessly.
- **Dynamic Header Detection:** Parses multi-line headers, strips blank rows, and handles encoding irregularities (UTF-8, Latin-1, CP1252).
- **Universal Data Model (`universal_mapper.py`):** Converts mismatched data into standardized exception schemas with severity classification (`Critical`, `High`, `Medium`), variance calculations, and percentage differentials.

```python
# Conceptual Architecture: Universal Data Normalization
def build_universal_data_model(
    job_type: str,
    reconciliation_results: list[dict],
    file_1_not_found: list[dict],
    file_2_not_found: list[dict],
    ...
) -> dict:
    # Extracts field-level discrepancies, classifies exception severity,
    # and computes absolute/relative numerical variances.
```

### 4.2 Intelligent Matching Strategies (`matchers.py`)

```mermaid
flowchart TD
    InputVal["Source vs Target Value Pair"] --> TypeCheck{"Determine Value Type"}
    
    TypeCheck -->|Text / Strings| TextMatch["RapidFuzz Token Sort & Normalized Levenshtein"]
    TypeCheck -->|Numbers / Currency| NumMatch["Absolute Tolerance & Percentage Delta Matcher"]
    TypeCheck -->|Dates / Timestamps| DateMatch["Multi-Format Parser (ISO, UK, US, Epoch)"]
    TypeCheck -->|Identifiers| IDMatch["Sanitizer (GSTIN, PAN, Invoice Number Strip)"]

    TextMatch --> ScoreEval{"Score >= Threshold?"}
    NumMatch --> ScoreEval
    DateMatch --> ScoreEval
    IDMatch --> ScoreEval

    ScoreEval -->|Yes (100%)| Exact["Exact Match"]
    ScoreEval -->|80% - 99%| Partial["Partial / Fuzzy Match (Flagged)"]
    ScoreEval -->|No| Mismatch["Discrepancy / Exception"]
```

1. **Exact & Normalized Text Matching:** Strips non-alphanumeric noise, normalizes unicode characters, and applies case folding.
2. **RapidFuzz Levenshtein & Token Matching:**
   $$\text{Ratio}(s_1, s_2) = \frac{2 \cdot M}{|s_1| + |s_2|}$$
   Calculates similarity indices to handle vendor typos (e.g., `"Microsoft Corporation"` vs `"Microsoft Corp"`).
3. **Numeric & Currency Tolerances:** Supports both absolute tolerances (e.g., $\pm \$0.50$ for rounding differences) and relative percentage deviations.
4. **GST Tax Invoice Subsystem:**
   - Handles **1-to-1**, **1-to-many**, and **many-to-many** invoice line items.
   - Reconciles taxable values, IGST, CGST, and SGST breakdowns.
   - Computes eligible vs. ineligible Input Tax Credit (ITC) discrepancies.

---

## 5. REST API Architecture & Data Flow

### Key API Routes

```text
POST /api/files/upload                # Upload source files (multipart/form-data)
GET  /api/files/{id}/columns          # Inspect column headers & data orientation
POST /api/reconciliation/generic     # Trigger generic multi-column reconciliation job
POST /api/reconciliation/gst         # Trigger GST invoice specific reconciliation
GET  /api/jobs/{id}                   # Poll real-time job status & execution metrics
GET  /api/reports/job/{id}/download   # Stream generated Excel workbook to client
GET  /health                          # Service health check & uptime probe
```

### Asynchronous Execution Pipeline

1. **File Upload:** The client uploads two tabular files (`File 1` and `File 2`). The backend writes them to session-scoped storage and returns file metadata.
2. **Column Inspection:** The frontend queries `/api/files/{id}/columns` to populate mapping interfaces dynamically.
3. **Job Initiation:** The user configures primary matching keys and comparison rules. Upon submission, FastAPI spawns a background thread/task worker and returns a `job_id`.
4. **Processing & Status Polling:** The frontend polls `/api/jobs/{id}` displaying progress status (`pending` $\rightarrow$ `processing` $\rightarrow$ `completed` / `failed`).
5. **Report Compilation:** Upon engine completion, multi-sheet Excel reports with custom styling, summary KPI tables, and side-by-side discrepancy views are persisted in storage.
6. **Delivery:** The user downloads the generated workbook via a direct streaming endpoint.

---

## 6. Deployment & Environment Setup

### Prerequisites
- Python 3.12+
- Node.js 18+ / pnpm
- Docker & Docker Compose (Optional for containerized run)

### Running with Docker (Recommended)
```bash
# Clone the repository
git clone https://github.com/jagdevs646/RecliQ.git
cd RecliQ

# Launch multi-container stack
docker compose up --build
```
- **Frontend Application:** `http://localhost:5173`
- **Backend API Docs:** `http://localhost:8000/docs`

### Cloud Deployment Profiles
- **Render (`render.yaml`):** Automated deployment configuration with managed PostgreSQL database, gunicorn/uvicorn workers, and persistent disk support.
- **Azure Container Apps (`./scripts/deploy-azure.ps1`):** Enterprise Azure deployment script with Azure Container Registry (ACR) and Azure Blob Storage integrations.

---

## 7. Project Summary & Source Code Access

The complete source code, test suites, API specifications, and deployment manifests are open for review and contributions:

- **GitHub Repository:** [https://github.com/jagdevs646/RecliQ](https://github.com/jagdevs646/RecliQ)
- **Primary Maintainer:** Jagdev / @jagdevs646
