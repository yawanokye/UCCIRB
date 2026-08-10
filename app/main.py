import fastapi
import starlette
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from fastapi.responses import JSONResponse
from starlette.middleware.sessions import SessionMiddleware

from .config import (APP_NAME, BASE_DIR, BOOTSTRAP_ADMIN_EMAIL, BOOTSTRAP_ADMIN_NAME,
                     BOOTSTRAP_ADMIN_PASSWORD, SECRET_KEY, SESSION_HTTPS_ONLY, STORAGE_DIR)
from .database import Base, SessionLocal, engine
from .routers import auth, portal
from .models import Role, User
from .services.auth import hash_password
from .services.routing import ensure_routing_units
from sqlalchemy import select

STORAGE_DIR.mkdir(parents=True, exist_ok=True)
Base.metadata.create_all(bind=engine)

with SessionLocal() as _routing_db:
    ensure_routing_units(_routing_db)

def bootstrap_superadmin():
    """Ensure the environment-defined bootstrap account is a usable System Administrator.

    BOOTSTRAP_ADMIN_* is intentionally authoritative while configured. This also repairs
    installations where the bootstrap email already existed as an applicant or another
    administrative role before the dedicated System Administrator portal was introduced.
    """
    if not BOOTSTRAP_ADMIN_EMAIL or not BOOTSTRAP_ADMIN_PASSWORD:
        return

    with SessionLocal() as db:
        existing = db.scalar(select(User).where(User.email == BOOTSTRAP_ADMIN_EMAIL))
        if existing:
            changed = False

            if existing.role != Role.SUPERADMIN.value:
                existing.role = Role.SUPERADMIN.value
                existing.college_id = None
                changed = True

            if not existing.active:
                existing.active = True
                changed = True

            if BOOTSTRAP_ADMIN_NAME and existing.full_name != BOOTSTRAP_ADMIN_NAME:
                existing.full_name = BOOTSTRAP_ADMIN_NAME
                changed = True

            # Keep the Render environment credential authoritative for the bootstrap
            # account so changing BOOTSTRAP_ADMIN_PASSWORD deliberately resets it.
            from .services.auth import verify_password
            if not verify_password(BOOTSTRAP_ADMIN_PASSWORD, existing.password_hash):
                existing.password_hash = hash_password(BOOTSTRAP_ADMIN_PASSWORD)
                changed = True

            if changed:
                db.commit()
            return

        db.add(User(
            email=BOOTSTRAP_ADMIN_EMAIL,
            full_name=BOOTSTRAP_ADMIN_NAME or 'System Administrator',
            password_hash=hash_password(BOOTSTRAP_ADMIN_PASSWORD),
            role=Role.SUPERADMIN.value,
            active=True,
            college_id=None,
        ))
        db.commit()


bootstrap_superadmin()

app = FastAPI(title=APP_NAME)
app.add_middleware(SessionMiddleware, secret_key=SECRET_KEY, https_only=SESSION_HTTPS_ONLY, same_site='lax')
app.mount('/static', StaticFiles(directory=BASE_DIR / 'app' / 'static'), name='static')
app.state.templates = Jinja2Templates(directory=BASE_DIR / 'app' / 'templates')
app.include_router(auth.router)
app.include_router(portal.router)


BUILD_ID = '2026-08-10-applicant-resources-v10'

@app.get('/healthz', include_in_schema=False)
def healthz():
    return JSONResponse({
        'status': 'ok',
        'build': BUILD_ID,
        'fastapi': fastapi.__version__,
        'starlette': starlette.__version__,
        'bootstrap_admin_configured': bool(BOOTSTRAP_ADMIN_EMAIL and BOOTSTRAP_ADMIN_PASSWORD),
        'review_assignment_workflow': 'secure-batch-v1',
        'applicant_resources': 'official-ucc-irb-v1',
        'public_applicant_guide': True,
    })
