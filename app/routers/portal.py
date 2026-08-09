from __future__ import annotations

from datetime import datetime, timedelta
import secrets
import string

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, RedirectResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session, joinedload

from ..config import CLEARANCE_VALIDITY_DAYS, REVIEW_DUE_DAYS
from ..database import get_db
from ..models import (
    AppStatus,
    ApplicationDocument,
    AuditLog,
    ClearanceCertificate,
    College,
    CollegeAccessRequest,
    CollegeDecision,
    EthicsApplication,
    IRBClassification,
    IRBDecision,
    IRBMeeting,
    IRBMeetingItem,
    PostApprovalRequest,
    ReviewAssignmentMeta,
    ReviewerAssignment,
    ReviewerDeclaration,
    Role,
    StatusHistory,
    User,
)
from ..services.auth import hash_password, require_roles, require_user
from ..services.certificate import certificate_path, generate_certificate_pdf
from ..services.storage import save_upload, storage_path
from ..services.workflow import audit, transition
from ..services.routing import (
    DIRECT_IRB_CODE,
    SCIENTIFIC_COMMITTEE_CODES,
    get_applicant_affiliations,
    get_direct_irb_affiliation,
    get_scientific_committee_colleges,
    is_direct_irb_affiliation,
    is_scientific_committee_college,
)

router = APIRouter()


def ctx(request, user=None, **kwargs):
    return {'request': request, 'user': user, **kwargs}


def get_app_or_404(db: Session, app_id: str):
    app = db.scalar(
        select(EthicsApplication)
        .options(joinedload(EthicsApplication.applicant), joinedload(EthicsApplication.college))
        .where(EthicsApplication.id == app_id)
    )
    if not app:
        raise HTTPException(404, 'Application not found')
    return app


def get_assignment_or_404(db: Session, assignment_id: str):
    assignment = db.scalar(
        select(ReviewerAssignment)
        .options(joinedload(ReviewerAssignment.application), joinedload(ReviewerAssignment.reviewer))
        .where(ReviewerAssignment.id == assignment_id)
    )
    if not assignment:
        raise HTTPException(404, 'Review assignment not found')
    return assignment


def assigned_to(db: Session, user: User, app: EthicsApplication) -> bool:
    return db.scalar(
        select(ReviewerAssignment.id).where(
            ReviewerAssignment.application_id == app.id,
            ReviewerAssignment.reviewer_id == user.id,
        )
    ) is not None


def can_view_metadata(db: Session, user: User, app: EthicsApplication) -> bool:
    if user.id == app.applicant_id:
        return True
    if user.role in {Role.SUPERADMIN.value, Role.IRB_SECRETARIAT.value, Role.IRB_CHAIR.value}:
        return True
    if user.role == Role.COLLEGE_ADMIN.value:
        return user.college_id == app.college_id and app.status != AppStatus.DRAFT.value
    if user.role in {Role.COLLEGE_REVIEWER.value, Role.IRB_REVIEWER.value}:
        return assigned_to(db, user, app)
    return False


def reviewer_has_clear_declaration(db: Session, user: User, app: EthicsApplication) -> bool:
    assignment_ids = db.scalars(
        select(ReviewerAssignment.id).where(
            ReviewerAssignment.application_id == app.id,
            ReviewerAssignment.reviewer_id == user.id,
            ReviewerAssignment.status.in_(['assigned', 'accepted', 'completed']),
        )
    ).all()
    if not assignment_ids:
        return False
    clear = db.scalar(
        select(ReviewerDeclaration.id).where(
            ReviewerDeclaration.assignment_id.in_(assignment_ids),
            ReviewerDeclaration.declaration == 'clear',
        )
    )
    return clear is not None


def can_view_documents(db: Session, user: User, app: EthicsApplication) -> bool:
    if user.id == app.applicant_id or user.role in {Role.SUPERADMIN.value, Role.IRB_SECRETARIAT.value, Role.IRB_CHAIR.value}:
        return True
    if user.role == Role.COLLEGE_ADMIN.value and user.college_id == app.college_id:
        granted = db.scalar(
            select(CollegeAccessRequest.id).where(
                CollegeAccessRequest.application_id == app.id,
                CollegeAccessRequest.status == 'granted',
            )
        )
        return granted is not None
    if user.role in {Role.COLLEGE_REVIEWER.value, Role.IRB_REVIEWER.value}:
        return assigned_to(db, user, app) and reviewer_has_clear_declaration(db, user, app)
    return False


def latest_classification(db: Session, app_id: str):
    return db.scalar(
        select(IRBClassification)
        .where(IRBClassification.application_id == app_id)
        .order_by(IRBClassification.classified_at.desc())
    )


def latest_certificate(db: Session, app_id: str):
    return db.scalar(
        select(ClearanceCertificate)
        .where(ClearanceCertificate.application_id == app_id)
        .order_by(ClearanceCertificate.issue_date.desc())
    )


def assignment_meta_map(db: Session, assignments):
    ids = [x.id for x in assignments]
    if not ids:
        return {}
    return {m.assignment_id: m for m in db.scalars(select(ReviewAssignmentMeta).where(ReviewAssignmentMeta.assignment_id.in_(ids))).all()}


def declaration_map(db: Session, assignments):
    ids = [x.id for x in assignments]
    if not ids:
        return {}
    return {d.assignment_id: d for d in db.scalars(select(ReviewerDeclaration).where(ReviewerDeclaration.assignment_id.in_(ids))).all()}


def reviewer_workload(db: Session, reviewers):
    now = datetime.utcnow()
    result = {}
    for reviewer in reviewers:
        assignments = db.scalars(select(ReviewerAssignment).where(ReviewerAssignment.reviewer_id == reviewer.id)).all()
        active = [a for a in assignments if a.status in {'assigned', 'accepted'}]
        completed = [a for a in assignments if a.status == 'completed']
        overdue = 0
        if active:
            meta = db.scalars(select(ReviewAssignmentMeta).where(ReviewAssignmentMeta.assignment_id.in_([a.id for a in active]))).all()
            overdue = sum(1 for m in meta if m.due_at and m.due_at < now)
        result[reviewer.id] = {'active': len(active), 'completed': len(completed), 'overdue': overdue}
    return result


def issue_clearance(db: Session, app: EthicsApplication, issuer: User, conditions: str | None = None):
    year = datetime.utcnow().year
    seq = (db.scalar(select(func.count(ClearanceCertificate.id)).where(func.extract('year', ClearanceCertificate.issue_date) == year)) or 0) + 1
    cert = ClearanceCertificate(
        application_id=app.id,
        certificate_no=f'UCC-IRB-EC-{year}-{seq:05d}',
        verification_token=secrets.token_urlsafe(32),
        issue_date=datetime.utcnow(),
        expiry_date=datetime.utcnow() + timedelta(days=CLEARANCE_VALIDITY_DAYS),
        status='valid',
        conditions=conditions or None,
        issued_by=issuer.id,
    )
    db.add(cert)
    db.flush()
    cert.pdf_stored_name = generate_certificate_pdf(cert, app)
    audit(db, issuer.id, 'ethical_clearance_certificate_issued', app.id, cert.certificate_no)
    return cert


