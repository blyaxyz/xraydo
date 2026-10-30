from pathlib import Path
import importlib.util
import tempfile
import unittest


def load_installer():
    path = Path(__file__).resolve().parents[1] / "scripts" / "install.py"
    spec = importlib.util.spec_from_file_location("xraydo_installer", path)
    module = importlib.util.module_from_spec(spec)
    if spec.loader is None:
        raise RuntimeError("installer module has no loader")
    spec.loader.exec_module(module)
    return module


class InstallerTests(unittest.TestCase):
    def test_os_release_parser(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            release = Path(directory) / "os-release"
            release.write_text('ID="ubuntu"\nVERSION_ID="24.04"\n', encoding="utf-8")
            self.assertEqual(
                load_installer().os_release(release),
                {"ID": "ubuntu", "VERSION_ID": "24.04"},
            )
