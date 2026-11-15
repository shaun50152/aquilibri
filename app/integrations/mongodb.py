"""MongoDB Atlas job store (lazy pymongo connection)."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

from app.config import get_settings

_jobs_collection_name = "jobs"
_sessions_collection_name = "sessions"
_notifications_collection_name = "notifications"


class MongoJobStore:
    def __init__(self) -> None:
        self._client: Any = None
        self._db: Any = None

    def _ensure_connected(self) -> Any:
        if self._db is not None:
            return self._db
        uri = get_settings().mongodb_uri
        if not uri:
            raise RuntimeError("MONGODB_URI is required when DATA_MODE=live")
        try:
            from pymongo import MongoClient
        except ImportError as exc:
            raise RuntimeError("pymongo is required for DATA_MODE=live") from exc
        database = get_settings().mongodb_database
        self._client = MongoClient(uri, serverSelectionTimeoutMS=8000)
        self._db = self._client[database]
        return self._db

    def _jobs(self):
        return self._ensure_connected()[_jobs_collection_name]

    def _sessions(self):
        return self._ensure_connected()[_sessions_collection_name]

    def _notifications(self):
        return self._ensure_connected()[_notifications_collection_name]

    def ping(self) -> None:
        self._ensure_connected().command("ping")

    def list_jobs(self, session_id: str) -> list[dict[str, Any]]:
        cursor = self._jobs().find({"session_id": session_id}).sort("id", 1)
        return [{k: v for k, v in doc.items() if k != "_id"} for doc in cursor]

    def replace_job(self, session_id: str, job: dict[str, Any]) -> None:
        doc = dict(job)
        doc["session_id"] = session_id
        self._jobs().replace_one(
            {"session_id": session_id, "id": doc["id"]},
            doc,
            upsert=True,
        )

    def replace_session_jobs(self, session_id: str, jobs: list[dict[str, Any]]) -> None:
        coll = self._jobs()
        coll.delete_many({"session_id": session_id})
        if not jobs:
            return
        docs = []
        for job in jobs:
            doc = dict(job)
            doc["session_id"] = session_id
            docs.append(doc)
        coll.insert_many(docs)

    def get_revision(self, session_id: str) -> int:
        doc = self._sessions().find_one({"session_id": session_id})
        if not doc:
            return 0
        return int(doc.get("revision", 0))

    def set_revision(self, session_id: str, revision: int) -> None:
        self._sessions().update_one(
            {"session_id": session_id},
            {"$set": {"revision": int(revision), "updated_at": datetime.utcnow().isoformat() + "Z"}},
            upsert=True,
        )

    def next_revision(self, session_id: str) -> int:
        rev = self.get_revision(session_id) + 1
        self.set_revision(session_id, rev)
        return rev

    def get_session_meta(self, session_id: str) -> tuple[int, Optional[str], int]:
        doc = self._sessions().find_one({"session_id": session_id})
        if not doc:
            return 0, None, 0
        return (
            int(doc.get("revision", 0)),
            doc.get("current_time"),
            int(doc.get("execution_epoch", 0)),
        )

    def set_session_meta(
        self,
        session_id: str,
        *,
        revision: int,
        current_time: Optional[str] = None,
        execution_epoch: Optional[int] = None,
    ) -> None:
        payload: dict[str, Any] = {
            "session_id": session_id,
            "revision": int(revision),
            "updated_at": datetime.utcnow().isoformat() + "Z",
        }
        if current_time is not None:
            payload["current_time"] = current_time
        if execution_epoch is not None:
            payload["execution_epoch"] = int(execution_epoch)
        self._sessions().update_one(
            {"session_id": session_id},
            {"$set": payload},
            upsert=True,
        )

    def list_notifications(self, session_id: str) -> list[dict[str, Any]]:
        cursor = self._notifications().find({"session_id": session_id}).sort("completed_at", 1)
        return [{k: v for k, v in doc.items() if k != "_id"} for doc in cursor]

    def upsert_notification(self, record: dict[str, Any]) -> None:
        coll = self._notifications()
        coll.replace_one(
            {"dedupe_key": record["dedupe_key"]},
            record,
            upsert=True,
        )

    def delete_notifications(self, session_id: str) -> int:
        result = self._notifications().delete_many({"session_id": session_id})
        return int(result.deleted_count)

    def clear_session(self, session_id: str) -> None:
        """Drop all jobs and notifications for a demo session (live blank slate)."""
        self._jobs().delete_many({"session_id": session_id})
        self._notifications().delete_many({"session_id": session_id})


_store: Optional[MongoJobStore] = None


def get_mongo_job_store() -> MongoJobStore:
    global _store
    if _store is None:
        _store = MongoJobStore()
    return _store


def reset_mongo_job_store_for_tests() -> None:
    global _store
    _store = None