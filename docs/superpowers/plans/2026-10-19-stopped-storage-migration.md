# Stopped Storage Migration Implementation Plan

> **For agentic workers:** This is an extension of the already approved backend capacity work. The parent task requires current-session execution without commits or additional agents.

**Goal:** Move the four stopped data stores from Windows binds to verified Linux volumes and retain a repeatable migration utility.

**Architecture:** Check every existing writer container and source mount before copying. Refuse nonempty targets, preserve source directories, then validate contents and Linux metadata before switching Compose. Windows uses native reads and WSL extended attributes to avoid slow repeated bind scans.

**Tech Stack:** Python standard library, Win32 EA API, Docker CLI, GNU tar/PAX.

- [x] Check clean container exits and matching bind sources; add regression checks in `tests/scripts/test_migrate_docker_storage.py`.
- [x] Promote stopped-copy helper to `scripts/copy_stopped_docker_volume.sh` and host guard to `scripts/migrate_docker_storage.py`.
- [x] Add `scripts/archive_windows_docker_bind.py` for native PAX/content-manifest verification.
- [x] Document explicit preflight, stop/copy/switch/recovery and rollback boundaries in `docs/2026-10-19-Docker-Linux-volume-migration.md`.
- [x] Verify a synthetic native round trip, including permission/uid/gid and subsecond mtime; reject deliberate target corruption.
- [x] Finish and verify all four real storage copies, restore services, compare aggregate database and Milvus metadata with the pre-stop baseline.
- [x] Submit and verify the complete 450k-vector projection, then pass final capacity verification with no concurrent test I/O. Background HNSW/scalar index builds remained in progress and are explicitly recorded; complete graph 20.1887 seconds, native warm semantic P95 0.2708 seconds, hybrid P95 0.2778 seconds.
