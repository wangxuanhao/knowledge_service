# Unified media and single-document ZIP implementation plan

> **For agentic workers:** Use subagent-driven-development for bounded implementation tasks. Root coordinates the single pytest session, integration, verification and final master commit.

**Goal:** Direct image/long-image upload, shared DOCX/PDF/MD media OCR, one ZIP per document, appendable multi-file submission and traceable original reading.

**Architecture:** Keep PostgreSQL source versions and media assets authoritative. Add a strict new-upload package boundary while retaining the legacy reader for frozen historical sources; normalize a package into exactly one parsed document. Tile long images within the existing OCR adapter contract and map boxes back to the canonical original image.

**Tech Stack:** Python 3.12 existing llm_model, Pillow/RapidOCR, Docling, FunASR, FastAPI/PostgreSQL/MinIO, native JS/CSS.

## Task 1: OCR long-image preprocessing and coordinate mapping

Files: integrations/image_ocr.py; new integrations/image_ocr_tiles.py; tests/service/test_long_image_ocr.py.

- [x] Add failing behavior tests for preserving narrow text in a 1080×20000 image, complete coverage, overlap deduplication, repeated text at different positions, normalized boxes, ordering and blank/failed tiles.
- [x] Root runs red test once; implement bounded overlapping tiles, EXIF handling and geometry mapping without shrinking the entire long image.
- [x] Preserve the existing adapter output and quality receipt interfaces; include tiling strategy in fingerprints. Persist tile state/output in a new processing_details field on source_asset_ocr_runs (new migration 0033), show partial coverage and retained successful text, and pass only matching successful tile receipts into retries. Explicitly fail partial inference rather than publish a complete-success receipt; retain original media for reading. Root owns services/image_ocr.py and source_store integration.
- [x] Root runs targeted regression; reviewer checks spec and code.

## Task 2: Single-document package reader and packaging CLI

Files: new integrations/document_package.py; scripts/package_rule_documents.py; tests/service/test_single_document_package.py; tests/scripts/test_package_rule_documents.py.

- [x] Add red tests: one MD and referenced assets => one document; multiple MD no explicit main => error; v2 document.entry; ordered document.pages; one outer folder; legacy single entry; reject legacy multiple/unknown versions/unsafe paths.
- [x] Implement strict upload reading returning the legacy-compatible documents list with exactly one normalized entry. Accept parser injection for DOCX/PDF/video/image entry without importing application services.
- [x] CLI default one ZIP per selected MD; emit v2 manifest and reporting. Add ordered images mode; preserve source read-only checks, file limits, metadata and source-path relationships.
- [x] Do not run pytest concurrently or commit shared changes; root owns red/green execution and integration.

## Task 3: Parser, upload, preview and source integration

Files: integrations/document_parser.py; services/ingest_batches.py; services/document_preview.py; services/source_documents.py; services/document_ingest_runner.py; models.py; API preview contracts.

- [x] Add failing tests for direct image parse, media-only source text, upload/preview item count, image pages provenance and unchanged PDF/DOCX/video support.
- [x] Image parser validates actual bytes, dimensions, frame semantics and returns original asset with no invented body text.
- [x] New-upload paths use strict package reader; existing frozen-source restoration keeps legacy behavior. Normalize exactly one item with container ZIP original and contained primary bytes/provenance.
- [x] Capability output declares direct image types and actual pixel/byte limits. Preview original images without doing OCR, then formal processing runs OCR.
- [x] Retain page/image/ASR mappings; use parser body order for media-derived text where possible without breaking source spans.
- [x] PDF original open/download and page-linked reading; preserve native/scanned/mixed parsing behavior.

## Task 4: Upload and original reader UI

Files: web/ingest-mode.js; web/chunk-preview.js; web/source-content.js; web/source-reader.js and CSS; web/index.html; associated Node contracts.

- [x] Add meaningful Node/UI checks for append selections, direct image accept/types, one ZIP item summary, no ZIP-document switcher, image-page reading, zoom and evidence region focus.
- [x] Upload count is document count; adding files does not clear prior selections. Do not merge unrelated selected files.
- [x] Preserve original/parsed/chunk views, video playback, image Blob disposal, project isolation and late-response cancellation.
- [x] Bump changed JS/CSS query versions in index.html and update necessary cache contracts.
- [x] Root performs actual browser operations after service restart.

## Task 5: Verification, manuals and master delivery

Files: docs/平台规则资料上传与ZIP打包操作手册.md; docs/2026-11-11-unified-media-acceptance.md; README.md; scripts/README.md.

- [x] Serial Python focused regression covering modified integrations, service/API paths, package scripts and original provenance; run the full Node contract suite separately.
- [x] Generate real DOCX with embedded picture, digital/scanned PDF, long image, MD-assets package and ordered image ZIP; verify upload, original reading, parsed text, source evidence and append batch behavior. Reuse existing local models; do not disclose credentials.
- [x] Record real OCR coverage at top/middle/bottom, tile-boundary checks, actual limits and limitations. Video is ASR speech text; do not claim frame OCR or layout-perfect Word rendering.
- [x] Refresh manual and examples for one-ZIP-one-document; clearly explain rejection/splitting of old multi-document packages and retention of old saved sources.
- [ ] Final spec and quality review, git diff --check, review status and exact staged paths, commit to local master. No push requested.

Only root runs pytest because tests share knowledge_test and teardown. Do not change .env or delete existing projects/data. User approved implementation and local master submission; continue through review without asking for the same approval again.