@router.get('/')
def home(request: Request):
    return request.app.state.templates.TemplateResponse(request, 'home.html', {})


@router.get('/verify/{token}')
def verify_certificate(request: Request, token: str, db: Session = Depends(get_db)):
    cert = db.scalar(
        select(ClearanceCertificate)
        .options(joinedload(ClearanceCertificate.application).joinedload(EthicsApplication.applicant), joinedload(ClearanceCertificate.application).joinedload(EthicsApplication.college))
        .where(ClearanceCertificate.verification_token == token)
    )
    if not cert:
        return request.app.state.templates.TemplateResponse(request, 'certificate_verify.html', ctx(request, certificate=None, display_status='Not found'), status_code=404)
    display_status = cert.status
    if cert.status == 'valid' and cert.expiry_date < datetime.utcnow():
        display_status = 'expired'
    return request.app.state.templates.TemplateResponse(request, 'certificate_verify.html', ctx(request, certificate=cert, display_status=display_status))


@router.get('/dashboard')
def dashboard(request: Request, db: Session = Depends(get_db)):
    user = require_user(request, db)
    if user.role == Role.APPLICANT.value:
        apps = db.scalars(
            select(EthicsApplication)
            .where(EthicsApplication.applicant_id == user.id)
            .order_by(EthicsApplication.created_at.desc())
        ).all()
        return request.app.state.templates.TemplateResponse(request, 'dashboard_applicant.html', ctx(request, user, apps=apps))

    if user.role == Role.COLLEGE_ADMIN.value:
        apps = db.scalars(
            select(EthicsApplication)
            .options(joinedload(EthicsApplication.applicant))
            .where(EthicsApplication.college_id == user.college_id, EthicsApplication.status != AppStatus.DRAFT.value)
            .order_by(EthicsApplication.updated_at.desc())
        ).unique().all()
        counts = {s: sum(1 for a in apps if a.status == s) for s in set(a.status for a in apps)}
        return request.app.state.templates.TemplateResponse(request, 'dashboard_college.html', ctx(request, user, apps=apps, counts=counts))

    if user.role in {Role.COLLEGE_REVIEWER.value, Role.IRB_REVIEWER.value}:
        assignments = db.scalars(
            select(ReviewerAssignment)
            .options(joinedload(ReviewerAssignment.application))
            .where(ReviewerAssignment.reviewer_id == user.id)
            .order_by(ReviewerAssignment.assigned_at.desc())
        ).all()
        return request.app.state.templates.TemplateResponse(
            request,
            'dashboard_reviewer.html',
            ctx(request, user, assignments=assignments, meta=assignment_meta_map(db, assignments), declarations=declaration_map(db, assignments), now=datetime.utcnow()),
        )

    if user.role == Role.SUPERADMIN.value:
        users = db.scalars(select(User).options(joinedload(User.college)).order_by(User.created_at.desc())).all()
        colleges = get_scientific_committee_colleges(db)
        admin_users = [u for u in users if u.role != Role.APPLICANT.value]
        applicant_count = sum(1 for u in users if u.role == Role.APPLICANT.value)
        return request.app.state.templates.TemplateResponse(
            request,
            'dashboard_admin.html',
            ctx(request, user, users=admin_users, colleges=colleges, applicant_count=applicant_count, created_password=None, created_email=None, error=None),
        )

    if user.role in {Role.IRB_SECRETARIAT.value, Role.IRB_CHAIR.value}:
        apps = db.scalars(
            select(EthicsApplication)
            .options(joinedload(EthicsApplication.applicant), joinedload(EthicsApplication.college))
            .where(EthicsApplication.status != AppStatus.DRAFT.value)
            .order_by(EthicsApplication.updated_at.desc())
        ).unique().all()
        pending_access = db.scalars(
            select(CollegeAccessRequest)
            .options(joinedload(CollegeAccessRequest.application).joinedload(EthicsApplication.college))
            .where(CollegeAccessRequest.status == 'pending')
            .order_by(CollegeAccessRequest.requested_at)
        ).all()
        post_requests = db.scalars(
            select(PostApprovalRequest)
            .options(joinedload(PostApprovalRequest.application))
            .where(PostApprovalRequest.status == 'pending')
            .order_by(PostApprovalRequest.submitted_at)
        ).all()
        meetings_count = db.scalar(select(func.count(IRBMeeting.id))) or 0
        direct_apps = [a for a in apps if is_direct_irb_affiliation(a.college)]
        college_path_apps = [a for a in apps if is_scientific_committee_college(a.college)]
        return request.app.state.templates.TemplateResponse(
            request,
            'dashboard_secretariat.html',
            ctx(
                request, user, apps=apps, pending_access=pending_access, post_requests=post_requests,
                meetings_count=meetings_count, direct_apps=direct_apps, college_path_apps=college_path_apps,
            ),
        )

    raise HTTPException(403)


def generate_temporary_password(length: int = 14) -> str:
    alphabet = string.ascii_letters + string.digits + '!@#$%?'
    chars = [secrets.choice(string.ascii_uppercase), secrets.choice(string.ascii_lowercase), secrets.choice(string.digits), secrets.choice('!@#$%?')]
    chars.extend(secrets.choice(alphabet) for _ in range(length - len(chars)))
    secrets.SystemRandom().shuffle(chars)
    return ''.join(chars)


@router.post('/admin/users')
def create_administrative_user(
    request: Request,
    full_name: str = Form(...),
    email: str = Form(...),
    role: str = Form(...),
    college_id: str = Form(''),
    temporary_password: str = Form(''),
    db: Session = Depends(get_db),
):
    admin = require_user(request, db)
    require_roles(admin, Role.SUPERADMIN.value)
    allowed_roles = {
        Role.IRB_SECRETARIAT.value,
        Role.COLLEGE_ADMIN.value,
        Role.COLLEGE_REVIEWER.value,
        Role.IRB_REVIEWER.value,
        Role.IRB_CHAIR.value,
        Role.SUPERADMIN.value,
    }
    full_name = full_name.strip()
    email = email.lower().strip()
    users = db.scalars(select(User).options(joinedload(User.college)).order_by(User.created_at.desc())).all()
    colleges = get_scientific_committee_colleges(db)

    def render_error(message: str):
        admin_users = [u for u in users if u.role != Role.APPLICANT.value]
        applicant_count = sum(1 for u in users if u.role == Role.APPLICANT.value)
        return request.app.state.templates.TemplateResponse(
            request,
            'dashboard_admin.html',
            ctx(request, admin, users=admin_users, colleges=colleges, applicant_count=applicant_count, created_password=None, created_email=None, error=message),
            status_code=400,
        )

    if role not in allowed_roles:
        return render_error('Select a valid administrative role.')
    if len(full_name) < 3 or '@' not in email:
        return render_error("Enter the officer's full name and a valid email address.")
    if db.scalar(select(User).where(User.email == email)):
        return render_error('An account already exists with this email address.')
    if role in {Role.COLLEGE_ADMIN.value, Role.COLLEGE_REVIEWER.value}:
        selected_college = db.get(College, college_id) if college_id else None
        if not selected_college or not is_scientific_committee_college(selected_college):
            return render_error('College Scientific Committee users can only be assigned to one of the five authorised Scientific Committee Colleges.')
    else:
        college_id = ''

    generated = False
    password = temporary_password.strip()
    if not password:
        password = generate_temporary_password()
        generated = True
    elif len(password) < 8:
        return render_error('Temporary password must be at least 8 characters, or leave it blank to generate one automatically.')

    new_user = User(
        email=email,
        full_name=full_name,
        role=role,
        college_id=college_id or None,
        password_hash=hash_password(password),
        active=True,
    )
    db.add(new_user)
    audit(db, admin.id, 'administrative_account_created', None, f'{email} | role={role}')
    db.commit()

    users = db.scalars(select(User).options(joinedload(User.college)).order_by(User.created_at.desc())).all()
    admin_users = [u for u in users if u.role != Role.APPLICANT.value]
    applicant_count = sum(1 for u in users if u.role == Role.APPLICANT.value)
    return request.app.state.templates.TemplateResponse(
        request,
        'dashboard_admin.html',
        ctx(
            request,
            admin,
            users=admin_users,
            colleges=colleges,
            applicant_count=applicant_count,
            created_password=password if generated else '(password set by administrator)',
            created_email=email,
            error=None,
        ),
    )


