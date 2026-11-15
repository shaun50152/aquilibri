"""MongoDB Atlas integration — not wired in fixtures mode."""


class MongoJobStore:
    """Loads and saves jobs, schedule revisions, incidents, notifications."""

    def __init__(self, uri: str, database: str) -> None:
        self.uri = uri
        self.database = database

    def connect(self) -> None:
        raise NotImplementedError(
            "MongoDB connection is not implemented. Use DATA_MODE=fixtures for local demo."
        )

    def list_jobs(self, session_id: str) -> list:
        raise NotImplementedError("MongoDB list_jobs is not implemented.")

    def insert_job(self, job: dict) -> dict:
        raise NotImplementedError("MongoDB insert_job is not implemented.")