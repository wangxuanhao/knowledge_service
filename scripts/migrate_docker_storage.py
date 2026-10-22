"""Copy cleanly stopped Docker bind data into empty Linux named volumes.

No services are stopped/started, no Compose file is changed, and no source or
volume is deleted. See docs/2026-10-19-Docker-Linux-volume-migration.md.
"""

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import subprocess


STORES = (
    ("postgres", "knowledge-postgres", "/var/lib/postgresql"),
    ("etcd", "milvus-etcd", "/etcd"),
    ("minio", "milvus-minio", "/minio_data"),
    ("milvus", "milvus-standalone", "/var/lib/milvus"),
)
HELPER = Path(__file__).with_name("copy_stopped_docker_volume.sh")


def docker(*args, check=True):
    result = subprocess.run(["docker", *args], text=True, capture_output=True)
    if check and result.returncode:
        raise ValueError("Docker command failed: " + result.stderr.strip())
    return result


def check_source(container, source, destination):
    state = container["state"]
    if state.get("Status") != "exited" or state.get("ExitCode") != 0:
        raise ValueError("Every source writer must be cleanly stopped (exited, code 0)")
    mounts = [m for m in container["mounts"] if m["Destination"] == destination]
    if (len(mounts) != 1 or mounts[0]["Type"] != "bind"
            or os.path.normcase(os.path.realpath(mounts[0]["Source"]))
            != os.path.normcase(os.path.realpath(source))):
        raise ValueError("Container bind source does not match the explicit source directory")


def inspect_sources(source_root):
    for key, container, destination in STORES:
        source = source_root / key
        if not source.is_dir() or not any(source.iterdir()):
            raise ValueError("Source directory is missing or empty: " + str(source))
        raw = docker("inspect", "--format",
                     '{"state":{{json .State}},"mounts":{{json .Mounts}}}',
                     container).stdout
        check_source(json.loads(raw), source, destination)


def copy_proof(output):
    def value(label):
        match = re.search(r"^" + re.escape(label) + r": (.+)$", output, re.MULTILINE)
        return match.group(1).strip() if match else None

    source_counts = value("Source files and bytes")
    source_hash = value("Source archive SHA256")
    if (not source_counts or source_counts != value("Target files and bytes")
            or not source_hash or not re.fullmatch(r"[0-9a-f]{64}", source_hash)
            or source_hash != value("Target archive SHA256")
            or "Verified byte-exact stopped-volume copy" not in output):
        raise ValueError("Copy proof is absent or inconsistent")
    files, size = map(int, source_counts.split())
    return {"files": files, "bytes": size, "source_sha256": source_hash,
            "target_sha256": source_hash, "verified": True}


def check_output_path(path, source):
    if path.resolve().is_relative_to(source.resolve()):
        raise ValueError("Audit outputs must be outside every source directory")