@router.post('/admin/users/{user_id}/toggle')
def toggle_administrative_user(request: Request, user_id: str, db: Session = Depends(get_db)):
    admin = require_user(request, db)
    require_roles(admin, Role.SUPERADMIN.value)
    target = db.get(User, user_id)
    if not target or target.role == Role.APPLICANT.value:
        raise HTTPException(404, 'Administrative user not found')
    if target.id == admin.id:
        raise HTTPException(400, 'You cannot deactivate your own administrator account.')
    target.active = not target.active
    audit(db, admin.id, 'administrative_account_status_changed', None, f'{target.email} | active={target.active}')
    db.commit()
    return RedirectResponse('/dashboard', status_code=303)


@router.get('/applications/new')
def new_application_page(request: Request, db: Session = Depends(get_db)):
    user = require_user(request, db)
    require_roles(user, Role.APPLICANT.value)
    colleges = get_applicant_affiliations(db)
    return request.app.state.templates.TemplateResponse(request, 'application_new.html', ctx(request, user, colleges=colleges, direct_irb_code=DIRECT_IRB_CODE))


@router.post('/applications/new')
def new_application(
    request: Request,
    title: str = Form(...),
    college_id: str = Form(...),
    department: str = Form(''),
    programme: str = Form(''),
    applicant_type: str = Form('Student'),
    study_summary: str = Form(''),
    db: Session = Depends(get_db),
):
    user = require_user(request, db)
    require_roles(user, Role.APPLICANT.value)
    affiliation = db.get(College, college_id)
    if not affiliation or (not is_scientific_committee_college(affiliation) and not is_direct_irb_affiliation(affiliation)):
        raise HTTPException(400, 'Select a valid UCC affiliation pathway.')
    if is_direct_irb_affiliation(affiliation) and not department.strip():
        raise HTTPException(400, 'Enter the name of your UCC academic or administrative unit.')
    app = EthicsApplication(
        applicant_id=user.id,
        college_id=college_id,
        title=title.strip(),
        department=department.strip() or None,
        programme=programme.strip() or None,
        applicant_type=applicant_type,
        study_summary=study_summary.strip() or None,
    )
    db.add(app)
    db.flush()
    audit(db, user.id, 'application_created', app.id, app.title)
    db.commit()
    return RedirectResponse(f'/applications/{app.id}', status_code=303)


@router.get('/applications/{app_id}')
def application_detail(request: Request, app_id: str, db: Session = Depends(get_db)):
    user = require_user(request, db)
    app = get_app_or_404(db, app_id)
    if not can_view_metadata(db, user, app):
        raise HTTPException(403)

    can_docs = can_view_documents(db, user, app)
    documents = []
    if can_docs:
        documents = db.scalars(
            select(ApplicationDocument)
            .where(ApplicationDocument.application_id == app.id)
            .order_by(ApplicationDocument.document_type, ApplicationDocument.version.desc())
        ).all()
    access = db.scalars(
        select(CollegeAccessRequest)
        .where(CollegeAccessRequest.application_id == app.id)
        .order_by(CollegeAccessRequest.requested_at.desc())
    ).all()
    assignments = db.scalars(
        select(ReviewerAssignment)
        .options(joinedload(ReviewerAssignment.reviewer))
        .where(ReviewerAssignment.application_id == app.id)
        .order_by(ReviewerAssignment.assigned_at.desc())
    ).all()
    declarations = declaration_map(db, assignments)
    assignment_meta = assignment_meta_map(db, assignments)

    college_reviewers = []
    irb_reviewers = []
    if user.role == Role.COLLEGE_ADMIN.value and user.college_id == app.college_id:
        college_reviewers = db.scalars(
            select(User).where(
                User.college_id == app.college_id,
                User.role == Role.COLLEGE_REVIEWER.value,
                User.active == True,
            ).order_by(User.full_name)
        ).all()
    if user.role in {Role.IRB_SECRETARIAT.value, Role.IRB_CHAIR.value, Role.SUPERADMIN.value}:
        irb_reviewers = db.scalars(
            select(User).where(User.role == Role.IRB_REVIEWER.value, User.active == True).order_by(User.full_name)
        ).all()

    college_decisions = db.scalars(
        select(CollegeDecision).where(CollegeDecision.application_id == app.id).order_by(CollegeDecision.decided_at.desc())
    ).all()
    classifications = db.scalars(
        select(IRBClassification).where(IRBClassification.application_id == app.id).order_by(IRBClassification.classified_at.desc())
    ).all()
    irb_decisions = db.scalars(
        select(IRBDecision).where(IRBDecision.application_id == app.id).order_by(IRBDecision.decided_at.desc())
    ).all()
    certificates = db.scalars(
        select(ClearanceCertificate).where(ClearanceCertificate.application_id == app.id).order_by(ClearanceCertificate.issue_date.desc())
    ).all()
    post_requests = db.scalars(
        select(PostApprovalRequest).where(PostApprovalRequest.application_id == app.id).order_by(PostApprovalRequest.submitted_at.desc())
    ).all()
    status_history = db.scalars(
        select(StatusHistory).where(StatusHistory.application_id == app.id).order_by(StatusHistory.created_at.desc())
    ).all()
    meetings = db.scalars(select(IRBMeeting).where(IRBMeeting.status.in_(['scheduled', 'draft'])).order_by(IRBMeeting.meeting_date)).all()
    meeting_items = db.scalars(
        select(IRBMeetingItem)
        .options(joinedload(IRBMeetingItem.meeting))
        .where(IRBMeetingItem.application_id == app.id)
        .order_by(IRBMeetingItem.added_at.desc())
    ).all()

    return request.app.state.templates.TemplateResponse(
        request,
        'application_detail.html',
        ctx(
            request,
            user,
            app=app,
            documents=documents,
            access_requests=access,
            assignments=assignments,
            declarations=declarations,
            assignment_meta=assignment_meta,
            college_reviewers=college_reviewers,
            irb_reviewers=irb_reviewers,
            reviewer_workload=reviewer_workload(db, irb_reviewers or college_reviewers),
            college_decisions=college_decisions,
            classifications=classifications,
            irb_decisions=irb_decisions,
            certificates=certificates,
            post_requests=post_requests,
            status_history=status_history,
            meetings=meetings,
            meeting_items=meeting_items,
            can_docs=can_docs,
            now=datetime.utcnow(),
            default_due_days=REVIEW_DUE_DAYS,
            uses_college_scientific_review=is_scientific_committee_college(app.college),
            is_direct_irb=is_direct_irb_affiliation(app.college),
        ),
    )


