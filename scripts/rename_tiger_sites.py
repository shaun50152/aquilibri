"""Strip demo suffix from Tiger site display names."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv

load_dotenv(ROOT / ".env")

from app.config import clear_settings_cache
from app.integrations.tigerdata import tiger_connection, tiger_env_configured


def main() -> None:
    clear_settings_cache()
    if not tiger_env_configured():
        raise SystemExit("Tiger/Postgres env not configured.")
    suffix = " (synthetic extended)"
    with tiger_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE sites SET name = REPLACE(name, %s, '') WHERE name LIKE %s",
                (suffix, f"%{suffix}%"),
            )
            updated = cur.rowcount
            cur.execute("SELECT site_id, name FROM sites ORDER BY site_id")
            rows = cur.fetchall()
        conn.commit()
    print(f"updated={updated}")
    for row in rows:
        print(row)


if __name__ == "__main__":
    main()
