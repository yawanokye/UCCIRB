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
    if not BOOTSTRAP_ADMIN_EMAIL or not BOOTSTRAP_ADMIN_PASSWORD:
        return
    with SessionLocal() as db:
        existing = db.scalar(select(User).where(User.email == BOOTSTRAP_ADMIN_EMAIL))
        if existing:
            return
        db.add(User(
            email=BOOTSTRAP_ADMIN_EMAIL,
            full_name=BOOTSTRAP_ADMIN_NAME or 'System Administrator',
            password_hash=hash_password(BOOTSTRAP_ADMIN_PASSWORD),
            role=Role.SUPERADMIN.value,
            active=True,
        ))
        db.commit()


bootstrap_superadmin()

app = FastAPI(title=APP_NAME)
app.add_middleware(SessionMiddleware, secret_key=SECRET_KEY, https_only=SESSION_HTTPS_ONLY, same_site='lax')
app.mount('/static', StaticFiles(directory=BASE_DIR / 'app' / 'static'), name='static')
app.state.templates = Jinja2Templates(directory=BASE_DIR / 'app' / 'templates')
app.include_router(auth.router)
app.include_router(portal.router)


BUILD_ID = '2026-08-09-phase2-routing-v6'

@app.get('/healthz', include_in_schema=False)
def healthz():
    return JSONResponse({
        'status': 'ok',
        'build': BUILD_ID,
        'fastapi': fastapi.__version__,
        'starlette': starlette.__version__,
    })
