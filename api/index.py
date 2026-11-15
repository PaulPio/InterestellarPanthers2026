"""Vercel entry point: every /api/* request lands here (see vercel.json).

The simulator package and the cloud app are imported from the repository tree rather than
installed, because ccm_sim finds its scenarios, parameters and contracts relative to its source.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for path in (ROOT / "sim" / "src", ROOT / "cloud"):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from ccm_cloud.app import create_app  # noqa: E402  (needs the sys.path lines above)

app = create_app()
