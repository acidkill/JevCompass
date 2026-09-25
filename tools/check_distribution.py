"""Fail a release if distributions are stale, unexpected, or contain private markers."""

from email.parser import BytesParser
from pathlib import Path
import tarfile
import tomllib
import zipfile


BLOCKED = (b"/home/acidkill/", b"PERPLEXITY_API_KEY=", b"OPENROUTER_API_KEY=",
           b"private-client-secret", b"node_modules/", b"jev-use@")
ALLOWED_ROOT = {"README.md", "LICENSE", "MANIFEST.in", "pyproject.toml", "PKG-INFO", "setup.cfg"}
PROJECT_ROOT = Path(__file__).resolve().parents[1]
PACKAGE_SOURCE = Path("src/jevcompass")


def _distribution_files(archive: Path) -> dict[str, bytes]:
    if archive.suffix == ".whl":
        with zipfile.ZipFile(archive) as bundle:
            return {name: bundle.read(name) for name in bundle.namelist()
                    if not name.endswith("/")}
    if archive.name.endswith(".tar.gz"):
        with tarfile.open(archive, "r:gz") as bundle:
            result = {}
            for member in bundle:
                if member.isfile():
                    stream = bundle.extractfile(member)
                    assert stream is not None, member.name
                    result[member.name] = stream.read()
            return result
    raise AssertionError(f"Unsupported distribution archive: {archive}")


def check(archive: Path, source_root: Path = PROJECT_ROOT) -> None:
    files = _distribution_files(archive)
    is_sdist = archive.name.endswith(".tar.gz")
    if is_sdist:
        # sdist members are prefixed by the generated jevcompass-VERSION/ directory.
        files = {name.split("/", 1)[-1]: data for name, data in files.items()}

    expected_version = tomllib.loads((source_root / "pyproject.toml").read_text())["project"]["version"]
    package_root = source_root / PACKAGE_SOURCE
    expected_package = {
        f"jevcompass/{path.name}": path.read_bytes()
        for path in package_root.iterdir()
        if path.is_file() and (path.suffix == ".py" or path.name == "catalog_data.json")
    }
    for name in ("jevcompass-focused-tests", "jevcompass-regression-review"):
        path = package_root / "bundled_skills" / name / "SKILL.md"
        assert path.is_file(), f"missing bundled skill: {name}"
        relative = path.relative_to(package_root).as_posix()
        expected_package[f"jevcompass/{relative}"] = path.read_bytes()
    package_prefix = "src/jevcompass/" if is_sdist else "jevcompass/"
    actual_package = {name: data for name, data in files.items()
                      if name.startswith(package_prefix)}
    if is_sdist:
        actual_package = {name.removeprefix("src/"): data for name, data in actual_package.items()}
    assert actual_package.keys() == expected_package.keys(), (
        f"{archive}: package files differ; missing={sorted(expected_package.keys() - actual_package.keys())}, "
        f"unexpected={sorted(actual_package.keys() - expected_package.keys())}"
    )
    for name, expected in expected_package.items():
        assert actual_package[name] == expected, f"{archive}: stale packaged file {name}"

    for name, expected in (("README.md", source_root / "README.md"),
                           ("pyproject.toml", source_root / "pyproject.toml")):
        if is_sdist:
            assert name in files, f"{archive}: missing {name}"
        if name in files:
            assert files[name] == expected.read_bytes(), f"{archive}: stale packaged file {name}"

    expected_license = (source_root / "LICENSE").read_bytes()
    license_members = [
        data for name, data in files.items()
        if (name == "LICENSE" if is_sdist else name.endswith(".dist-info/licenses/LICENSE"))
    ]
    assert license_members == [expected_license], f"{archive}: missing or stale Apache license"

    for name, data in files.items():
        assert not name.startswith(("tests/", ".github/", "PILOT.md")), name
        assert (name.startswith((package_prefix, "src/jevcompass.egg-info/",
                                 "jevcompass/")) or
                name in ALLOWED_ROOT or ".dist-info/" in name), name
        assert not any(marker in data for marker in BLOCKED), name

    metadata_suffix = ".dist-info/METADATA" if not is_sdist else "PKG-INFO"
    metadata = [
        data for name, data in files.items()
        if (name == "PKG-INFO" if is_sdist else name.endswith(metadata_suffix))
    ]
    assert len(metadata) == 1, f"{archive}: expected one {metadata_suffix}"
    parsed = BytesParser().parsebytes(metadata[0])
    assert parsed.get("Name", "").lower() == "jevcompass", f"{archive}: invalid distribution name"
    assert parsed.get("License-Expression") == "Apache-2.0", f"{archive}: missing Apache SPDX metadata"
    assert "LICENSE" in (parsed.get_all("License-File") or []), f"{archive}: missing license file metadata"
    assert parsed.get("Version") == expected_version, (
        f"{archive}: metadata version {parsed.get('Version')!r} != pyproject version {expected_version!r}"
    )
    expected_readme = (source_root / "README.md").read_bytes()
    assert parsed.get_payload(decode=True) == expected_readme, (
        f"{archive}: stale README in distribution metadata"
    )


if __name__ == "__main__":
    archives = sorted(Path("dist").glob("jevcompass-*.whl")) + sorted(Path("dist").glob("jevcompass-*.tar.gz"))
    assert len(archives) == 2, "Expected one wheel and one sdist"
    for item in archives:
        check(item)
    print("Distribution contents and freshness checked")
