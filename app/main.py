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
from .models import (AppStatus, ApplicationDocument, College, CollegeAccessRequest, EthicsApplication, Role, User)
from .services.auth import hash_password
from .services.routing import ensure_routing_units, is_scientific_committee_college
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


def migrate_v11_legacy_college_routing():
    """Upgrade V10 College states to the V11 automatic-forwarding rule.

    V10 used visible/locked/access-request states. V11 forwards a complete first submission
    directly to the College, so legacy records in those transitional states are activated and
    moved into the College receiving queue without deleting their history.
    """
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
                req.decision_note = 'Superseded by V11 automatic forwarding after Secretariat completeness screening.'
            changed = True
        if changed:
            db.commit()


migrate_v11_legacy_college_routing()

app = FastAPI(title=APP_NAME)
app.add_middleware(SessionMiddleware, secret_key=SECRET_KEY, https_only=SESSION_HTTPS_ONLY, same_site='lax')
app.mount('/static', StaticFiles(directory=BASE_DIR / 'app' / 'static'), name='static')
app.state.templates = Jinja2Templates(directory=BASE_DIR / 'app' / 'templates')
app.include_router(auth.router)
app.include_router(portal.router)


BUILD_ID = '2026-08-10-review-revision-privacy-v12'

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
        'submission_register': True,
        'college_direct_revision_workflow': True,
        'college_email_reviewer_assignment': True,
        'review_material_email_attachments': True,
        'applicant_review_report_download': True,
        'applicant_reviewer_privacy': True,
        'college_revision_reviewer_disposition': True,
    })