@router.post('/applications/{app_id}/documents')
def upload_document(
    request: Request,
    app_id: str,
    document_type: str = Form(...),
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
):
    user = require_user(request, db)
    app = get_app_or_404(db, app_id)
    if user.id != app.applicant_id:
        raise HTTPException(403)
    if app.status not in {AppStatus.DRAFT.value, AppStatus.RETURNED_ADMIN.value, AppStatus.COLLEGE_REVISION.value, AppStatus.IRB_REVISION.value}:
        raise HTTPException(400, 'Application is locked for editing')
    stored, original = save_upload(file, app.id)
    maxver = db.scalar(
        select(func.max(ApplicationDocument.version)).where(
            ApplicationDocument.application_id == app.id,
            ApplicationDocument.document_type == document_type,
        )
    ) or 0
    doc = ApplicationDocument(
        application_id=app.id,
        document_type=document_type,
        original_name=original,
        stored_name=stored,
        version=maxver + 1,
        uploaded_by=user.id,
    )
    db.add(doc)
    audit(db, user.id, 'document_uploaded', app.id, f'{document_type} v{doc.version}: {original}')
    db.commit()
    return RedirectResponse(f'/applications/{app.id}', status_code=303)


@router.post('/applications/{app_id}/submit')
def submit_application(request: Request, app_id: str, db: Session = Depends(get_db)):
    user = require_user(request, db)
    app = get_app_or_404(db, app_id)
    if user.id != app.applicant_id:
        raise HTTPException(403)
    if app.status not in {AppStatus.DRAFT.value, AppStatus.RETURNED_ADMIN.value}:
        raise HTTPException(400, 'Application cannot be submitted from its current state')
    docs = db.scalars(select(ApplicationDocument).where(ApplicationDocument.application_id == app.id)).all()
    required = {'Research Protocol', 'Data Collection Instrument', 'Supervisor Approval'} if app.applicant_type.lower() == 'student' else {'Research Protocol', 'Data Collection Instrument'}
    present = {d.document_type for d in docs}
    missing = required - present
    if missing:
        raise HTTPException(400, f'Missing required document(s): {", ".join(sorted(missing))}')
    if not app.reference_no:
        seq = (db.scalar(select(func.count(EthicsApplication.id)).where(EthicsApplication.submitted_at.is_not(None))) or 0) + 1
        app.reference_no = f'UCC-IRB-{datetime.utcnow().year}-{seq:05d}'
    app.submitted_at = datetime.utcnow()
    transition(db, app, AppStatus.SUBMITTED.value, user.id, 'Submitted centrally to IRB Secretariat')
    db.commit()
    return RedirectResponse(f'/applications/{app.id}', status_code=303)


@router.post('/applications/{app_id}/submit-revision')
def submit_revision(request: Request, app_id: str, db: Session = Depends(get_db)):
    user = require_user(request, db)
    app = get_app_or_404(db, app_id)
    if user.id != app.applicant_id:
        raise HTTPException(403)
    if app.status == AppStatus.COLLEGE_REVISION.value:
        required_type = 'Response to College Review'
        next_status = AppStatus.COLLEGE_REVISED.value
        note = 'Applicant submitted revised documents to College Scientific Committee'
    elif app.status == AppStatus.IRB_REVISION.value:
        required_type = 'Response to IRB Review'
        next_status = AppStatus.IRB_REVISED.value
        note = 'Applicant submitted revised documents for IRB review'
    else:
        raise HTTPException(400, 'No revision is currently requested')
    present = db.scalar(
        select(ApplicationDocument.id).where(
            ApplicationDocument.application_id == app.id,
            ApplicationDocument.document_type == required_type,
        )
    )
    if not present:
        raise HTTPException(400, f'Upload {required_type} before submitting the revision.')
    transition(db, app, next_status, user.id, note)
    db.commit()
    return RedirectResponse(f'/applications/{app.id}', status_code=303)


@router.post('/secretariat/{app_id}/screen')
def secretariat_screen(
    request: Request,
    app_id: str,
    outcome: str = Form(...),
    note: str = Form(''),
    db: Session = Depends(get_db),
):
    user = require_user(request, db)
    require_roles(user, Role.IRB_SECRETARIAT.value, Role.IRB_CHAIR.value, Role.SUPERADMIN.value)
    app = get_app_or_404(db, app_id)
    if app.status not in {AppStatus.SUBMITTED.value, AppStatus.SECRETARIAT_SCREENING.value}:
        raise HTTPException(400, 'Application is not awaiting Secretariat screening.')
    if outcome == 'complete':
        transition(db, app, AppStatus.ADMIN_COMPLETE.value, user.id, note or 'Administrative screening complete')
        if is_scientific_committee_college(app.college):
            transition(db, app, AppStatus.VISIBLE_TO_COLLEGE.value, user.id, 'Metadata visible to the relevant College Scientific Committee; documents remain locked pending Secretariat authorisation')
        else:
            transition(db, app, AppStatus.DIRECT_IRB.value, user.id, 'Affiliation has no College Scientific Committee. Application retained by the IRB Secretariat for direct IRB review classification')
    elif outcome == 'return':
        app.secretariat_note = note
        transition(db, app, AppStatus.RETURNED_ADMIN.value, user.id, note or 'Returned for administrative correction')
    else:
        raise HTTPException(400, 'Unknown screening outcome')
    db.commit()
    return RedirectResponse(f'/applications/{app.id}', status_code=303)


@router.post('/college/{app_id}/request-access')
def request_access(request: Request, app_id: str, note: str = Form(''), db: Session = Depends(get_db)):
    user = require_user(request, db)
    require_roles(user, Role.COLLEGE_ADMIN.value)
    app = get_app_or_404(db, app_id)
    if user.college_id != app.college_id or not is_scientific_committee_college(app.college):
        raise HTTPException(403)
    if app.status != AppStatus.VISIBLE_TO_COLLEGE.value:
        raise HTTPException(400, 'Application is not available for access request')
    existing = db.scalar(
        select(CollegeAccessRequest.id).where(
            CollegeAccessRequest.application_id == app.id,
            CollegeAccessRequest.status == 'pending',
        )
    )
    if existing:
        raise HTTPException(400, 'A review access request is already pending.')
    req = CollegeAccessRequest(application_id=app.id, requested_by=user.id, request_note=note or None)
    db.add(req)
    transition(db, app, AppStatus.COLLEGE_ACCESS_REQUESTED.value, user.id, 'College requested permission to commence scientific review')
    db.commit()
    return RedirectResponse(f'/applications/{app.id}', status_code=303)


