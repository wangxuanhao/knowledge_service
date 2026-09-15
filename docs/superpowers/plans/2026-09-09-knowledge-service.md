# Knowledge Service Implementation Plan

> **For agentic workers:** use subagent-driven-development for bounded implementation/review tasks. User has approved architecture; proceed without further routine confirmation.

**Goal:** Deliver runnable project/ontology/bitemporal knowledge/metadata-first retrieval service.

**Architecture:** Python package, SQLite authoritative storage, candidate-only FAISS ranking, versioned RDF ontology, explicit Semantica adapters, FastAPI and local console.

**Tech Stack:** Python 3.12+, SQLite JSON1, FastAPI/Pydantic, FAISS/numpy, rdflib/pyshacl, Semantica.

## Tasks

- [x] 1. Core storage and filter contract: `knowledge_service/repository.py`, `filters.py`, `time.py`; tests `tests/service/test_repository.py`. Validate time boundaries/revisions/project scoping/metadata typing/transactions and restart.
- [x] 2. Ontology and model adapters: `ontology.py`, `embeddings.py`, `semantica_adapter.py`, `retrieval.py`; tests exercise real RDF/FAISS and installed Semantica time behavior. No hidden fallback to numbered scripts.
- [x] 3. API and ingestion: `api.py`, `models.py`, `service.py`; tests ingest, revise, query, filter, inspect history and validate ontology through TestClient. Environment settings contain no secrets.
- [x] 4. Workbench and packaging: `web/`, `pyproject.toml`, `.env.example`, `scripts/start-service.ps1`, README; independent service entry point `python -m knowledge_service`.
- [x] 5. Review against spec, resolve issues, run service tests and legacy checks, launch locally and verify health + real demo workflow. Report exact commands and limitations.

Verification uses `.venv-service/Scripts/python.exe -m pytest tests/service -q` and `python -m knowledge_service --help`. New behavioral tests are written before implementation where practical. Each task is independently reviewable; existing data never deleted or silently migrated.

## Verification record — 2026-09-09

- Python 3.13 service environment, editable package built and installed successfully.
- `pytest tests/service tests/test_web_ui_contract.py -q`: 39 passed; one upstream Starlette/AnyIO deprecation warning.
- Real local BGE-M3: 2 inputs × 1024 dimensions, no external model call.
- `scripts/smoke_service.py`: passed through running HTTP service using local semantic embeddings, nested filter + half-open boundary + Semantica graph + SHACL + SPARQL + evidence answer.
- Verification project: `930d02dc-5a26-4cea-a6ec-ff2a939df28d`; explicitly labelled service verification, kept available in workbench.
- Independent spec and code reviews completed. Fixed temporal SHACL union bug, provider malformed response handling, and concurrent ingestion receipt overwrite; red-to-green regression tests retained.
- Real paid LLM extraction/generation was not invoked. Minimal Semantica module dependencies installed; full distribution dependency closure is intentionally not claimed.
