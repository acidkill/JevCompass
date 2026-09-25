from pathlib import Path
import io
import sys
import tarfile
import tempfile
import unittest
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tools.check_distribution import check


def source_tree(root: Path, version: str = "7.4.2") -> dict[str, bytes]:
    files = {
        "pyproject.toml": (
            '[project]\nname = "jevcompass"\nlicense = "Apache-2.0"\nlicense-files = ["LICENSE"]\nversion = "' + version + '"\n'
        ).encode(),
        "README.md": b"# JevCompass\nCurrent release notes.\n",
        "LICENSE": b"Apache License\nVersion 2.0, January 2004\n",
        "src/jevcompass/__init__.py": b'"""Fresh package."""\n',
        "src/jevcompass/cli.py": b"def main():\n    return 0\n",
        "src/jevcompass/catalog_data.json": b'{"skills": []}\n',
    }
    for name, data in files.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    return files


def make_archive(
    tmp_path: Path, files: dict[str, bytes], kind: str,
    metadata_version: str = "7.4.2", metadata_readme: bytes | None = None,
) -> Path:
    description = files["README.md"] if metadata_readme is None else metadata_readme
    metadata = (
        f"Metadata-Version: 2.4\nName: jevcompass\nVersion: {metadata_version}\n"
        "License-Expression: Apache-2.0\nLicense-File: LICENSE\n\n"
    ).encode() + description
    if kind == "wheel":
        archive = tmp_path / "jevcompass-7.4.2-py3-none-any.whl"
        members = {
            name.removeprefix("src/"): data
            for name, data in files.items()
            if name.startswith("src/jevcompass/")
        }
        members.update({
            "README.md": files["README.md"],
            "pyproject.toml": files["pyproject.toml"],
            "jevcompass-7.4.2.dist-info/METADATA": metadata,
            "jevcompass-7.4.2.dist-info/licenses/LICENSE": files["LICENSE"],
        })
        with zipfile.ZipFile(archive, "w") as bundle:
            for name, data in members.items():
                bundle.writestr(name, data)
    else:
        archive = tmp_path / "jevcompass-7.4.2.tar.gz"
        members = dict(files)
        members["PKG-INFO"] = metadata
        with tarfile.open(archive, "w:gz") as bundle:
            for name, data in members.items():
                info = tarfile.TarInfo(f"jevcompass-7.4.2/{name}")
                info.size = len(data)
                bundle.addfile(info, io.BytesIO(data))
    return archive


class DistributionFreshnessTests(unittest.TestCase):
    def _check_variant(
        self, kind: str, mutate=None, metadata_version="7.4.2", metadata_readme=None,
    ):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "source"
            files = source_tree(source)
            if mutate is not None:
                mutate(files)
            archive = make_archive(root, files, kind, metadata_version, metadata_readme)
            return check(archive, source)

    def test_fresh_wheel_and_sdist_match_current_sources(self):
        for kind in ("wheel", "sdist"):
            with self.subTest(kind=kind):
                self._check_variant(kind)

    def test_stale_module_readme_and_pyproject_fail(self):
        for kind in ("wheel", "sdist"):
            for stale_file in ("src/jevcompass/cli.py", "README.md", "pyproject.toml"):
                with self.subTest(kind=kind, stale_file=stale_file):
                    def make_stale(files):
                        files[stale_file] = b"outdated bytes\n"

                    with self.assertRaisesRegex(AssertionError, "stale packaged file"):
                        self._check_variant(kind, make_stale)

    def test_stale_license_fails_for_wheel_and_sdist(self):
        for kind in ("wheel", "sdist"):
            with self.subTest(kind=kind):
                def make_stale(files):
                    files["LICENSE"] = b"outdated license text\n"

                with self.assertRaisesRegex(AssertionError, "missing or stale Apache license"):
                    self._check_variant(kind, make_stale)

    def test_stale_metadata_version_fails(self):
        for kind in ("wheel", "sdist"):
            with self.subTest(kind=kind):
                with self.assertRaisesRegex(AssertionError, "metadata version"):
                    self._check_variant(kind, metadata_version="0.1.0")

    def test_stale_metadata_readme_fails(self):
        for kind in ("wheel", "sdist"):
            with self.subTest(kind=kind):
                with self.assertRaisesRegex(
                    AssertionError, "stale README in distribution metadata"
                ):
                    self._check_variant(kind, metadata_readme=b"# Old README\\n")

    def test_missing_expected_module_fails(self):
        for kind in ("wheel", "sdist"):
            with self.subTest(kind=kind):
                def omit_module(files):
                    del files["src/jevcompass/cli.py"]

                with self.assertRaisesRegex(AssertionError, "package files differ"):
                    self._check_variant(kind, omit_module)

    def test_unexpected_module_fails(self):
        for kind in ("wheel", "sdist"):
            with self.subTest(kind=kind):
                def add_module(files):
                    files["src/jevcompass/unreleased.py"] = b"PRIVATE = True\n"

                with self.assertRaisesRegex(AssertionError, "package files differ"):
                    self._check_variant(kind, add_module)


if __name__ == "__main__":
    unittest.main()
