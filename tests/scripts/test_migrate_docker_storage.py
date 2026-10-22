"""Safety checks for the explicit stopped-writer storage migration utility."""

import importlib.util
import hashlib
import json
import os
from pathlib import Path
import sys
import tarfile
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location(
    "migrate_docker_storage", ROOT / "scripts/migrate_docker_storage.py"
)
migration = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = migration
SPEC.loader.exec_module(migration)
ARCHIVE_SPEC = importlib.util.spec_from_file_location(
    "archive_windows_docker_bind", ROOT / "scripts/archive_windows_docker_bind.py"
)
native = importlib.util.module_from_spec(ARCHIVE_SPEC)
ARCHIVE_SPEC.loader.exec_module(native)


class StoppedSourceChecks(unittest.TestCase):
    def setUp(self):
        self.source = Path(tempfile.mkdtemp())
        self.container = {
            "state": {"Status": "exited", "ExitCode": 0},
            "mounts": [{"Type": "bind", "Source": str(self.source),
                        "Destination": "/etcd"}],
        }

    def test_clean_stopped_matching_source_is_accepted(self):
        migration.check_source(self.container, self.source, "/etcd")

    def test_running_or_failed_writer_is_refused(self):
        for state in ({"Status": "running", "ExitCode": 0},
                      {"Status": "exited", "ExitCode": 80}):
            with self.subTest(state=state):
                self.container["state"] = state
                with self.assertRaisesRegex(ValueError, "cleanly stopped"):
                    migration.check_source(self.container, self.source, "/etcd")

    def test_wrong_source_or_named_source_is_refused(self):
        for field, value in (("Source", str(self.source / "different")),
                             ("Type", "volume")):
            with self.subTest(field=field):
                original = dict(self.container["mounts"][0])
                self.container["mounts"][0][field] = value
                with self.assertRaisesRegex(ValueError, "bind source"):
                    migration.check_source(self.container, self.source, "/etcd")
                self.container["mounts"][0] = original

    def test_copy_proof_requires_matching_counts_and_hash(self):
        proof = """Source files and bytes: 2 73
Target files and bytes: 2 73
Source archive SHA256: aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa
Target archive SHA256: aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa
Verified byte-exact stopped-volume copy
"""
        result = migration.copy_proof(proof)
        self.assertEqual(result["files"], 2)
        self.assertEqual(result["bytes"], 73)
        with self.assertRaisesRegex(ValueError, "proof"):
            migration.copy_proof(proof.replace("Target files and bytes: 2 73",
                                               "Target files and bytes: 2 74"))
        with self.assertRaisesRegex(ValueError, "proof"):
            migration.copy_proof(proof.replace("Verified byte-exact stopped-volume copy", ""))


class NativeArchiveChecks(unittest.TestCase):
    def test_outputs_inside_source_or_same_destination_are_refused(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source"
            source.mkdir()
            with self.assertRaisesRegex(ValueError, "outside"):
                migration.check_output_path(source / "report.json", source)
            with self.assertRaisesRegex(ValueError, "outside"):
                native.create_archive(source, source / "data.tar", root / "manifest.json")
            with self.assertRaisesRegex(ValueError, "outside"):
                native.create_archive(source, root / "data.tar", source / "manifest.json")
            with self.assertRaisesRegex(ValueError, "different"):
                native.create_archive(source, root / "data.tar", root / "data.tar")
            self.assertEqual(list(source.iterdir()), [])
            manifest = source / "existing-manifest.json"
            manifest.write_text(json.dumps({"source": str(source), "entries": {}}), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "outside"):
                native.verify_volume("unused-fixture", manifest, "unused-image")
            self.assertEqual(list(source.iterdir()), [manifest])

    def test_pax_preserves_subsecond_mtime_content_and_empty_directories(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source"
            source.mkdir()
            (source / "empty").mkdir()
            content = b"\x00binary storage fixture\xff"
            fixture = source / "space name.bin"
            fixture.write_bytes(content)
            os.utime(fixture, ns=(1700000000123456700, 1700000000123456700))
            archive, manifest = root / "data.tar", root / "manifest.json"
            native.create_archive(source, archive, manifest)
            report = json.loads(manifest.read_text(encoding="utf-8"))
            self.assertEqual(report["files"], 1)
            self.assertEqual(report["entries"]["space name.bin"]["mtime_ns"], fixture.stat().st_mtime_ns)
            self.assertEqual(report["entries"]["space name.bin"]["sha256"], hashlib.sha256(content).hexdigest())
            self.assertEqual(report["entries"]["empty"]["type"], "directory")
            with tarfile.open(archive) as tar:
                member = tar.getmember("./space name.bin")
                self.assertEqual(native.member_summary(member)["mtime_ns"], fixture.stat().st_mtime_ns)
                self.assertEqual(tar.extractfile(member).read(), content)
            with self.assertRaisesRegex(ValueError, "must not already exist"):
                native.create_archive(source, archive, manifest)


if __name__ == "__main__":
    unittest.main()
