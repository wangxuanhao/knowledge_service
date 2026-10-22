"""Read a stopped Windows bind directory once, preserving its WSL metadata.

The caller must keep every writer stopped. Opaque storage bytes are archived;
no database records are parsed. NTFS $LXUID/$LXGID/$LXMOD attributes preserve
the Linux view that Windows tar.exe and os.stat do not expose.
"""

import argparse
import ctypes
from ctypes import wintypes
from decimal import Decimal
import hashlib
import json
import os
from pathlib import Path
import stat
import struct
import subprocess
import tarfile
import time


def linux_metadata(path):
    if os.name != "nt":
        info = path.lstat()
        return info.st_uid, info.st_gid, stat.S_IMODE(info.st_mode)
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    ntdll = ctypes.WinDLL("ntdll")
    kernel.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
                                  wintypes.LPVOID, wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
    kernel.CreateFileW.restype = wintypes.HANDLE
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]

    class IOStatus(ctypes.Structure):
        _fields_ = [("Status", ctypes.c_void_p), ("Information", ctypes.c_size_t)]

    ntdll.NtQueryEaFile.argtypes = [wintypes.HANDLE, ctypes.POINTER(IOStatus),
                                   wintypes.LPVOID, wintypes.ULONG, wintypes.BOOLEAN,
                                   wintypes.LPVOID, wintypes.ULONG, wintypes.LPVOID, wintypes.BOOLEAN]
    ntdll.NtQueryEaFile.restype = wintypes.LONG
    handle = kernel.CreateFileW(str(path), 0x0008, 7, None, 3, 0x02000000, None)
    if handle == wintypes.HANDLE(-1).value:
        raise ctypes.WinError(ctypes.get_last_error())
    buffer = ctypes.create_string_buffer(65536)
    status = IOStatus()
    try:
        code = ntdll.NtQueryEaFile(handle, ctypes.byref(status), buffer, len(buffer),
                                 False, None, 0, None, True) & 0xFFFFFFFF
    finally:
        kernel.CloseHandle(handle)
    if code == 0xC0000052:  # STATUS_NO_EAS_ON_FILE; Docker Desktop default view.
        return 0, 0, 0o777
    if code:
        raise OSError("NtQueryEaFile failed with status " + hex(code))
    data, attributes, offset = buffer.raw[:status.Information], {}, 0
    while offset < len(data):
        following, _, name_size, value_size = struct.unpack_from("<IBBH", data, offset)
        name = data[offset + 8:offset + 8 + name_size]
        value = data[offset + 9 + name_size:offset + 9 + name_size + value_size]
        attributes[name] = value
        if not following:
            break
        offset += following

    def integer(name, default):
        value = attributes.get(name)
        return struct.unpack("<I", value)[0] if value else default

    return integer(b"$LXUID", 0), integer(b"$LXGID", 0), integer(b"$LXMOD", 0o777) & 0o7777


class HashReader:
    def __init__(self, stream):
        self.stream, self.digest = stream, hashlib.sha256()

    def read(self, size=-1):
        chunk = self.stream.read(size)
        self.digest.update(chunk)
        return chunk


def member_summary(member, digest=None):
    name = member.name.removeprefix("./").rstrip("/") or "."
    return {"path": name, "type": "file" if member.isfile() else "directory",
            "size": member.size,
            "mtime_ns": int(Decimal(str(member.pax_headers.get("mtime", member.mtime))) * 1000000000),
            "uid": member.uid,
            "gid": member.gid, "mode": member.mode, "sha256": digest}


