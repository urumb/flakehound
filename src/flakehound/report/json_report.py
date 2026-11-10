"""Machine-readable JSON report (versioned schema, see ``schema_version``)."""

from __future__ import annotations

import json
import os
from pathlib import Path

from flakehound.session import Report


def write_json(report: Report, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(report.to_json(), indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, path)
