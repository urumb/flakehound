"""Our GitHub workflow files must be valid YAML with the security settings we rely on.

A YAML mistake in release.yml once stopped a release before any job ran; this
catches it locally and in CI instead.
"""

from pathlib import Path

import pytest

yaml = pytest.importorskip("yaml")

WORKFLOWS = sorted((Path(__file__).resolve().parents[1] / ".github" / "workflows").glob("*.yml"))


@pytest.mark.parametrize("path", WORKFLOWS, ids=lambda p: p.name)
def test_workflow_is_valid_and_hardened(path):
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert isinstance(data, dict) and "jobs" in data
    # Least privilege by default.
    assert data.get("permissions") == {"contents": "read"}
    for name, job in data["jobs"].items():
        for step in job.get("steps", []):
            uses = step.get("uses")
            if uses:
                ref = uses.split("@", 1)[1]
                # Third-party actions are pinned to full commit SHAs, not tags.
                assert len(ref) == 40 and all(c in "0123456789abcdef" for c in ref), (
                    f"{path.name}:{name}: {uses} is not pinned to a commit SHA"
                )
            if uses and uses.startswith("actions/checkout@"):
                assert step.get("with", {}).get("persist-credentials") is False


def test_release_publishes_only_via_trusted_publishing():
    data = yaml.safe_load(
        (Path(__file__).resolve().parents[1] / ".github/workflows/release.yml").read_text(
            encoding="utf-8"
        )
    )
    publish = data["jobs"]["publish"]
    assert publish["environment"]["name"] == "pypi"
    assert publish["permissions"] == {"id-token": "write"}
    text = str(data)
    assert "password" not in text and "PYPI_TOKEN" not in text and "secrets." not in text
