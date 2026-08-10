import fastapi
import starlette
from fastapi import Depends, FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from fastapi.responses import JSONResponse
from starlette.middleware.sessions import SessionMiddleware
from starlette.middleware.trustedhost import TrustedHostMiddleware

from .config import (ALLOWED_HOSTS, APP_NAME, BASE_DIR, BOOTSTRAP_ADMIN_EMAIL, BOOTSTRAP_ADMIN_NAME,
                     BOOTSTRAP_ADMIN_PASSWORD, ENABLE_API_DOCS, SECRET_KEY, SESSION_HTTPS_ONLY,
                     SESSION_MAX_AGE_SECONDS, STORAGE_DIR)
from .database import Base, SessionLocal, engine
from .routers import auth, portal
from .models import (AppStatus, ApplicationDocument, College, CollegeAccessRequest, EthicsApplication, Role, User)
from .services.auth import hash_password
from .services.routing import ensure_routing_units, is_scientific_committee_college
from .security import csrf_protect, request_rate_limit, security_headers
from sqlalchemy import select

STORAGE_DIR.mkdir(parents=True, exist_ok=True)
Base.metadata.create_all(bind=engine)

with SessionLocal() as _routing_db:
    ensure_routing_units(_routing_db)


def bootstrap_superadmin():
    """Ensure the environment-defined bootstrap account is a usable System Administrator."""
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


def migrate_v11_legacy_college_routing():
    """Upgrade legacy College states to the automatic-forwarding rule."""
    legacy_states = {
        AppStatus.ADMIN_COMPLETE.value,
        AppStatus.VISIBLE_TO_COLLEGE.value,
        AppStatus.COLLEGE_ACCESS_REQUESTED.value,
        AppStatus.COLLEGE_REVIEW_AUTHORISED.value,
    }
    with SessionLocal() as db:
        rows = db.scalars(select(EthicsApplication).where(EthicsApplication.status.in_(legacy_states))).all()
        changed = False
        for application in rows:
            college = db.get(College, application.college_id)
            if not is_scientific_committee_college(college):
                continue
            for doc in db.scalars(select(ApplicationDocument).where(ApplicationDocument.application_id == application.id)).all():
                doc.active_for_college = True
            application.status = AppStatus.FORWARDED_TO_COLLEGE.value
            for req in db.scalars(select(CollegeAccessRequest).where(
                CollegeAccessRequest.application_id == application.id, CollegeAccessRequest.status == 'pending'
            )).all():
                req.status = 'superseded'
                req.decision_note = 'Superseded by automatic forwarding after Secretariat completeness screening.'
            changed = True
        if changed:
            db.commit()


migrate_v11_legacy_college_routing()

api_docs = {} if ENABLE_API_DOCS else {'docs_url': None, 'redoc_url': None, 'openapi_url': None}
app = FastAPI(title=APP_NAME, dependencies=[Depends(csrf_protect)], **api_docs)

# Host and session controls. In production SESSION_HTTPS_ONLY should be true.
app.add_middleware(TrustedHostMiddleware, allowed_hosts=ALLOWED_HOSTS)
app.add_middleware(
    SessionMiddleware,
    secret_key=SECRET_KEY,
    session_cookie='ucc_irb_session',
    max_age=SESSION_MAX_AGE_SECONDS,
    https_only=SESSION_HTTPS_ONLY,
    same_site='lax',
)

# Application-layer rate limiting and response hardening.
app.middleware('http')(request_rate_limit)
app.middleware('http')(security_headers)

app.mount('/static', StaticFiles(directory=BASE_DIR / 'app' / 'static'), name='static')
app.state.templates = Jinja2Templates(directory=BASE_DIR / 'app' / 'templates')
app.include_router(auth.router)
app.include_router(portal.router)

BUILD_ID = '2026-08-10-revised-queue-layout-v14'


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
        'secretariat_document_checklist': True,
        'secretariat_inline_document_review': True,
        'secretariat_document_comments': True,
        'organised_screening_cockpit': True,
        'submission_register': True,
        'college_direct_revision_workflow': True,
        'college_email_reviewer_assignment': True,
        'review_material_email_attachments': True,
        'applicant_review_report_download': True,
        'applicant_reviewer_privacy': True,
        'college_revision_reviewer_disposition': True,
        'college_revision_queue_repair': True,
        'college_dashboard_organised': True,
        'security_hardening': 'v1',
        'csrf_protection': True,
        'login_rate_controls': True,
        'secure_upload_validation': True,
    })
