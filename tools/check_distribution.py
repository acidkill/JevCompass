"""Fail a release if distributions include unexpected files or private markers."""

from pathlib import Path
import tarfile
import zipfile


BLOCKED = (b"/home/acidkill/", b"PERPLEXITY_API_KEY=", b"OPENROUTER_API_KEY=",
           b"private-client-secret", b"node_modules/", b"jev-use@")
ALLOWED_ROOT = {"README.md", "MANIFEST.in", "pyproject.toml", "PKG-INFO", "setup.cfg"}


def check(archive: Path) -> None:
    if archive.suffix == ".whl":
        with zipfile.ZipFile(archive) as bundle:
            members = [(name, bundle.read(name)) for name in bundle.namelist()]
    else:
        with tarfile.open(archive) as bundle:
            members = [(m.name.split("/", 1)[-1], bundle.extractfile(m).read())
                       for m in bundle if m.isfile()]
    for name, data in members:
        assert not name.startswith(("tests/", ".github/", "PILOT.md")), name
        assert (name.startswith(("src/jevcompass/", "src/jevcompass.egg-info/",
                                 "jevcompass/", "jevcompass-0.1.0.dist-info/")) or
                name in ALLOWED_ROOT or ".dist-info/" in name), name
        assert not any(marker in data for marker in BLOCKED), name


if __name__ == "__main__":
    archives = sorted(Path("dist").glob("jevcompass-*.whl")) + sorted(Path("dist").glob("jevcompass-*.tar.gz"))
    assert len(archives) == 2, "Expected one wheel and one sdist"
    for item in archives:
        check(item)
    print("Distribution contents checked")