@router.post('/secretariat/access/{request_id}/decide')
def decide_access(
    request: Request,
    request_id: str,
    decision: str = Form(...),
    note: str = Form(''),
    db: Session = Depends(get_db),
):
    user = require_user(request, db)
    require_roles(user, Role.IRB_SECRETARIAT.value, Role.IRB_CHAIR.value, Role.SUPERADMIN.value)
    req = db.get(CollegeAccessRequest, request_id)
    if not req or req.status != 'pending':
        raise HTTPException(404, 'Pending access request not found')
    app = get_app_or_404(db, req.application_id)
    req.decided_by = user.id
    req.decided_at = datetime.utcnow()
    req.decision_note = note or None
    if decision == 'grant':
        req.status = 'granted'
        for doc in db.scalars(select(ApplicationDocument).where(ApplicationDocument.application_id == app.id)).all():
            doc.active_for_college = True
        transition(db, app, AppStatus.COLLEGE_REVIEW_AUTHORISED.value, user.id, note or 'College document access activated')
        transition(db, app, AppStatus.AWAITING_COLLEGE_REVIEWER.value, user.id, 'Awaiting College reviewer assignment')
    elif decision == 'withhold':
        req.status = 'withheld'
        transition(db, app, AppStatus.VISIBLE_TO_COLLEGE.value, user.id, note or 'College access withheld')
    else:
        raise HTTPException(400, 'Unknown decision')
    audit(db, user.id, 'college_access_decision', app.id, f'{decision}: {note}')
    db.commit()
    return RedirectResponse(f'/applications/{app.id}', status_code=303)


@router.post('/college/{app_id}/assign-reviewer')
def assign_college_reviewer(
    request: Request,
    app_id: str,
    reviewer_id: str = Form(...),
    assignment_type: str = Form('primary'),
    due_days: int = Form(REVIEW_DUE_DAYS),
    db: Session = Depends(get_db),
):
    user = require_user(request, db)
    require_roles(user, Role.COLLEGE_ADMIN.value)
    app = get_app_or_404(db, app_id)
    allowed = {AppStatus.AWAITING_COLLEGE_REVIEWER.value, AppStatus.COLLEGE_REVIEW.value, AppStatus.COLLEGE_REVISED.value}
    if user.college_id != app.college_id or not is_scientific_committee_college(app.college) or app.status not in allowed:
        raise HTTPException(403)
    reviewer = db.get(User, reviewer_id)
    if not reviewer or reviewer.role != Role.COLLEGE_REVIEWER.value or reviewer.college_id != app.college_id or not reviewer.active:
        raise HTTPException(400, 'Invalid College reviewer')
    duplicate = db.scalar(
        select(ReviewerAssignment.id).where(
            ReviewerAssignment.application_id == app.id,
            ReviewerAssignment.reviewer_id == reviewer.id,
            ReviewerAssignment.level == 'college',
            ReviewerAssignment.status.in_(['assigned', 'accepted']),
        )
    )
    if duplicate:
        raise HTTPException(400, 'This reviewer already has an active assignment for the application.')
    assignment = ReviewerAssignment(
        application_id=app.id,
        reviewer_id=reviewer.id,
        level='college',
        assignment_type=assignment_type,
        assigned_by=user.id,
    )
    db.add(assignment)
    db.flush()
    db.add(ReviewAssignmentMeta(assignment_id=assignment.id, due_at=datetime.utcnow() + timedelta(days=max(1, min(due_days, 90)))))
    transition(db, app, AppStatus.COLLEGE_REVIEW.value, user.id, f'Assigned College scientific review to {reviewer.full_name}')
    db.commit()
    return RedirectResponse(f'/applications/{app.id}', status_code=303)


@router.post('/review/{assignment_id}/declaration')
def submit_reviewer_declaration(
    request: Request,
    assignment_id: str,
    declaration: str = Form(...),
    note: str = Form(''),
    db: Session = Depends(get_db),
):
    user = require_user(request, db)
    assignment = get_assignment_or_404(db, assignment_id)
    if assignment.reviewer_id != user.id:
        raise HTTPException(403)
    if db.scalar(select(ReviewerDeclaration.id).where(ReviewerDeclaration.assignment_id == assignment.id)):
        raise HTTPException(400, 'Conflict-of-interest declaration already submitted.')
    if declaration not in {'clear', 'conflict'}:
        raise HTTPException(400, 'Choose a valid declaration.')
    db.add(ReviewerDeclaration(assignment_id=assignment.id, declaration=declaration, note=note or None))
    app = get_app_or_404(db, assignment.application_id)
    if declaration == 'clear':
        assignment.status = 'accepted'
        audit(db, user.id, 'reviewer_conflict_declaration_clear', app.id, note or 'No conflict declared')
    else:
        assignment.status = 'declined'
        audit(db, user.id, 'reviewer_conflict_declared', app.id, note or 'Potential conflict declared')
        db.flush()
        # Return to assignment queue only when no other active reviewer remains.
        remaining = db.scalar(
            select(func.count(ReviewerAssignment.id)).where(
                ReviewerAssignment.application_id == app.id,
                ReviewerAssignment.level == assignment.level,
                ReviewerAssignment.status.in_(['assigned', 'accepted']),
            )
        ) or 0
        if remaining == 0:
            to_status = AppStatus.AWAITING_COLLEGE_REVIEWER.value if assignment.level == 'college' else AppStatus.AWAITING_IRB_REVIEWER.value
            transition(db, app, to_status, user.id, 'Reviewer declared a conflict; reassignment required')
    db.commit()
    return RedirectResponse(f'/applications/{app.id}', status_code=303)


