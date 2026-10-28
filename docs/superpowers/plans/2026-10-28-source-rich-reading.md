# Source Storage and Rich Reading Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Retain originals in MinIO, persist extracted images, and make the library show fixed-version text and images by default.

**Architecture:** Reuse the content-addressed object protocol and existing source-version/block/asset schema. Add a MinIO adapter, replace new batch BYTEA payloads with object references, retain image output from document parsing, and share the authorized reading renderer between the library and evidence reader. Preserve existing version, authorization, extraction-quality and retry semantics.

**Tech Stack:** Python 3.12 (D:/anaconda/envs/llm_model/python.exe), FastAPI, PostgreSQL migrations, boto3/S3/MinIO, Docling, markdown-it/bleach, native JS/CSS, pytest and node:test.

**Spec:** docs/superpowers/specs/2026-10-28-source-rich-reading-design.md

**Workspace:** Existing checkout has unrelated dirty changes; keep them intact. Branch codex/source-rich-reading retains that working state. Never stage/commit unrelated modifications. One pytest process at a time, no sourcing .env into tests. Graph index misses source modules; use current disk code for those after graph lookup.

---

## Task 1: Object storage, batch references and safe migration

**Files:**
- Create: knowledge_service/integrations/s3_source_object_store.py, scripts/migrate_source_objects.py, tests/service/test_s3_source_objects.py, tests/service/test_batch_object_references.py, tests/scripts/test_migrate_source_objects.py.
- Modify: knowledge_service/integrations/source_object_store.py, knowledge_service/services/source_documents.py (factory only), knowledge_service/services/ingest_batches.py, knowledge_service/services/document_ingest_runner.py (raw payload reads only), knowledge_service/repository/ingest_batch_store.py, knowledge_service/repository/source_store.py (GC references), pyproject.toml, .env.example, scripts/README.md.
- Add numbered migration 0029_source_upload_objects.sql (confirm next available number): nullable legacy content + upload_object_id FK; any source-object metadata insertion helper must use normal app DML.

- [ ] Write failing protocol tests for S3 byte/stream put, dedup/hash validation, bounded read, missing object, unsafe key, config errors and no local fallback; inject bounded S3 client doubles.
- [ ] Write failing PG/API tests proving new upload stores upload_object_id with content NULL, retries reload original bytes, ZIP shares retained package, legacy BYTEA still works, and GC protects queued objects.
- [ ] Run the new tests with the sole pytest session; record expected failures.
- [ ] Implement environment-selected local/s3 factory. Require explicit endpoint/bucket/credentials for s3, use boto3 with bounded retries/timeouts and SHA-256 verification. Add source-storage dependency explicitly without undoing current dependency work.
- [ ] Implement object registration, queue reference writes and legacy-compatible reads; keep existing call signatures where possible. The migration is additive and does not discard BYTEA before objects are validated.
- [ ] Add migration tool dry-run/apply and JSON manifest. Use resolve_dsn, redacted errors, referenced object inventory and per-object full verification. Do not auto-delete local files or switch configuration. Preserve xx/hash keys so existing versions and asset links stay unchanged.
- [ ] Run targeted tests plus source protocol, batch/resume, source API/raw recovery and script safety regressions. Test real S3 behavior later against the dedicated bucket.
- [ ] Spec review, fix/review, then quality review; do not advance with open correctness issues.

## Task 2: Rich document parsing and independent reading/OCR status

**Files:**
- Create: knowledge_service/integrations/document_media.py, knowledge_service/integrations/remote_images.py, tests/service/test_document_media.py, tests/service/test_remote_source_images.py, tests/service/test_rich_document_ingest.py.
- Modify: knowledge_service/integrations/document_parser.py, knowledge_service/services/source_documents.py, knowledge_service/services/document_ingest_runner.py, knowledge_service/repository/source_store.py (media metadata), knowledge_service/integrations/markdown_package.py where needed.
- Add next numbered source-asset provenance migration if additional fields are required; avoid changing existing migration files.