def check_target(volume, key, image):
    existing = docker("volume", "inspect", volume, check=False)
    if existing.returncode:
        return False
    labels = json.loads(existing.stdout)[0].get("Labels") or {}
    if (labels.get("com.docker.compose.project") != "knowledge-service"
            or labels.get("com.docker.compose.volume") != key + "_data"):
        raise ValueError("Existing target has unexpected ownership labels: " + volume)
    users = docker("ps", "-a", "--filter", "volume=" + volume,
                   "--format", "{{.Names}}").stdout.strip()
    if users:
        raise ValueError("Target volume is already attached to a container: " + volume)
    docker("run", "--rm", "--pull", "never", "--network", "none",
           "--user", "0:0", "--mount", "type=volume,src=" + volume + ",dst=/target,readonly",
           "--entrypoint", "bash", image, "-c",
           'test -z "$(find /target -mindepth 1 -maxdepth 1 -print -quit)"')
    return True


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-root", type=Path, required=True,
                        help="Existing bind volumes directory, containing postgres/etcd/minio/milvus")
    parser.add_argument("--report", type=Path, required=True, help="New audit JSON path")
    parser.add_argument("--helper-image", default="milvusdb/milvus:v3.0.2",
                        help="Already installed trusted image with bash and GNU tar/find/coreutils")
    parser.add_argument("--execute", action="store_true", help="Copy; omission performs read-only preflight")
    parser.add_argument("--windows-native", action="store_true",
                        help="Use native Windows reads with WSL EA/PAX metadata and content-manifest verification")
    parser.add_argument("--writers-stopped", action="store_true",
                        help="Acknowledge all applications/jobs are stopped and remain stopped")
    args = parser.parse_args()
    source_root = args.source_root.resolve(strict=True)
    report_path = args.report.resolve()
    check_output_path(report_path, source_root)
    if args.execute and not args.writers_stopped:
        parser.error("--execute requires --writers-stopped after stopping applications and jobs")
    if args.windows_native and os.name != "nt":
        parser.error("--windows-native requires Windows")
    if report_path.exists():
        raise ValueError("Report path already exists; choose a new audit path")
    for path in (source_root, HELPER.resolve()):
        if any(char in str(path) for char in (",", "\n", "\r")):
            raise ValueError("Mount paths cannot contain commas or newlines")
    docker("image", "inspect", "--format", "{{.Id}}", args.helper_image)
    inspect_sources(source_root)
    plan = []
    for key, container, destination in STORES:
        volume = "knowledge_service_" + key + "_data"
        exists = check_target(volume, key, args.helper_image)
        plan.append({"store": key, "container": container,
                     "source": str(source_root / key), "target_volume": volume,
                     "container_path": destination, "target_exists": exists})
    report = {"started_at": datetime.now(timezone.utc).isoformat(),
              "mode": "execute" if args.execute else "preflight",
              "source_read_only": True, "source_retained": True, "copies": plan}
    report_path.parent.mkdir(parents=True, exist_ok=True)

    def checkpoint():
        report_path.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")

    checkpoint()
    if not args.execute:
        print("Preflight passed for all four stopped sources and empty/absent targets")
        return
    try:
        for item in plan:
            inspect_sources(source_root)
            key, volume = item["store"], item["target_volume"]
            exists = check_target(volume, key, args.helper_image)
            if not exists:
                docker("volume", "create", "--label", "com.docker.compose.project=knowledge-service",
                       "--label", "com.docker.compose.volume=" + key + "_data",
                       "--label", "knowledge-service.migration=stopped-bind-copy", volume)
            if args.windows_native:
                from archive_windows_docker_bind import create_archive, verify_volume
                archive = report_path.with_name(report_path.stem + "-" + key + ".tar")
                manifest = report_path.with_name(report_path.stem + "-" + key + ".manifest.json")
                create_archive(Path(item["source"]), archive, manifest)
                inspect_sources(source_root)
                docker("run", "--rm", "--pull", "never", "--network", "none", "--user", "0:0",
                       "--mount", "type=bind,src=" + str(archive) + ",dst=/archive.tar,readonly",
                       "--mount", "type=volume,src=" + volume + ",dst=/target",
                       "--entrypoint", "bash", args.helper_image, "-c",
                       'test -z "$(find /target -mindepth 1 -maxdepth 1 -print -quit)" && '
                       'tar --same-owner --same-permissions --delay-directory-restore -C /target -xf /archive.tar')
                verify_volume(volume, manifest, args.helper_image)
                inspect_sources(source_root)
                proof = json.loads(manifest.with_suffix(".verification.json").read_text(encoding="utf-8"))
                item.update(proof, archive=str(archive), manifest=str(manifest))
                checkpoint()
                continue
            log_path = report_path.with_name(report_path.stem + "-" + key + ".log")
            command = ["docker", "run", "--rm", "--pull", "never", "--network", "none",
                       "--user", "0:0", "--mount", "type=bind,src=" + item["source"] + ",dst=/source,readonly",
                       "--mount", "type=volume,src=" + volume + ",dst=/target",
                       "--mount", "type=bind,src=" + str(HELPER.resolve()) + ",dst=/copy.sh,readonly",
                       "--entrypoint", "bash", args.helper_image, "/copy.sh"]
            with log_path.open("x", encoding="utf-8") as log:
                process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                           text=True, encoding="utf-8")
                for line in process.stdout:
                    print(key + ": " + line, end="", flush=True)
                    log.write(line)
                    log.flush()
                if process.wait():
                    raise ValueError("Copy failed for " + key + "; inspect its audit log")
            item.update(copy_proof(log_path.read_text(encoding="utf-8")))
            inspect_sources(source_root)
            item["log"] = str(log_path)
            checkpoint()
        inspect_sources(source_root)
        report["completed_at"] = datetime.now(timezone.utc).isoformat()
        report["all_copies_verified"] = True
        checkpoint()
        print("All copies verified. Sources retained. Services remain stopped; review the runbook before switching.")
    except (ValueError, OSError) as exc:
        report["error"] = str(exc)
        checkpoint()
        raise


if __name__ == "__main__":
    try:
        main()
    except (ValueError, OSError) as exc:
        raise SystemExit(str(exc))