@router.post('/review/{assignment_id}/submit')
def submit_review(
    request: Request,
    assignment_id: str,
    recommendation: str = Form(...),
    comments: str = Form(...),
    db: Session = Depends(get_db),
):
    user = require_user(request, db)
    assignment = get_assignment_or_404(db, assignment_id)
    if assignment.reviewer_id != user.id or assignment.status not in {'accepted', 'assigned'}:
        raise HTTPException(403)
    declaration = db.scalar(select(ReviewerDeclaration).where(ReviewerDeclaration.assignment_id == assignment.id))
    if not declaration or declaration.declaration != 'clear':
        raise HTTPException(400, 'Submit a no-conflict declaration before completing the review.')
    assignment.recommendation = recommendation
    assignment.comments = comments
    assignment.status = 'completed'
    assignment.completed_at = datetime.utcnow()
    app = get_app_or_404(db, assignment.application_id)
    # Session autoflush is disabled, so flush the completed assignment before counting
    # active reviewers in the current review round.
    db.flush()

    pending = db.scalar(
        select(func.count(ReviewerAssignment.id)).where(
            ReviewerAssignment.application_id == app.id,
            ReviewerAssignment.level == assignment.level,
            ReviewerAssignment.status.in_(['assigned', 'accepted']),
        )
    ) or 0

    if pending == 0:
        if assignment.level == 'college':
            transition(db, app, AppStatus.AWAITING_COLLEGE_DECISION.value, user.id, 'College scientific review round completed')
        else:
            # Only use recommendations from the current active IRB review round.
            milestone = db.scalar(
                select(StatusHistory)
                .where(
                    StatusHistory.application_id == app.id,
                    StatusHistory.to_status.in_([AppStatus.AWAITING_IRB_REVIEWER.value, AppStatus.IRB_REVISED.value]),
                )
                .order_by(StatusHistory.created_at.desc())
            )
            round_start = milestone.created_at if milestone else datetime.min
            current = db.scalars(
                select(ReviewerAssignment).where(
                    ReviewerAssignment.application_id == app.id,
                    ReviewerAssignment.level == 'irb',
                    ReviewerAssignment.assigned_at >= round_start,
                    ReviewerAssignment.status == 'completed',
                )
            ).all()
            recs = {x.recommendation for x in current}
            classification = latest_classification(db, app.id)
            if 'minor_revision' in recs or 'major_revision' in recs:
                transition(db, app, AppStatus.IRB_REVISION.value, user.id, 'IRB ethical review requires applicant revision')
            elif (classification and classification.classification == 'full_board') or 'full_board' in recs:
                transition(db, app, AppStatus.FULL_BOARD.value, user.id, 'Application requires Full Board consideration')
            else:
                transition(db, app, AppStatus.AWAITING_FINAL_DECISION.value, user.id, 'IRB review completed; awaiting authorised final decision')

    audit(db, user.id, 'review_submitted', app.id, f'{assignment.level}: {recommendation}')
    db.commit()
    return RedirectResponse(f'/applications/{app.id}', status_code=303)


@router.post('/college/{app_id}/decision')
def college_decision(
    request: Request,
    app_id: str,
    decision: str = Form(...),
    comments: str = Form(''),
    meeting_reference: str = Form(''),
    db: Session = Depends(get_db),
):
    user = require_user(request, db)
    require_roles(user, Role.COLLEGE_ADMIN.value)
    app = get_app_or_404(db, app_id)
    if user.college_id != app.college_id or not is_scientific_committee_college(app.college) or app.status != AppStatus.AWAITING_COLLEGE_DECISION.value:
        raise HTTPException(403)
    allowed = {'recommend', 'minor_revision', 'major_revision', 'specialist_review', 'not_recommended'}
    if decision not in allowed:
        raise HTTPException(400, 'Unknown College decision')
    db.add(
        CollegeDecision(
            application_id=app.id,
            decision=decision,
            comments=comments or None,
            meeting_reference=meeting_reference or None,
            decided_by=user.id,
        )
    )
    if decision == 'recommend':
        transition(db, app, AppStatus.SCIENTIFICALLY_RECOMMENDED.value, user.id, comments or 'Scientifically recommended by College Scientific Committee')
        transition(db, app, AppStatus.RETURNED_TO_IRB.value, user.id, 'College scientific review completed and application returned to IRB Secretariat')
    elif decision in {'minor_revision', 'major_revision'}:
        transition(db, app, AppStatus.COLLEGE_REVISION.value, user.id, comments or decision.replace('_', ' ').title())
    elif decision == 'specialist_review':
        transition(db, app, AppStatus.AWAITING_COLLEGE_REVIEWER.value, user.id, comments or 'Specialist scientific review required')
    else:
        transition(db, app, AppStatus.NOT_SCIENTIFICALLY_RECOMMENDED.value, user.id, comments or 'Not scientifically recommended')
    audit(db, user.id, 'college_scientific_committee_decision', app.id, decision)
    db.commit()
    return RedirectResponse(f'/applications/{app.id}', status_code=303)


@router.post('/irb/{app_id}/classify')
def classify_irb_review(
    request: Request,
    app_id: str,
    classification: str = Form(...),
    rationale: str = Form(''),
    db: Session = Depends(get_db),
):
    user = require_user(request, db)
    require_roles(user, Role.IRB_SECRETARIAT.value, Role.IRB_CHAIR.value, Role.SUPERADMIN.value)
    app = get_app_or_404(db, app_id)
    if app.status not in {AppStatus.RETURNED_TO_IRB.value, AppStatus.DIRECT_IRB.value, AppStatus.IRB_CLASSIFICATION.value}:
        raise HTTPException(400, 'Application is not ready for IRB classification.')
    if classification not in {'exempt', 'expedited', 'full_board'}:
        raise HTTPException(400, 'Select a valid review classification.')
    db.add(IRBClassification(application_id=app.id, classification=classification, rationale=rationale or None, classified_by=user.id))
    transition(db, app, AppStatus.IRB_CLASSIFICATION.value, user.id, f'Classified as {classification.replace("_", " ").title()}')
    if classification == 'exempt':
        transition(db, app, AppStatus.AWAITING_FINAL_DECISION.value, user.id, 'Exempt determination awaiting authorised IRB decision')
    else:
        transition(db, app, AppStatus.AWAITING_IRB_REVIEWER.value, user.id, 'Awaiting IRB ethical reviewer assignment')
    db.commit()
    return RedirectResponse(f'/applications/{app.id}', status_code=303)


@router.post('/irb/{app_id}/assign-reviewer')
def assign_irb_reviewer(
    request: Request,
    app_id: str,
    reviewer_id: str = Form(...),
    assignment_type: str = Form('primary'),
    due_days: int = Form(REVIEW_DUE_DAYS),
    db: Session = Depends(get_db),
):
    user = require_user(request, db)
    require_roles(user, Role.IRB_SECRETARIAT.value, Role.IRB_CHAIR.value, Role.SUPERADMIN.value)
    app = get_app_or_404(db, app_id)
    if app.status not in {AppStatus.AWAITING_IRB_REVIEWER.value, AppStatus.IRB_REVIEW.value, AppStatus.IRB_REVISED.value}:
        raise HTTPException(400, 'Application is not awaiting IRB reviewer assignment.')
    reviewer = db.get(User, reviewer_id)
    if not reviewer or reviewer.role != Role.IRB_REVIEWER.value or not reviewer.active:
        raise HTTPException(400, 'Invalid IRB reviewer')
    duplicate = db.scalar(
        select(ReviewerAssignment.id).where(
            ReviewerAssignment.application_id == app.id,
            ReviewerAssignment.reviewer_id == reviewer.id,
            ReviewerAssignment.level == 'irb',
            ReviewerAssignment.status.in_(['assigned', 'accepted']),
        )
    )
    if duplicate:
        raise HTTPException(400, 'This reviewer already has an active IRB assignment for the application.')
    assignment = ReviewerAssignment(
        application_id=app.id,
        reviewer_id=reviewer.id,
        level='irb',
        assignment_type=assignment_type,
        assigned_by=user.id,
    )
    db.add(assignment)
    db.flush()
    db.add(ReviewAssignmentMeta(assignment_id=assignment.id, due_at=datetime.utcnow() + timedelta(days=max(1, min(due_days, 90)))))
    transition(db, app, AppStatus.IRB_REVIEW.value, user.id, f'Assigned IRB ethical review to {reviewer.full_name}')
    db.commit()
    return RedirectResponse(f'/applications/{app.id}', status_code=303)


