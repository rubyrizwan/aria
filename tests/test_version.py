import re
from pathlib import Path

from app.version import __version__


def test_project_and_changelog_versions_match():
    root = Path(__file__).resolve().parent.parent
    pyproject = (root / "pyproject.toml").read_text()
    project_version = re.search(r'^version = "([^"]+)"$', pyproject, re.MULTILINE)

    assert project_version
    assert project_version.group(1) == __version__
    assert f"## [{__version__}]" in (root / "CHANGELOG.md").read_text()
    readme = (root / "README.md").read_text()
    assert f"Versi stabil saat ini: **{__version__}**." in readme
    assert f"| `{__version__}` |" in readme
