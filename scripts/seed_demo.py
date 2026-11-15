"""Print fixture summary for local verification (no database writes in fixtures mode)."""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.config import FIXTURES_DIR  # noqa: E402
from app.schemas.jobs import Job  # noqa: E402
from app.schemas.sites import SiteProfile  # noqa: E402


def main() -> None:
    jobs_raw = json.loads((FIXTURES_DIR / "jobs.json").read_text(encoding="utf-8"))
    sites_raw = json.loads((FIXTURES_DIR / "sites.json").read_text(encoding="utf-8"))
    jobs = [Job.model_validate(j) for j in jobs_raw["jobs"]]
    sites = [SiteProfile.model_validate(s) for s in sites_raw["sites"]]
    print(f"Validated {len(jobs)} jobs and {len(sites)} sites from fixtures.")
    print(f"Demo anchor: {jobs_raw.get('demo_clock_anchor')}")


if __name__ == "__main__":
    main()