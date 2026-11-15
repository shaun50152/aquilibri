"""Tiger Data (Postgres) hourly site telemetry — not wired in fixtures mode."""


class TigerDataClient:
    """Reads hourly GPU, water, and energy telemetry."""

    def __init__(self, database_url: str) -> None:
        self.database_url = database_url

    def fetch_hourly_profiles(self, site_ids: list[str], start, end) -> list:
        raise NotImplementedError(
            "Tiger Data telemetry fetch is not implemented. Use fixture site profiles."
        )