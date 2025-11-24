"""Audit logging helper for recording change events."""

from datetime import datetime, timezone
from typing import Optional

from flask import session as flask_session

from models import AuditLog


def _current_username() -> str:
    """Return current user from Flask session, fallback to system."""
    try:
        return flask_session.get("username") or "system"
    except Exception:
        return "system"


def log_audit_event(
    db_session,
    entity_type: str,
    entity_id: int,
    action: str,
    field_name: Optional[str] = None,
    old_value: Optional[str] = None,
    new_value: Optional[str] = None,
    description: Optional[str] = None,
):
    """
    Add an audit entry to the given SQLAlchemy session (no commit).

    Caller is responsible for committing/closing the session.
    """
    if db_session is None:
        raise ValueError("db_session is required to log audit events")

    entry = AuditLog(
        timestamp=datetime.now(timezone.utc),
        user=_current_username(),
        entity_type=entity_type,
        entity_id=entity_id,
        action=action,
        field_name=field_name,
        old_value=str(old_value) if old_value is not None else None,
        new_value=str(new_value) if new_value is not None else None,
        description=description,
    )
    db_session.add(entry)
    return entry
