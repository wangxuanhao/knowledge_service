# File Preview and Parsing Workflow Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [x]`) syntax for tracking.

**Goal:** Make file-row original previews, no-manifest MD/HTML resource ZIPs, and explicitly started per-file durable parsing and per-file applicability a coherent upload workflow.

**Architecture:** Keep binary uploads and temporary preparation separate from authoritative knowledge writes. Reuse existing parsing adapters, batch leases/heartbeats and source rendering; publish prepared outputs only after user confirmation. ZIP is one MD/HTML document plus referenced resources.

**Tech Stack:** FastAPI, PostgreSQL, native JS/CSS, Docling/RapidOCR, local ASR/ffmpeg, existing source reader and Node/Playwright tests.

## Task 1: Input cases and contracts

- [x] Inspect original Word/PDF files without changing them.
- [x] Add synthetic TXT/MD/HTML/image/video and no-manifest ZIP cases in `tests/docs/cases/`.
- [x] Run `D:/anaconda/envs/llm_model/python.exe tests/docs/verify_inputs.py` and record the explicit coverage limits.
- [x] Add service regressions for ZIP main-file discovery and referenced-only resources; reject new PDF/DOCX/video mains and `document.pages` packages with an explicit upload profile while preserving historical source reading.

## Task 2: Original preview and per-file entry

Files: `web/ingest-mode.js`, `web/library-workspace.js`, a focused original-preview controller, `web/workbench.js`, existing PDF/media/source renderers, `tests/service/library-upload-steps.test.cjs`, `test_chunk_preview_ui.py`.

- [x] Cover file-row identity, same-name content, native preview, mixed-platform configuration and mobile geometry in browser/service regressions; cover removal/cancellation in preparation operations.
- [x] Put original-preview/play actions in each file row; selecting files starts individual temporary uploads and lightweight validation only; neither upload nor the first Next action starts deep parsing.
- [x] Add per-file platform/business/audience/time/title settings with optional batch defaults; bulk apply changes only enabled fields and supports fill-unset/explicit-overwrite. Show mixed values and final per-file configuration. Metadata edits must not restart parsing.
- [x] Serve original PDF pages on demand through owned temporary uploads; native preview must not call Docling/OCR.
- [x] Keep parsed text/slice inspection as separate actions backed by prepared results.
- [x] Define honest DOCX/legacy DOC capability behavior according to available local conversion tools.
- [x] Bump each changed asset version and update only relevant contract assertions.

## Task 3: Durable preparation jobs

Files: a preparation service/repository seam and numbered migration, preparation HTTP routes, `document_preview.py`, `document_uploads.py`, `ingest_batches.py`; reuse existing batch runner lease patterns.

- [x] First add failing tests: upload/preview creates no knowledge, ownership/TTL checks, repeat submission idempotency, cancellation and restart recovery.
- [x] Store temporary upload ownership, content hash, parser settings/version, status, stage, times, optional counters, result reference and error code.
- [x] Split lightweight file/ZIP inspection from deep parsing; accepted uploads stay staged; an explicit parse operation validates selected item/config versions then queues work and returns task IDs promptly. Upload never queues deep parsing.
- [x] Test mixed-platform files, upload failure, duplicate upload callbacks and parser-option changes while a job is running; reject stale parse results by parser fingerprint, independently version metadata and chunks; metadata edits during parsing must retain usable results. Keep semantic metadata edits independent from parser fingerprints.
- [x] Reuse completed parse artifacts by owned content/config/model fingerprint; attach identical active requests to existing work. Keep upload/parse/publish idempotency distinct from content and publication semantics.
- [x] Add staged preparation create/upload/configure/parse/status/results/cancel/retry/publish API contracts and OpenAPI examples. Preserve existing ingest-batches start semantics for existing clients.
- [x] Run PDF/OCR/ASR in background workers with bounded concurrency and real heartbeats.
- [x] Reuse prepared results for chunk changes and formal ingestion; invalidate by the precise parser/config fingerprint.
- [x] Add SSE or polling using a common public task state; avoid leaking storage keys, credentials and user drafts across projects.
- [x] Test shared-task cancellation detaches one item without interrupting others; cancel underlying work only when unreferenced.
- [x] Enforce publication identity per item/snapshot across changed idempotency keys; require explicit independent-copy/revision intent and target version for revisions.

## Task 4: Progress and result review

Files: `web/ingest-batches.js`, new preparation view, wizard adapter/styles; backend progress adapters where real callbacks are available.

- [x] First add failing tests for elapsed/stage display, indeterminate progress, close/reopen, timeout, partial failure and retry.
- [x] Implement upload/verify → per-file or selected-files configuration → explicit start parsing and review → confirm publication.
- [x] Show upload bytes separately from parsing/OCR/transcription/knowledge/index progress; report aggregate completed-file counts, never a fabricated overall compute percentage.
- [x] Make upload/parse/publish boundaries identical for UI and API; test separate options for different platforms/businesses in the same session.
- [x] Show page/time-unit counts only when actually reported; otherwise show stage, elapsed and last activity.
- [x] Review original, parsed text and slices per file before publishing; process successful files independently.
- [x] Distinguish stored source, governance/knowledge completion and search-index readiness.

## Task 5: Verification and release

- [x] One isolated pytest session for preparation, upload/package APIs and actual fixture-backed browser cases; never parallelize PG test sessions.
- [x] Run `node --test tests/service/*.test.cjs` and relevant desktop/mobile interactions.
- [x] Sample first-page OCR separately from full-file and throughput acceptance; report each scope honestly.
- [x] Review changes before master integration, update current manuals, and publish only synthetic test fixtures unless real source publication is explicitly authorized.

Status: implementation and scoped verification completed. Deployment requires migration 0041 and service restart. Full-file OCR/ASR accuracy and long-file capacity remain separate acceptance work; no artificial page progress is reported. The current API guide is docs/file-preparation-api.md; existing unrelated manual edits are outside this change.
