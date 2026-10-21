"""Failure paths must not change the active environment or inherit mirrors."""
import importlib.util
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("runtime_installer", ROOT / "runtime/install.py")
installer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(installer)


class InstallerTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name) / "runtime"
        self.root.mkdir()
        self.bundle = Path(self.temporary.name) / "bundle"
        self.bundle.mkdir()
        (self.bundle / "manifest.json").write_text(json.dumps(dict(bundleHashes={}, environmentVersion="new", bridgeVersion="0.2.0", bridgeWheel="bridge.whl")))
        self.old = self.root / "envs/old/Scripts/python.exe"
        self.old.parent.mkdir(parents=True)
        self.old.write_bytes(b"previous working environment")
        self.current = dict(environmentVersion="old", runtimeRoot=str(self.root), python=str(self.old))
        (self.root / "current.json").write_text(json.dumps(self.current))
        installer.STOP.clear()
        installer.CHILD = None
        installer.STATE = None

    def fail_preparation(self, callback):
        argv = ["install.py", "--root", str(self.root), "--bundle", str(self.bundle), "--uv", str(self.root / "uv.exe")]
        with patch("sys.argv", argv), patch.object(installer.threading.Thread, "start"), patch.object(installer, "run", side_effect=callback), patch.object(installer, "emit"):
            return installer.main()

    def test_failed_upgrade_keeps_previous_pointer_and_venv_path(self):
        def failure(command, env, stage):
            self.assertEqual(stage, "venv")
            target = Path(command[-1])
            self.assertTrue(target.is_relative_to(self.root / "envs"))
            self.assertTrue(target.name.startswith("new-"))
            raise RuntimeError("simulated disk full")
        self.assertEqual(self.fail_preparation(failure), 1)
        self.assertEqual(json.loads((self.root / "current.json").read_text()), self.current)
        self.assertEqual(self.old.read_bytes(), b"previous working environment")
        self.assertEqual(json.loads((self.root / "state.json").read_text())["stage"], "failed")

    def test_migrated_pointer_is_rebuilt_and_host_settings_are_isolated(self):
        self.current.update(environmentVersion="new", runtimeRoot="C:/previous-location")
        (self.root / "current.json").write_text(json.dumps(self.current))
        def failure(command, env, stage):
            self.assertEqual(stage, "venv")
            self.assertNotEqual(Path(command[-1]), self.old.parent.parent)
            self.assertEqual(env["UV_INDEX_URL"], "https://pypi.org/simple")
            self.assertNotIn("PIP_INDEX_URL", env)
            self.assertNotIn("PYTHONPATH", env)
            self.assertEqual(env["UV_PYTHON_INSTALL_REGISTRY"], "0")
            raise RuntimeError("stop after isolation assertions")
        with patch.dict(os.environ, {"PIP_INDEX_URL": "https://invalid.example/simple", "UV_INDEX_URL": "https://invalid.example/simple", "PYTHONPATH": "unsafe"}):
            self.assertEqual(self.fail_preparation(failure), 1)
        self.assertEqual(json.loads((self.root / "current.json").read_text()), self.current)

    def test_bundle_path_cannot_escape(self):
        with self.assertRaises(ValueError):
            installer.confined(self.root, self.root / "../other")

    def test_failed_attempt_reuses_its_final_directory_on_retry(self):
        attempts = []
        def failure(command, env, stage):
            attempts.append(command[-1])
            raise RuntimeError("simulated interrupted install")
        self.assertEqual(self.fail_preparation(failure), 1)
        self.assertEqual(self.fail_preparation(failure), 1)
        self.assertEqual(attempts[0], attempts[1])
        self.assertEqual(json.loads((self.root / "current.json").read_text()), self.current)


if __name__ == "__main__":
    unittest.main()
