from datetime import datetime
from sqlalchemy.orm import Session
from ..models import AppStatus, AuditLog, EthicsApplication, StatusHistory


def transition(db: Session, app: EthicsApplication, to_status: str, user_id: str, note: str | None = None):
    old = app.status
    app.status = to_status
    app.updated_at = datetime.utcnow()
    db.add(StatusHistory(application_id=app.id, from_status=old, to_status=to_status, changed_by=user_id, note=note))
    db.add(AuditLog(user_id=user_id, application_id=app.id, action='status_change', detail=f'{old} -> {to_status}' + (f' | {note}' if note else '')))
    return app


def audit(db: Session, user_id: str, action: str, application_id: str | None = None, detail: str | None = None):
    db.add(AuditLog(user_id=user_id, application_id=application_id, action=action, detail=detail))
