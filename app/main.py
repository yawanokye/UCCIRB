import fastapi
import starlette
from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from fastapi.responses import JSONResponse, RedirectResponse
from starlette.middleware.sessions import SessionMiddleware
from starlette.middleware.trustedhost import TrustedHostMiddleware

from .config import (ALLOWED_HOSTS, APP_NAME, BASE_DIR, BOOTSTRAP_ADMIN_EMAIL, BOOTSTRAP_ADMIN_NAME,
                     BOOTSTRAP_ADMIN_PASSWORD, ENABLE_API_DOCS, SECRET_KEY, SESSION_HTTPS_ONLY,
                     SESSION_MAX_AGE_SECONDS, STORAGE_DIR)
from .database import Base, SessionLocal, engine
from .routers import auth, portal
from .models import (AppStatus, ApplicationDocument, College, CollegeAccessRequest, EthicsApplication, ReviewReportDocument, ReviewReportFileBlob, ReviewerAssignment, Role, User)
from .services.auth import hash_password
from .services.routing import ensure_routing_units, is_scientific_committee_college
from .security import csrf_protect, request_rate_limit, security_headers
from .services.workflow import status_label
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

def backfill_review_report_blobs():
    """Copy any still-available legacy reviewer files into the durable database fallback."""
    import mimetypes
    from .services.storage import storage_path
    with SessionLocal() as db:
        docs = db.scalars(select(ReviewReportDocument)).all()
        changed = False
        for doc in docs:
            if db.get(ReviewReportFileBlob, doc.id):
                continue
            assignment = db.get(ReviewerAssignment, doc.assignment_id)
            if not assignment:
                continue
            path = storage_path(assignment.application_id, doc.stored_name)
            if not path.exists():
                continue
            try:
                content = path.read_bytes()
            except OSError:
                continue
            if not content:
                continue
            db.add(ReviewReportFileBlob(
                review_document_id=doc.id,
                content=content,
                media_type=mimetypes.guess_type(doc.original_name)[0] or 'application/octet-stream',
                size_bytes=len(content),
            ))
            changed = True
        if changed:
            db.commit()


backfill_review_report_blobs()

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
app.state.templates.env.globals['status_label'] = status_label
app.include_router(auth.router)
app.include_router(portal.router)

BUILD_ID = '2026-08-18-applicant-controls-prerequisites-v19'


@app.exception_handler(HTTPException)
async def portal_http_exception_handler(request: Request, exc: HTTPException):
    # Protected browser pages should never leave an expired user on a raw 401 JSON page.
    if exc.status_code == 401:
        expired_portal = getattr(request.state, 'expired_portal', None)
        portal = expired_portal or request.session.get('portal')
        path = request.url.path
        expired_q = '&expired=1' if expired_portal else ''
        if portal == 'system_admin' or path.startswith('/system-admin'):
            target = '/system-admin/login' + ('?expired=1' if expired_portal else '')
        elif portal == 'administrative' or path.startswith(('/secretariat', '/college', '/irb', '/review-batches', '/account')):
            target = '/login?portal=administrative' + expired_q
        else:
            target = '/login?portal=applicant' + expired_q
        return RedirectResponse(target, status_code=303)
    return JSONResponse({'detail': exc.detail}, status_code=exc.status_code, headers=getattr(exc, 'headers', None))


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
        'college_revision_queue_repair': 'upload-evidence-v2',
        'review_report_database_fallback': True,
        'review_report_inline_view': True,
        'review_material_left_alignment': True,
        'college_dashboard_organised': True,
        'security_hardening': 'v1',
        'csrf_protection': True,
        'applicant_auth_csrf_mode': 'no-origin-check-no-form-token',
        'origin_referer_enforcement': False,
        'applicant_login_lockout': 'account-based',
        'admin_login_lockout': 'account-and-ip',
        'login_rate_controls': True,
        'secure_upload_validation': True,
        'applicant_document_removal': True,
        'friendly_return_status_labels': True,
        'routing_details_applicant_hidden': True,
        'expired_session_redirects_to_login': True,
        'prerequisite_redirect_guidance': True,
        'reviewer_assessment_form': 'ucc-irb-research-ethics-reviewer-assessment-form.docx',
    })