@router.get('/irb/meetings')
def meetings_page(request: Request, db: Session = Depends(get_db)):
    user = require_user(request, db)
    require_roles(user, Role.IRB_SECRETARIAT.value, Role.IRB_CHAIR.value, Role.SUPERADMIN.value)
    meetings = db.scalars(select(IRBMeeting).order_by(IRBMeeting.meeting_date.desc())).all()
    full_board_apps = db.scalars(
        select(EthicsApplication)
        .options(joinedload(EthicsApplication.applicant))
        .where(EthicsApplication.status == AppStatus.FULL_BOARD.value)
        .order_by(EthicsApplication.updated_at)
    ).unique().all()
    return request.app.state.templates.TemplateResponse(request, 'meetings.html', ctx(request, user, meetings=meetings, full_board_apps=full_board_apps))


@router.post('/irb/meetings')
def create_meeting(
    request: Request,
    meeting_no: str = Form(...),
    meeting_date: str = Form(...),
    venue: str = Form(''),
    meeting_link: str = Form(''),
    db: Session = Depends(get_db),
):
    user = require_user(request, db)
    require_roles(user, Role.IRB_SECRETARIAT.value, Role.IRB_CHAIR.value, Role.SUPERADMIN.value)
    try:
        when = datetime.fromisoformat(meeting_date)
    except ValueError:
        raise HTTPException(400, 'Enter a valid meeting date and time.')
    if db.scalar(select(IRBMeeting.id).where(IRBMeeting.meeting_no == meeting_no.strip())):
        raise HTTPException(400, 'Meeting number already exists.')
    meeting = IRBMeeting(
        meeting_no=meeting_no.strip(),
        meeting_date=when,
        venue=venue.strip() or None,
        meeting_link=meeting_link.strip() or None,
        created_by=user.id,
    )
    db.add(meeting)
    db.commit()
    return RedirectResponse(f'/irb/meetings/{meeting.id}', status_code=303)


@router.get('/irb/meetings/{meeting_id}')
def meeting_detail(request: Request, meeting_id: str, db: Session = Depends(get_db)):
    user = require_user(request, db)
    require_roles(user, Role.IRB_SECRETARIAT.value, Role.IRB_CHAIR.value, Role.SUPERADMIN.value)
    meeting = db.get(IRBMeeting, meeting_id)
    if not meeting:
        raise HTTPException(404, 'IRB meeting not found')
    items = db.scalars(
        select(IRBMeetingItem)
        .options(joinedload(IRBMeetingItem.application).joinedload(EthicsApplication.applicant))
        .where(IRBMeetingItem.meeting_id == meeting.id)
        .order_by(IRBMeetingItem.agenda_no, IRBMeetingItem.added_at)
    ).all()
    full_board_apps = db.scalars(
        select(EthicsApplication)
        .where(EthicsApplication.status == AppStatus.FULL_BOARD.value)
        .order_by(EthicsApplication.updated_at)
    ).all()
    return request.app.state.templates.TemplateResponse(request, 'meeting_detail.html', ctx(request, user, meeting=meeting, items=items, full_board_apps=full_board_apps))


@router.post('/irb/meetings/{meeting_id}/add')
def add_meeting_item(
    request: Request,
    meeting_id: str,
    application_id: str = Form(...),
    agenda_no: str = Form(''),
    db: Session = Depends(get_db),
):
    user = require_user(request, db)
    require_roles(user, Role.IRB_SECRETARIAT.value, Role.IRB_CHAIR.value, Role.SUPERADMIN.value)
    meeting = db.get(IRBMeeting, meeting_id)
    app = get_app_or_404(db, application_id)
    if not meeting:
        raise HTTPException(404, 'IRB meeting not found')
    if app.status != AppStatus.FULL_BOARD.value:
        raise HTTPException(400, 'Only Full Board applications can be scheduled here.')
    if db.scalar(select(IRBMeetingItem.id).where(IRBMeetingItem.meeting_id == meeting.id, IRBMeetingItem.application_id == app.id)):
        raise HTTPException(400, 'Application is already on this meeting agenda.')
    db.add(IRBMeetingItem(meeting_id=meeting.id, application_id=app.id, agenda_no=agenda_no or None, added_by=user.id))
    audit(db, user.id, 'application_scheduled_for_irb_meeting', app.id, meeting.meeting_no)
    db.commit()
    return RedirectResponse(f'/irb/meetings/{meeting.id}', status_code=303)


@router.post('/irb/{app_id}/decision')
def final_irb_decision(
    request: Request,
    app_id: str,
    decision: str = Form(...),
    conditions: str = Form(''),
    meeting_id: str = Form(''),
    db: Session = Depends(get_db),
):
    user = require_user(request, db)
    require_roles(user, Role.IRB_CHAIR.value, Role.SUPERADMIN.value)
    app = get_app_or_404(db, app_id)
    if app.status not in {AppStatus.AWAITING_FINAL_DECISION.value, AppStatus.FULL_BOARD.value, AppStatus.DEFERRED.value}:
        raise HTTPException(400, 'Application is not awaiting a final IRB decision.')
    if decision not in {'approved', 'approved_conditions', 'deferred', 'rejected'}:
        raise HTTPException(400, 'Select a valid IRB decision.')
    if decision == 'approved_conditions' and not conditions.strip():
        raise HTTPException(400, 'Enter the conditions attached to approval.')
    meeting = db.get(IRBMeeting, meeting_id) if meeting_id else None
    if app.status == AppStatus.FULL_BOARD.value and not meeting:
        raise HTTPException(400, 'Select the IRB meeting at which the Full Board decision was made.')
    if meeting and not db.scalar(select(IRBMeetingItem.id).where(IRBMeetingItem.meeting_id == meeting.id, IRBMeetingItem.application_id == app.id)):
        raise HTTPException(400, 'This application is not listed on the selected IRB meeting agenda.')

    db.add(
        IRBDecision(
            application_id=app.id,
            decision=decision,
            conditions=conditions.strip() or None,
            meeting_id=meeting.id if meeting else None,
            decided_by=user.id,
        )
    )
    if decision in {'approved', 'approved_conditions'}:
        transition(db, app, AppStatus.APPROVED_CONDITIONS.value if decision == 'approved_conditions' else AppStatus.APPROVED.value, user.id, conditions or 'IRB approval granted')
        issue_clearance(db, app, user, conditions.strip() or None)
        transition(db, app, AppStatus.CLEARANCE_ISSUED.value, user.id, 'Ethical clearance certificate issued')
        transition(db, app, AppStatus.ACTIVE.value, user.id, 'Study approval is active')
    elif decision == 'deferred':
        transition(db, app, AppStatus.DEFERRED.value, user.id, conditions or 'IRB decision deferred')
    else:
        transition(db, app, AppStatus.REJECTED.value, user.id, conditions or 'IRB application rejected')
    audit(db, user.id, 'final_irb_decision', app.id, decision)
    db.commit()
    return RedirectResponse(f'/applications/{app.id}', status_code=303)


