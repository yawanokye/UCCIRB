from fastapi import Depends, Request
from sqlalchemy.orm import Session
from .database import get_db
from .services.auth import require_user


def get_current_user(request: Request, db: Session = Depends(get_db)):
    return require_user(request, db)
