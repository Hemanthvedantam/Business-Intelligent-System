<div align="center">

<img src="https://img.shields.io/badge/ABIP-Autonomous%20Business%20Intelligence-4F46E5?style=for-the-badge&logoColor=white" alt="ABIP"/>

<h3>Autonomous Business Intelligence Platform</h3>
<p>Upload your data. Ask a question. Get executive-grade intelligence — automatically.</p>

[![Python](https://img.shields.io/badge/Python-3.12-3776AB?style=flat&logo=python&logoColor=white)](https://python.org)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.137-009688?style=flat&logo=fastapi&logoColor=white)](https://fastapi.tiangolo.com)
[![LangGraph](https://img.shields.io/badge/LangGraph-Multi--Agent-FF6B35?style=flat&logo=chainlink&logoColor=white)](https://langchain-ai.github.io/langgraph)
[![Groq](https://img.shields.io/badge/Groq-LLaMA%203.3%2070B-F55036?style=flat&logo=groq&logoColor=white)](https://groq.com)
[![DuckDB](https://img.shields.io/badge/DuckDB-Analytics-FFC832?style=flat&logo=duckdb&logoColor=black)](https://duckdb.org)
[![License](https://img.shields.io/badge/License-MIT-22C55E?style=flat)](LICENSE)
[![Deploy](https://img.shields.io/badge/Deployed%20on-Render-46E3B7?style=flat&logo=render&logoColor=white)](https://business-intelligent-system-12.onrender.com)

**[Live Demo](https://business-intelligent-system-12.onrender.com)** · **[Report Bug](https://github.com/Hemanthvedantam/Business-Intelligent-System/issues)** · **[Request Feature](https://github.com/Hemanthvedantam/Business-Intelligent-System/issues)**

</div>

---

## Overview

ABIP is a production-grade business intelligence platform built on a **multi-agent AI architecture**. It eliminates the gap between raw business data and actionable insight — no SQL knowledge, no manual analysis, no dashboards to configure.

Drop in a CSV or Excel file. A coordinated pipeline of specialized AI agents automatically performs statistical analysis, detects anomalies, identifies root causes, generates forecasts, and produces an executive report — all within seconds.

Built for analysts, product managers, and business leaders who need answers fast.

---

## Architecture

```
                        ┌─────────────────────────────────────┐
                        │           FastAPI Backend            │
                        │                                      │
  User ──► Upload ────► │  Files ──► DuckDB ──► Insights API  │
            Query ────► │  Auth  ──► JWT     ──► Pages        │
                        └──────────────┬──────────────────────┘
                                       │
                        ┌──────────────▼──────────────────────┐
                        │        LangGraph Agent Pipeline      │
                        │                                      │
                        │  Planner ──► Data Analyst            │
                        │      └────► Root Cause Agent         │
                        │      └────► Forecast Agent           │
                        │      └────► RAG Agent (Qdrant)       │
                        │      └────► Recommend Agent          │
                        │      └────► Executive Synthesizer    │
                        └─────────────────────────────────────┘
```

Each agent is a **specialized LLM worker** with a distinct role. The Planner decomposes the investigation goal; downstream agents execute in parallel where possible; the Executive agent synthesizes a final boardroom-ready report.

---

## Key Features

### Autonomous Intelligence
AI context surfaces automatically from existing data — no prompts required from the user. Upload a dataset and the platform immediately begins generating insights, narratives, and alerts in the background.

### Multi-Agent Investigation Pipeline
Seven coordinated agents handle the full analytical lifecycle: planning → data analysis → root cause detection → forecasting → RAG-based retrieval → recommendations → executive synthesis. Orchestrated via **LangGraph** with real-time streaming logs.

### Natural Language Investigations
Ask business questions in plain English. The pipeline translates intent into structured analysis across your datasets and returns evidence-backed answers with citations.

### Executive Dashboard
A live command center featuring a weighted **Health Score ring**, urgency-tiered priority banners, and an Intelligence Snapshot — all computed from real API data, not static placeholders.

### DuckDB-Powered Analytics
In-process analytical SQL engine. Supports complex aggregations, window functions, and joins across multi-file datasets at millisecond latency — no separate database server required.

### Streaming Insights
Server-Sent Events (SSE) deliver insights progressively to the UI. TTL-cached results, per-section error boundaries, skeleton loaders, and session-persistent tab state ensure a production-quality UX.

### Multi-Provider LLM Support
Provider abstraction layer supports **Groq**, **Gemini**, and **OpenRouter** — swap models without changing application code.

---

## Tech Stack

| Category | Technology |
|---|---|
| **API Framework** | FastAPI 0.137, Uvicorn, Starlette |
| **Frontend** | Jinja2, Vanilla JS, Chart.js, Tabler Icons |
| **Agent Orchestration** | LangGraph, LangChain Core |
| **LLM Providers** | Groq (LLaMA 3.3 70B), Gemini, OpenRouter |
| **Analytics DB** | DuckDB 1.5 |
| **Vector Store** | Qdrant |
| **App Database** | SQLAlchemy + SQLite / Alembic migrations |
| **Auth** | JWT (python-jose), bcrypt |
| **ML / Stats** | scikit-learn, pandas, numpy, scipy |
| **Report Generation** | ReportLab (PDF), python-docx (DOCX) |
| **Deployment** | Render |

---

## Getting Started

### Prerequisites

- Python 3.12+
- Git
- A free [Groq API key](https://console.groq.com)

### Installation

```bash
# 1. Clone the repository
git clone https://github.com/Hemanthvedantam/Business-Intelligent-System.git
cd Business-Intelligent-System

# 2. Create and activate virtual environment
python -m venv .venv

# Linux / macOS
source .venv/bin/activate

# Windows
.venv\Scripts\activate

# 3. Install dependencies
pip install -r requirements.txt

# 4. Configure environment
cp .env.example .env
# Open .env and fill in your values (see below)

# 5. Start the server
uvicorn main:app --reload
```

Visit `http://localhost:8000`

### Environment Variables

```env
# LLM
GROQ_API_KEY=your_groq_api_key

# Auth
JWT_SECRET_KEY=your_random_secret_key_min_32_chars
JWT_ALGORITHM=HS256
JWT_EXPIRE_MINUTES=1440

# Database
DATABASE_URL=sqlite:///./data/abip.db

# Vector Store
QDRANT_HOST=localhost
QDRANT_PORT=6333
```

---

## Project Structure

```
Business-Intelligent-System/
│
├── app/
│   ├── agents/
│   │   ├── planner.py          # Goal decomposition
│   │   ├── data_analyst.py     # Statistical analysis
│   │   ├── root_cause.py       # Anomaly root cause detection
│   │   ├── forecast_agent.py   # Time-series forecasting
│   │   ├── rag_agent.py        # Retrieval-augmented Q&A
│   │   ├── recommend.py        # Business recommendations
│   │   ├── executive.py        # Final report synthesis
│   │   └── graph.py            # LangGraph pipeline definition
│   │
│   ├── core/
│   │   ├── config.py           # Settings via pydantic-settings
│   │   ├── security.py         # JWT + bcrypt auth
│   │   └── logging.py          # Structured logging (structlog)
│   │
│   ├── providers/
│   │   ├── base.py             # LLM provider interface
│   │   ├── groq_provider.py    # Groq implementation
│   │   ├── gemini.py           # Gemini implementation
│   │   └── openrouter_provider.py
│   │
│   ├── routers/
│   │   ├── auth.py             # /register, /login
│   │   ├── files.py            # /upload, /datasets
│   │   ├── insights_api.py     # /insights (SSE streaming)
│   │   ├── investigations.py   # /investigate (agent pipeline)
│   │   ├── reports.py          # /reports (PDF/DOCX export)
│   │   └── pages.py            # HTML template routes
│   │
│   ├── services/
│   │   ├── duckdb_service.py   # SQL analytics engine
│   │   ├── pipeline_service.py # Agent pipeline orchestration
│   │   ├── report_service.py   # Document generation
│   │   ├── data_quality.py     # Dataset profiling
│   │   ├── domain_discovery.py # Auto domain classification
│   │   └── memory_service.py   # Conversation memory
│   │
│   ├── models/                 # SQLAlchemy ORM models
│   ├── static/                 # CSS + JS assets
│   └── templates/              # Jinja2 HTML templates
│
├── data/
│   ├── uploads/                # User datasets
│   ├── reports/                # Generated reports
│   └── qdrant/                 # Vector store persistence
│
├── main.py                     # Application entrypoint
└── requirements.txt
```

---

## Application Pages

| Route | Page | Description |
|---|---|---|
| `/dashboard` | Executive Dashboard | Health score, priority alerts, intelligence snapshot |
| `/datasets` | Data Management | Upload CSV/XLSX, drag-and-drop, quality profiling |
| `/explorer` | Data Explorer | DuckDB SQL console with inline visualizations |
| `/insights` | Auto Insights | Clustering, correlations, anomalies, trend detection |
| `/nlp` | NLP Analysis | AI narrative across business dimensions |
| `/chat` | Investigations | Natural language multi-agent investigation interface |
| `/reports` | Reports | View, download, and export PDF/DOCX reports |
| `/settings` | Settings | Platform and LLM provider configuration |

---

## Deployment

### Deploy on Render (Recommended)

1. Fork this repository
2. Go to [render.com](https://render.com) → **New Web Service**
3. Connect your forked repo
4. Configure:
   - **Build Command:** `pip install -r requirements.txt`
   - **Start Command:** `uvicorn main:app --host 0.0.0.0 --port $PORT`
   - **Python Version:** `3.12`
5. Add all environment variables from the table above
6. Deploy

### Deploy on AWS EC2

```bash
# On your EC2 instance (t3.small or larger recommended)
git clone https://github.com/Hemanthvedantam/Business-Intelligent-System.git
cd Business-Intelligent-System
pip install -r requirements.txt

# Run with gunicorn for production
pip install gunicorn
gunicorn main:app -w 2 -k uvicorn.workers.UvicornWorker --bind 0.0.0.0:8000
```

---

## Roadmap

- [ ] Multi-user workspace with role-based access control
- [ ] Scheduled automated report delivery (email / Slack)
- [ ] Real-time database connectors (PostgreSQL, BigQuery, Snowflake)
- [ ] Fine-tuned domain-specific models per industry vertical
- [ ] Collaborative investigation sessions
- [ ] REST API with full OpenAPI documentation for external integrations

---

## Author

**Hemanth Vedantam**

AI/ML Engineer · B.Tech Computer Science (AI Specialization) · KL University, 2026

[![LinkedIn](https://img.shields.io/badge/LinkedIn-Connect-0A66C2?style=flat&logo=linkedin&logoColor=white)](https://linkedin.com/in/hemanthvedantam)
[![GitHub](https://img.shields.io/badge/GitHub-Follow-181717?style=flat&logo=github&logoColor=white)](https://github.com/Hemanthvedantam)

---

## License

Distributed under the MIT License. See [`LICENSE`](LICENSE) for details.

---

<div align="center">
<sub>Built with FastAPI · LangGraph · Groq · DuckDB</sub>
</div>