@router.get('/certificates/{certificate_id}/download')
def download_certificate(request: Request, certificate_id: str, db: Session = Depends(get_db)):
    user = require_user(request, db)
    cert = db.get(ClearanceCertificate, certificate_id)
    if not cert:
        raise HTTPException(404, 'Certificate not found')
    app = get_app_or_404(db, cert.application_id)
    allowed = user.id == app.applicant_id or user.role in {Role.SUPERADMIN.value, Role.IRB_SECRETARIAT.value, Role.IRB_CHAIR.value}
    if user.role == Role.COLLEGE_ADMIN.value and user.college_id == app.college_id:
        allowed = True
    if not allowed:
        raise HTTPException(403)
    if not cert.pdf_stored_name:
        raise HTTPException(404, 'Certificate PDF has not been generated')
    path = certificate_path(cert.pdf_stored_name)
    if not path.exists():
        raise HTTPException(404, 'Certificate file is missing')
    audit(db, user.id, 'certificate_downloaded', app.id, cert.certificate_no)
    db.commit()
    return FileResponse(path, filename=f'{cert.certificate_no}.pdf', media_type='application/pdf')


@router.post('/applications/{app_id}/post-approval')
def submit_post_approval_request(
    request: Request,
    app_id: str,
    request_type: str = Form(...),
    summary: str = Form(...),
    file: UploadFile | None = File(None),
    db: Session = Depends(get_db),
):
    user = require_user(request, db)
    app = get_app_or_404(db, app_id)
    if user.id != app.applicant_id:
        raise HTTPException(403)
    if app.status not in {AppStatus.ACTIVE.value, AppStatus.CLEARANCE_ISSUED.value}:
        raise HTTPException(400, 'Post-approval requests are available only for an active approved study.')
    if request_type not in {'amendment', 'renewal', 'adverse_event', 'closure'}:
        raise HTTPException(400, 'Unknown post-approval request type.')
    if db.scalar(select(PostApprovalRequest.id).where(PostApprovalRequest.application_id == app.id, PostApprovalRequest.request_type == request_type, PostApprovalRequest.status == 'pending')):
        raise HTTPException(400, f'A pending {request_type.replace("_", " ")} request already exists.')

    supporting_document_id = None
    if file and file.filename:
        stored, original = save_upload(file, app.id)
        maxver = db.scalar(
            select(func.max(ApplicationDocument.version)).where(
                ApplicationDocument.application_id == app.id,
                ApplicationDocument.document_type == f'Post-Approval {request_type.replace("_", " ").title()}',
            )
        ) or 0
        doc = ApplicationDocument(
            application_id=app.id,
            document_type=f'Post-Approval {request_type.replace("_", " ").title()}',
            original_name=original,
            stored_name=stored,
            version=maxver + 1,
            uploaded_by=user.id,
        )
        db.add(doc)
        db.flush()
        supporting_document_id = doc.id

    req = PostApprovalRequest(
        application_id=app.id,
        request_type=request_type,
        summary=summary.strip(),
        supporting_document_id=supporting_document_id,
        submitted_by=user.id,
    )
    db.add(req)
    status_map = {
        'amendment': AppStatus.AMENDMENT_PENDING.value,
        'renewal': AppStatus.RENEWAL_PENDING.value,
        'adverse_event': AppStatus.ADVERSE_EVENT_PENDING.value,
        'closure': AppStatus.CLOSURE_PENDING.value,
    }
    transition(db, app, status_map[request_type], user.id, f'{request_type.replace("_", " ").title()} request submitted')
    audit(db, user.id, 'post_approval_request_submitted', app.id, request_type)
    db.commit()
    return RedirectResponse(f'/applications/{app.id}', status_code=303)


@router.post('/post-approval/{request_id}/decide')
def decide_post_approval_request(
    request: Request,
    request_id: str,
    decision: str = Form(...),
    note: str = Form(''),
    db: Session = Depends(get_db),
):
    user = require_user(request, db)
    require_roles(user, Role.IRB_SECRETARIAT.value, Role.IRB_CHAIR.value, Role.SUPERADMIN.value)
    req = db.get(PostApprovalRequest, request_id)
    if not req or req.status != 'pending':
        raise HTTPException(404, 'Pending post-approval request not found')
    if decision not in {'approve', 'return', 'reject'}:
        raise HTTPException(400, 'Select a valid decision.')
    app = get_app_or_404(db, req.application_id)
    req.status = {'approve': 'approved', 'return': 'returned', 'reject': 'rejected'}[decision]
    req.decided_by = user.id
    req.decision_note = note or None
    req.decided_at = datetime.utcnow()

    if decision == 'approve':
        if req.request_type == 'closure':
            transition(db, app, AppStatus.CLOSED.value, user.id, note or 'Study closure approved')
            for cert in db.scalars(select(ClearanceCertificate).where(ClearanceCertificate.application_id == app.id, ClearanceCertificate.status == 'valid')).all():
                cert.status = 'closed'
        elif req.request_type == 'renewal':
            for cert in db.scalars(select(ClearanceCertificate).where(ClearanceCertificate.application_id == app.id, ClearanceCertificate.status == 'valid')).all():
                cert.status = 'superseded'
            issue_clearance(db, app, user, note.strip() or None)
            transition(db, app, AppStatus.ACTIVE.value, user.id, note or 'Renewal approved and new clearance issued')
        else:
            transition(db, app, AppStatus.ACTIVE.value, user.id, note or f'{req.request_type.replace("_", " ").title()} reviewed and approved')
    else:
        transition(db, app, AppStatus.ACTIVE.value, user.id, note or f'{req.request_type.replace("_", " ").title()} request {req.status}')
    audit(db, user.id, 'post_approval_request_decision', app.id, f'{req.request_type}: {decision}')
    db.commit()
    return RedirectResponse(f'/applications/{app.id}', status_code=303)


@router.get('/documents/{document_id}/download')
def download_document(request: Request, document_id: str, db: Session = Depends(get_db)):
    user = require_user(request, db)
    doc = db.get(ApplicationDocument, document_id)
    if not doc:
        raise HTTPException(404)
    app = get_app_or_404(db, doc.application_id)
    if not can_view_documents(db, user, app):
        raise HTTPException(403)
    path = storage_path(app.id, doc.stored_name)
    if not path.exists():
        raise HTTPException(404, 'Stored file missing')
    audit(db, user.id, 'document_downloaded', app.id, doc.original_name)
    db.commit()
    return FileResponse(path, filename=doc.original_name)