- [ ] Write failing tests using a genuine small DOCX and PDF plus injected DoclingDocument fixtures: retain image bytes, article ordering, caption/page metadata, stable asset references, safe HTML, original bytes and source mapping.
- [ ] Write failing tests for Markdown ZIP/Base64/public remote images, missing/private/redirect/oversized/unsupported external sources, repeated image references, no network re-fetch on reading and version immutability.
- [ ] Write failing tests that documents mode remains readable on no-text/failed OCR, while extraction modes preserve the strict existing quality gate and expose explicit warnings.
- [ ] Run the tests to establish red. Inspect installed Semantica/Docling interfaces rather than assuming image output survives the wrapper.
- [ ] Extend ParsedDocument with assets and structured provenance; produce ordered normalized Markdown referencing extracted images. Configure real Docling conversion to retain image/page data without downloading models in requests. Preserve parser_factory unit-test seam and OCR-disabled behavior.
- [ ] Freeze images into the object store, persist source-asset metadata and create authorized rendered URLs. Keep Markdown relative-package support; remote resolution uses bounded streamed HTTP with DNS/address validation on every redirect, no private/loopback/link-local destination, only supported decoded images.
- [ ] Pass asset and parser information through batch and supported legacy file-upload paths; preserve text-only inputs and reparse/new-version semantics.
- [ ] Separate reading assets from OCR success; only documents mode treats OCR quality failure as a nonfatal warning. Do not turn failed OCR into searchable/extracted text.
- [ ] Run related parser, markdown, source API/mapping, raw recovery, batch and extraction-gate tests in one pytest invocation. Perform actual CPU Docling conversion on generated fixtures, record limitations explicitly.
- [ ] Spec review then quality review, repairing issues before continuing.

## Task 3: Shared rich reader and default library view

**Files:**
- Create: knowledge_service/web/source-content.js, tests/service/source-content.test.cjs, tests/service/source-rich-reading.browser.cjs.
- Modify: knowledge_service/web/sources-view.js, knowledge_service/web/source-reader.js, knowledge_service/web/source-reader.css, knowledge_service/web/sources-library.css, knowledge_service/web/index.html and affected frontend contracts.

- [ ] Write failing Node/browser behavior checks: library opens a fixed rendered version with inline authenticated images, missing images show useful text, legacy documents fall back to saved text, close/switch aborts old reads and revokes Blob URLs, repeated source images and zoom are safe.
- [ ] Run Node tests to observe missing behavior before implementation.
- [ ] Extract a focused shared source-content loader/renderer with scoped cancellation, trusted server HTML, image hydration, lazy loading, image zoom and cleanup. Keep SourceReader evidence anchors and existing module exports intact.
- [ ] Make library default to rich content only when a source version exists; retain original-download, evidence reader, processing/history and plain-text access. Keep current project/scope/ticket race guards and release resources on every transition.
- [ ] Extend article CSS for tables, captions, images and mobile fit; reuse established typography/colors. Bump every changed/new resource version and update pinned contract strings.
- [ ] Run the full Node contract glob and relevant existing browser tests plus new rich-reading tests at 1440,768,390 widths. Inspect actual screenshots, image zoom and errors; no frontend build tooling.
- [ ] Spec review then quality review and required fixes.

## Task 4: Integration, configuration, migration and delivery

**Files:**
- Modify: docs/README.md, README.md (only relevant sections), docs/2026-10-14-批量知识写入与断点续跑运维说明.md.
- Create: docs/research/2026-10-28-source-rich-reading-verification.md; runtime artifacts in ignored .benchmarks/source-rich-reading/.

- [ ] Run the full applicable regression surface once; diagnose new failures with evidence and distinguish baseline failures. Never run two pytest sessions.
- [ ] Provision private knowledge-sources bucket and a restricted business account using existing MinIO admin access without printing secrets. Read .env through dotenv in a script, update only added nonconflicting source-storage settings, and keep credential output suppressed.
- [ ] Apply additive migrations using the repository migration entry point. Inventory current registered objects/queue bytes, copy and verify using the tool, then switch source backend only when the inventory verifies. Retain local originals as rollback.
- [ ] Restart backend using the existing startup workflow after saving verification baseline. Smoke a dedicated small project/input: retain/download original, parse and read images from MinIO, show article in library and preserve evidence authorization. Do not write into the retained capacity project.
- [ ] Ensure old objects lacking exact bytes are reported clearly; recover only from checksum-matching database text or legacy queue bytes. Do not fabricate originals or report incomplete migration as complete.
- [ ] Record commands, results, test counts/skips, sample coverage and material remaining limitations. Update operation docs with backup, migration and reprocessing behavior.
- [ ] Final independent compliance/quality review, inspect diff and fresh verification. Deliver concise Chinese outcome, evidence and any actual limitations; do not promise raw PDF preview unless implemented.