def create_archive(source, archive, manifest):
    source, archive, manifest = source.resolve(strict=True), archive.resolve(), manifest.resolve()
    if archive == manifest:
        raise ValueError("Archive and manifest destinations must be different")
    if archive.is_relative_to(source) or manifest.is_relative_to(source):
        raise ValueError("Archive and manifest must be outside the source directory")
    if archive.exists() or manifest.exists():
        raise ValueError("Archive and manifest destinations must not already exist")
    started, entries = time.perf_counter(), {}
    with tarfile.open(archive, "x", format=tarfile.PAX_FORMAT) as tar:
        def add(path):
            info = path.lstat()
            if path.is_symlink() or getattr(info, "st_file_attributes", 0) & 0x400:
                raise ValueError("Reparse points/symlinks require a separately reviewed migration")
            directory = path.is_dir()
            if not directory and not path.is_file():
                raise ValueError("Only directories and regular files are supported")
            uid, gid, mode = linux_metadata(path)
            relative = path.relative_to(source).as_posix()
            member = tarfile.TarInfo("." if relative == "." else "./" + relative)
            member.uid, member.gid, member.mode, member.mtime = uid, gid, mode, int(info.st_mtime)
            member.pax_headers["mtime"] = str(Decimal(info.st_mtime_ns) / 1000000000)
            member.type = tarfile.DIRTYPE if directory else tarfile.REGTYPE
            member.size = 0 if directory else info.st_size
            digest = None
            if directory:
                tar.addfile(member)
            else:
                with path.open("rb") as stream:
                    reader = HashReader(stream)
                    tar.addfile(member, reader)
                    digest = reader.digest.hexdigest()
                after = path.stat()
                if (info.st_size, info.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
                    raise ValueError("A source file changed while archiving; keep every writer stopped")
            entry = member_summary(member, digest)
            entries[entry["path"]] = entry
            if len(entries) % 2000 == 0:
                print("Archived entries", len(entries), "seconds", round(time.perf_counter()-started, 2), flush=True)
        add(source)
        def fail_walk(error):
            raise error

        for root, directories, files in os.walk(source, onerror=fail_walk):
            for name in sorted(directories):
                add(Path(root) / name)
            for name in sorted(files):
                add(Path(root) / name)
    result = {"source": str(source), "archive": str(archive), "entries": entries,
              "files": sum(e["type"] == "file" for e in entries.values()),
              "bytes": sum(e["size"] for e in entries.values()),
              "archive_seconds": time.perf_counter()-started}
    manifest.write_text(json.dumps(result, indent=2), encoding="utf-8")
    print("Native archive complete; files", result["files"], "bytes", result["bytes"],
          "seconds", round(result["archive_seconds"], 2), flush=True)


def verify_volume(volume, manifest, image):
    expected = json.loads(manifest.read_text(encoding="utf-8"))
    from migrate_docker_storage import check_output_path
    protected = Path(expected["source"])
    if protected.name in ("postgres", "etcd", "minio", "milvus"):
        protected = protected.parent
    for suffix in (".verification.json", ".differences.json"):
        check_output_path(manifest.with_suffix(suffix), protected)
    subprocess.run(["docker", "volume", "inspect", volume], check=True, capture_output=True)
    command = ["docker", "run", "--rm", "--pull", "never", "--network", "none",
               "--user", "0:0", "--mount", "type=volume,src=" + volume + ",dst=/target,readonly",
               "--entrypoint", "bash", image, "-c",
               "tar --sort=name --numeric-owner --format=pax -C /target -cf - ."]
    process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    actual = {}
    with tarfile.open(fileobj=process.stdout, mode="r|") as tar:
        for member in tar:
            if not member.isfile() and not member.isdir():
                raise ValueError("Unexpected target entry type")
            digest = None
            if member.isfile():
                reader = HashReader(tar.extractfile(member))
                while reader.read(1024 * 1024):
                    pass
                digest = reader.digest.hexdigest()
            entry = member_summary(member, digest)
            actual[entry["path"]] = entry
    error = process.stderr.read().decode("utf-8", errors="replace")
    if process.wait():
        raise ValueError("Target tar read failed: " + error)
    if actual != expected["entries"]:
        mismatches = {key: {"source": expected["entries"].get(key), "target": actual.get(key)}
                      for key in set(actual) | set(expected["entries"])
                      if actual.get(key) != expected["entries"].get(key)}
        manifest.with_suffix(".differences.json").write_text(json.dumps(mismatches, indent=2), encoding="utf-8")
        raise ValueError("Target content/metadata manifest differs in " + str(len(mismatches)) + " entries")
    canonical_hash = hashlib.sha256(json.dumps(actual, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    result = {"files": expected["files"], "bytes": expected["bytes"], "entries": len(actual),
              "verified": True, "mtime_precision": "nanoseconds",
              "source_manifest_sha256": canonical_hash, "target_manifest_sha256": canonical_hash,
              "fields": ["path", "type", "size", "mtime_ns", "uid", "gid", "mode", "sha256"]}
    manifest.with_suffix(".verification.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    print("Verified file content and specified Linux metadata fields:", json.dumps(result), flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path)
    parser.add_argument("--archive", type=Path)
    parser.add_argument("--manifest", required=True, type=Path)
    parser.add_argument("--verify-volume")
    parser.add_argument("--writers-stopped", action="store_true")
    parser.add_argument("--helper-image", default="milvusdb/milvus:v3.0.2")
    args = parser.parse_args()
    if args.verify_volume:
        verify_volume(args.verify_volume, args.manifest, args.helper_image)
    elif args.source and args.archive:
        if not args.writers_stopped:
            parser.error("Archiving requires --writers-stopped after stopping every application/job")
        from migrate_docker_storage import inspect_sources
        inspect_sources(args.source.resolve(strict=True).parent)
        from migrate_docker_storage import check_output_path
        for path in (args.archive, args.manifest):
            check_output_path(path, args.source.resolve(strict=True).parent)
        create_archive(args.source.resolve(strict=True), args.archive.resolve(), args.manifest.resolve())
        inspect_sources(args.source.resolve(strict=True).parent)
    else:
        parser.error("Provide --source and --archive, or --verify-volume")
