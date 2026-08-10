from __future__ import annotations

from datetime import datetime, timedelta
import csv
import hashlib
import io
import secrets
import string
import zipfile

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, RedirectResponse, Response, StreamingResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session, joinedload

from ..config import (CLEARANCE_VALIDITY_DAYS, REVIEW_DUE_DAYS, PUBLIC_BASE_URL,
                      REVIEW_ASSIGNMENT_LINK_EXPIRY_DAYS, MAX_REVIEWERS_PER_APPLICATION)
from ..database import get_db
from ..models import (
    AppStatus,
    ApplicationDocument,
    ApplicationSubmission,
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
    DocumentSubmissionMeta,
    ReviewAssignmentMeta,
    ReviewAssignmentBatch,
    ReviewAssignmentBatchItem,
    ReviewAssignmentDocument,
    ReviewReportDocument,
    ReviewerAssignment,
    ReviewerDeclaration,
    ReviewerContact,
    SecretariatAttentionRequest,
    SecretariatDocumentCheck,
    Role,
    StatusHistory,
    User,
)
from ..services.auth import hash_password, require_roles, require_user
from ..services.certificate import certificate_path, generate_certificate_pdf
from ..services.email import (college_revision_request_email, college_revision_submitted_email,
                              gmail_configured, review_assignment_email)
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


REVIEW_MATERIAL_OPTIONS = [
    'Application Letter',
    'Research Protocol',
    'Completed UCC-IRB Composite Form',
    'Data Collection Instrument',
    'Participant Information Sheet',
    'Consent Form',
    'Assent Form',
    'Consent from Records Keeper',
    'Completed IRB Checklist',
    'Similarity Report',
    'Applicant Abridged CV',
    'Supervisor Approval',
    'Head of Unit Support Letter',
    'Supervisor Abridged CV',
    'Response to College Review',
    'Tracked Changes Protocol',
    'Response to IRB Review',
    'Other Supporting Document',
]

DEFAULT_REVIEW_MATERIALS = {
    'Research Protocol', 'Completed UCC-IRB Composite Form', 'Data Collection Instrument',
    'Participant Information Sheet', 'Consent Form', 'Assent Form', 'Consent from Records Keeper',
    'Response to College Review', 'Tracked Changes Protocol', 'Response to IRB Review',
}

MAX_REVIEW_EMAIL_ATTACHMENT_BYTES = 18 * 1024 * 1024
MAX_REVIEW_EMAIL_ATTACHMENTS = 18


def ctx(request, user=None, **kwargs):
    return {'request': request, 'user': user, **kwargs}




def affiliation_label(app: EthicsApplication) -> str:
    if app.college and is_direct_irb_affiliation(app.college):
        return app.department or 'Other UCC Academic/Administrative Unit'
    return app.college.name if app.college else 'Unassigned'


def required_document_rows(app: EthicsApplication, documents: list[ApplicationDocument]):
    present = {d.document_type for d in documents}
    rows = []
    protocol_ok = bool({'Research Protocol', 'Completed UCC-IRB Composite Form'} & present)
    rows.append(('Research Protocol / Completed UCC-IRB Composite Form', protocol_ok))
    for label in ['Application Letter', 'Similarity Report', 'Applicant Abridged CV']:
        rows.append((label, label in present))
    if app.applicant_type.lower() == 'student':
        for label in ['Supervisor Approval', 'Head of Unit Support Letter', 'Supervisor Abridged CV']:
            rows.append((label, label in present))
    checklist_ok = 'Completed UCC-IRB Composite Form' in present or 'Completed IRB Checklist' in present
    rows.append(('Completed IRB Checklist (or checklist contained in Composite Form)', checklist_ok))
    return rows


def document_check_map(db: Session, app_id: str):
    rows = db.scalars(
        select(SecretariatDocumentCheck).where(SecretariatDocumentCheck.application_id == app_id)
    ).all()
    return {r.document_id: r for r in rows}


def document_stage_map(db: Session, document_ids: list[str]):
    if not document_ids:
        return {}
    rows = db.scalars(
        select(DocumentSubmissionMeta).where(DocumentSubmissionMeta.document_id.in_(document_ids))
    ).all()
    return {r.document_id: r for r in rows}


def current_upload_stage(db: Session, app: EthicsApplication):
    if app.status == AppStatus.COLLEGE_REVISION.value:
        prior = db.scalar(select(func.count(ApplicationSubmission.id)).where(
            ApplicationSubmission.application_id == app.id,
            ApplicationSubmission.submission_kind == 'college_revision',
        )) or 0
        return 'college_revision', prior + 1
    if app.status == AppStatus.IRB_REVISION.value:
        prior = db.scalar(select(func.count(ApplicationSubmission.id)).where(
            ApplicationSubmission.application_id == app.id,
            ApplicationSubmission.submission_kind == 'irb_revision',
        )) or 0
        return 'irb_revision', prior + 1
    return 'fresh', 1


def latest_submission_kind_map(db: Session, application_ids: list[str]):
    result = {app_id: 'fresh' for app_id in application_ids}
    if not application_ids:
        return result
    rows = db.scalars(
        select(ApplicationSubmission)
        .where(ApplicationSubmission.application_id.in_(application_ids))
        .order_by(ApplicationSubmission.application_id, ApplicationSubmission.submitted_at.desc())
    ).all()
    seen = set()
    for row in rows:
        if row.application_id not in seen:
            result[row.application_id] = row.submission_kind
            seen.add(row.application_id)
    return result


def latest_submission_map(db: Session, application_ids: list[str]):
    result = {}
    if not application_ids:
        return result
    rows = db.scalars(
        select(ApplicationSubmission)
        .where(ApplicationSubmission.application_id.in_(application_ids))
        .order_by(ApplicationSubmission.application_id, ApplicationSubmission.submitted_at.desc())
    ).all()
    for row in rows:
        result.setdefault(row.application_id, row)
    return result


def reviewer_contact_for_proxy(db: Session, proxy_user_id: str):
    return db.scalar(select(ReviewerContact).where(ReviewerContact.proxy_user_id == proxy_user_id))


def reviewer_contact_map_for_batches(db: Session, batches: list[ReviewAssignmentBatch]):
    proxy_ids = [b.reviewer_id for b in batches]
    if not proxy_ids:
        return {}
    contacts = db.scalars(select(ReviewerContact).where(ReviewerContact.proxy_user_id.in_(proxy_ids))).all()
    by_proxy = {c.proxy_user_id: c for c in contacts}
    return {b.id: by_proxy.get(b.reviewer_id) for b in batches}


def get_or_create_reviewer_contact(db: Session, actor: User, *, level: str, title: str,
                                   first_name: str, last_name: str, email: str, phone: str = ''):
    email = email.strip().lower()
    if '@' not in email:
        raise HTTPException(400, 'Enter a valid reviewer email address.')
    first_name = first_name.strip()
    last_name = last_name.strip()
    if not first_name or not last_name:
        raise HTTPException(400, 'Enter the reviewer first name and surname.')
    college_id = actor.college_id if level == 'college' else None
    existing = db.scalar(select(ReviewerContact).where(
        ReviewerContact.level == level,
        ReviewerContact.college_id == college_id,
        ReviewerContact.email == email,
    ))
    full_name = ' '.join(x for x in [title.strip(), first_name, last_name] if x)
    if existing:
        existing.title = title.strip() or None
        existing.first_name = first_name
        existing.last_name = last_name
        existing.phone = phone.strip() or None
        existing.active = True
        proxy = db.get(User, existing.proxy_user_id)
        if proxy:
            proxy.full_name = full_name
            proxy.college_id = college_id
            proxy.role = 'reviewer_contact_proxy'
            proxy.active = False
        return existing, proxy

    fingerprint = hashlib.sha256(f'{level}|{college_id or "irb"}|{email}'.encode()).hexdigest()[:24]
    proxy_email = f'reviewer-{fingerprint}@internal.ucc-irb.local'
    proxy = db.scalar(select(User).where(User.email == proxy_email))
    if not proxy:
        proxy = User(
            email=proxy_email,
            full_name=full_name,
            password_hash=hash_password(secrets.token_urlsafe(36)),
            role='reviewer_contact_proxy',
            college_id=college_id,
            active=False,
        )
        db.add(proxy)
        db.flush()
    contact = ReviewerContact(
        proxy_user_id=proxy.id,
        level=level,
        college_id=college_id,
        title=title.strip() or None,
        first_name=first_name,
        last_name=last_name,
        email=email,
        phone=phone.strip() or None,
        active=True,
        created_by=actor.id,
    )
    db.add(contact)
    db.flush()
    return contact, proxy


def resolve_attention_requests(db: Session, app_id: str, actor_id: str, note: str):
    pending = db.scalars(select(SecretariatAttentionRequest).where(
        SecretariatAttentionRequest.application_id == app_id,
        SecretariatAttentionRequest.status == 'pending',
    )).all()
    for req in pending:
        req.status = 'resolved'
        req.resolved_by = actor_id
        req.resolved_at = datetime.utcnow()
        req.resolution_note = note

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
        # The College may see metadata from first submission, but substantive documents become
        # available automatically only after the Secretariat marks the application complete
        # and forwards it to the Scientific Committee.
        locked = {
            AppStatus.DRAFT.value,
            AppStatus.SUBMITTED.value,
            AppStatus.SECRETARIAT_SCREENING.value,
            AppStatus.RETURNED_ADMIN.value,
            AppStatus.ADMIN_COMPLETE.value,
        }
        return app.status not in locked and is_scientific_committee_college(app.college)
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


def review_token_hash(token: str) -> str:
    return hashlib.sha256(token.encode('utf-8')).hexdigest()


def new_review_token() -> str:
    return secrets.token_urlsafe(32)


def next_review_batch_reference(db: Session, level: str) -> str:
    year = datetime.utcnow().year
    prefix = 'CSC' if level == 'college' else 'IRB'
    count = db.scalar(select(func.count(ReviewAssignmentBatch.id))) or 0
    return f'UCC-{prefix}-REV-{year}-{count + 1:05d}'


def get_review_batch_by_token(db: Session, token: str):
    return db.scalar(
        select(ReviewAssignmentBatch)
        .options(joinedload(ReviewAssignmentBatch.reviewer), joinedload(ReviewAssignmentBatch.items))
        .where(ReviewAssignmentBatch.token_hash == review_token_hash(token))
    )


def validate_review_batch(batch: ReviewAssignmentBatch | None):
    if not batch:
        return False, 404, 'This secure review assignment link is invalid.'
    if batch.revoked_at:
        return False, 410, 'This secure review assignment link has been revoked.'
    if batch.link_expires_at < datetime.utcnow():
        return False, 410, 'This secure review assignment link has expired. Please contact the assigning office.'
    return True, 200, ''


def report_documents_map(db: Session, assignments):
    ids = [a.id for a in assignments]
    result = {a.id: [] for a in assignments}
    if not ids:
        return result
    for doc in db.scalars(
        select(ReviewReportDocument)
        .where(ReviewReportDocument.assignment_id.in_(ids))
        .order_by(ReviewReportDocument.uploaded_at)
    ).all():
        result.setdefault(doc.assignment_id, []).append(doc)
    return result


def review_assignment_documents_map(db: Session, assignments):
    ids = [a.id for a in assignments]
    result = {a.id: [] for a in assignments}
    if not ids:
        return result
    rows = db.scalars(
        select(ReviewAssignmentDocument)
        .options(joinedload(ReviewAssignmentDocument.document))
        .where(ReviewAssignmentDocument.assignment_id.in_(ids))
        .order_by(ReviewAssignmentDocument.created_at)
    ).all()
    for row in rows:
        if row.document:
            result.setdefault(row.assignment_id, []).append(row.document)
    return result


def latest_documents_for_types(db: Session, app_id: str, document_types: list[str] | None = None):
    docs = db.scalars(
        select(ApplicationDocument)
        .where(ApplicationDocument.application_id == app_id)
        .order_by(ApplicationDocument.document_type, ApplicationDocument.version.desc(), ApplicationDocument.uploaded_at.desc())
    ).all()
    latest = {}
    for doc in docs:
        latest.setdefault(doc.document_type, doc)
    if document_types:
        wanted = set(document_types)
        return [doc for dtype, doc in latest.items() if dtype in wanted]
    return list(latest.values())


def latest_revision_documents(db: Session, app_id: str, submission_kind: str = 'college_revision'):
    latest_round = db.scalar(select(func.max(DocumentSubmissionMeta.round_no)).where(
        DocumentSubmissionMeta.application_id == app_id,
        DocumentSubmissionMeta.submission_kind == submission_kind,
    ))
    if not latest_round:
        return []
    return db.scalars(
        select(ApplicationDocument)
        .join(DocumentSubmissionMeta, DocumentSubmissionMeta.document_id == ApplicationDocument.id)
        .where(
            DocumentSubmissionMeta.application_id == app_id,
            DocumentSubmissionMeta.submission_kind == submission_kind,
            DocumentSubmissionMeta.round_no == latest_round,
        )
        .order_by(ApplicationDocument.document_type, ApplicationDocument.version.desc())
    ).all()


def selected_review_documents(db: Session, assignment_id: str):
    rows = db.scalars(
        select(ReviewAssignmentDocument)
        .options(joinedload(ReviewAssignmentDocument.document))
        .where(ReviewAssignmentDocument.assignment_id == assignment_id)
        .order_by(ReviewAssignmentDocument.created_at)
    ).all()
    return [r.document for r in rows if r.document]


def review_email_attachments_for_batch(db: Session, batch: ReviewAssignmentBatch):
    items = db.scalars(
        select(ReviewAssignmentBatchItem)
        .options(joinedload(ReviewAssignmentBatchItem.assignment).joinedload(ReviewerAssignment.application))
        .where(ReviewAssignmentBatchItem.batch_id == batch.id)
        .order_by(ReviewAssignmentBatchItem.work_no)
    ).unique().all()
    attachments = []
    skipped = []
    total = 0
    for item in items:
        assignment = item.assignment
        app = assignment.application
        for doc in selected_review_documents(db, assignment.id):
            path = storage_path(app.id, doc.stored_name)
            if not path.exists():
                skipped.append(doc.original_name)
                continue
            size = path.stat().st_size
            filename = f'{_safe_zip_name(app.reference_no or app.id)} - {_safe_zip_name(doc.document_type)} - {_safe_zip_name(doc.original_name)}'
            if len(attachments) >= MAX_REVIEW_EMAIL_ATTACHMENTS or total + size > MAX_REVIEW_EMAIL_ATTACHMENT_BYTES:
                skipped.append(filename)
                continue
            attachments.append((path, filename))
            total += size
    note = f'{len(attachments)} selected document(s) attached to this email.'
    if skipped:
        note += f' {len(skipped)} additional selected document(s) were kept in the secure workspace because of email attachment limits.'
    if not attachments:
        note = 'No document files were attached. The selected review materials remain available in the secure workspace.'
    return attachments, note


def applicant_public_history_note(note: str | None) -> str | None:
    if not note:
        return note
    lowered = note.lower()
    if lowered.startswith('assigned scientific review to ') or 'scientific reviewer' in lowered and 'assigned' in lowered:
        return 'Assigned scientific reviewer'
    if lowered.startswith('assigned irb ethical review to ') or 'irb ethical reviewer' in lowered and 'assigned' in lowered:
        return 'Assigned IRB ethical reviewer'
    return note


def previous_college_reviewers(db: Session, app_id: str):
    assignments = db.scalars(
        select(ReviewerAssignment)
        .options(joinedload(ReviewerAssignment.reviewer))
        .where(
            ReviewerAssignment.application_id == app_id,
            ReviewerAssignment.level == 'college',
            ReviewerAssignment.status == 'completed',
        )
        .order_by(ReviewerAssignment.completed_at.desc(), ReviewerAssignment.assigned_at.desc())
    ).all()
    seen = set()
    result = []
    for assignment in assignments:
        if assignment.reviewer_id in seen:
            continue
        seen.add(assignment.reviewer_id)
        contact = reviewer_contact_for_proxy(db, assignment.reviewer_id)
        if contact:
            name = ' '.join(x for x in [contact.title or '', contact.first_name, contact.last_name] if x).strip()
            email = contact.email
        else:
            name = assignment.reviewer.full_name if assignment.reviewer else 'Previous reviewer'
            email = assignment.reviewer.email if assignment.reviewer else ''
        result.append({'reviewer_id': assignment.reviewer_id, 'name': name, 'email': email, 'last_completed': assignment.completed_at})
    return result


def complete_review_assignment(db: Session, assignment: ReviewerAssignment, actor_id: str,
                               recommendation: str, comments: str):
    assignment.recommendation = recommendation
    assignment.comments = comments
    assignment.status = 'completed'
    assignment.completed_at = datetime.utcnow()
    app = get_app_or_404(db, assignment.application_id)
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
            transition(db, app, AppStatus.AWAITING_COLLEGE_DECISION.value, actor_id, 'College scientific review round completed')
        else:
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
                transition(db, app, AppStatus.IRB_REVISION.value, actor_id, 'IRB ethical review requires applicant revision')
            elif (classification and classification.classification == 'full_board') or 'full_board' in recs:
                transition(db, app, AppStatus.FULL_BOARD.value, actor_id, 'Application requires Full Board consideration')
            else:
                transition(db, app, AppStatus.AWAITING_FINAL_DECISION.value, actor_id, 'IRB review completed; awaiting authorised final decision')

    audit(db, actor_id, 'review_submitted', app.id, f'{assignment.level}: {recommendation}')
    return app


def current_review_round_start(db: Session, app_id: str, level: str) -> datetime:
    milestones = (
        [AppStatus.FORWARDED_TO_COLLEGE.value, AppStatus.AWAITING_COLLEGE_REVIEWER.value, AppStatus.COLLEGE_REVISED.value]
        if level == 'college'
        else [AppStatus.AWAITING_IRB_REVIEWER.value, AppStatus.IRB_REVISED.value]
    )
    milestone = db.scalar(
        select(StatusHistory)
        .where(StatusHistory.application_id == app_id, StatusHistory.to_status.in_(milestones))
        .order_by(StatusHistory.created_at.desc())
    )
    return milestone.created_at if milestone else datetime.min


def assignment_count_for_application(db: Session, app_id: str, level: str) -> int:
    round_start = current_review_round_start(db, app_id, level)
    return db.scalar(
        select(func.count(ReviewerAssignment.id)).where(
            ReviewerAssignment.application_id == app_id,
            ReviewerAssignment.level == level,
            ReviewerAssignment.assigned_at >= round_start,
            ReviewerAssignment.status.in_(['assigned', 'accepted', 'completed']),
        )
    ) or 0


def active_duplicate_assignment(db: Session, app_id: str, reviewer_id: str, level: str):
    round_start = current_review_round_start(db, app_id, level)
    return db.scalar(
        select(ReviewerAssignment.id).where(
            ReviewerAssignment.application_id == app_id,
            ReviewerAssignment.reviewer_id == reviewer_id,
            ReviewerAssignment.level == level,
            ReviewerAssignment.assigned_at >= round_start,
            ReviewerAssignment.status.in_(['assigned', 'accepted', 'completed']),
        )
    )


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
def home(request: Request, db: Session = Depends(get_db)):
    user = None
    user_id = request.session.get('user_id')
    if user_id:
        candidate = db.get(User, user_id)
        if candidate and candidate.active:
            user = candidate
    return request.app.state.templates.TemplateResponse(request, 'home.html', ctx(request, user))


@router.get('/applicant-guide')
def applicant_guide(request: Request, db: Session = Depends(get_db)):
    user = None
    user_id = request.session.get('user_id')
    if user_id:
        candidate = db.get(User, user_id)
        if candidate and candidate.active:
            user = candidate
    return request.app.state.templates.TemplateResponse(request, 'applicant_guide.html', ctx(request, user))


@router.get('/resources')
def resources_redirect():
    return RedirectResponse('/applicant-guide#resources', status_code=303)


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
            .options(joinedload(EthicsApplication.applicant), joinedload(EthicsApplication.college))
            .where(EthicsApplication.college_id == user.college_id, EthicsApplication.status != AppStatus.DRAFT.value)
            .order_by(EthicsApplication.updated_at.desc())
        ).unique().all()
        kinds = latest_submission_kind_map(db, [a.id for a in apps])
        pending_initial = [a for a in apps if a.status in {AppStatus.SUBMITTED.value, AppStatus.SECRETARIAT_SCREENING.value}]
        fresh_apps = [a for a in apps if a not in pending_initial and kinds.get(a.id, 'fresh') == 'fresh']
        revised_apps = [a for a in apps if kinds.get(a.id) == 'college_revision']
        pending_attention = db.scalars(select(SecretariatAttentionRequest).where(
            SecretariatAttentionRequest.college_id == user.college_id,
            SecretariatAttentionRequest.status == 'pending',
        )).all()
        attention_by_app = {r.application_id: r for r in pending_attention}
        counts = {st: sum(1 for a in apps if a.status == st) for st in set(a.status for a in apps)}
        previous_reviewers_by_app = {a.id: previous_college_reviewers(db, a.id) for a in revised_apps}
        return request.app.state.templates.TemplateResponse(
            request, 'dashboard_college.html',
            ctx(request, user, apps=apps, counts=counts, pending_initial=pending_initial, fresh_apps=fresh_apps,
                revised_apps=revised_apps, attention_by_app=attention_by_app, submission_kinds=kinds,
                previous_reviewers_by_app=previous_reviewers_by_app)
        )

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
        admin_users = [u for u in users if u.role not in {Role.APPLICANT.value, 'reviewer_contact_proxy'}]
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
        pending_attention = db.scalars(
            select(SecretariatAttentionRequest)
            .options(
                joinedload(SecretariatAttentionRequest.application).joinedload(EthicsApplication.college),
                joinedload(SecretariatAttentionRequest.application).joinedload(EthicsApplication.applicant),
            )
            .where(SecretariatAttentionRequest.status == 'pending')
            .order_by(SecretariatAttentionRequest.requested_at)
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
        screening_apps = [a for a in apps if a.status in {AppStatus.SUBMITTED.value, AppStatus.SECRETARIAT_SCREENING.value}]
        return request.app.state.templates.TemplateResponse(
            request,
            'dashboard_secretariat.html',
            ctx(
                request, user, apps=apps, pending_access=pending_access, post_requests=post_requests,
                meetings_count=meetings_count, direct_apps=direct_apps, college_path_apps=college_path_apps, screening_apps=screening_apps, pending_attention=pending_attention,
            ),
        )

    raise HTTPException(403)


@router.get('/system-admin')
def system_admin_portal(request: Request, db: Session = Depends(get_db)):
    user = require_user(request, db)
    require_roles(user, Role.SUPERADMIN.value)
    users = db.scalars(select(User).options(joinedload(User.college)).order_by(User.created_at.desc())).all()
    colleges = get_scientific_committee_colleges(db)
    admin_users = [u for u in users if u.role not in {Role.APPLICANT.value, 'reviewer_contact_proxy'}]
    applicant_count = sum(1 for u in users if u.role == Role.APPLICANT.value)
    return request.app.state.templates.TemplateResponse(
        request,
        'dashboard_admin.html',
        ctx(
            request,
            user,
            users=admin_users,
            colleges=colleges,
            applicant_count=applicant_count,
            created_password=None,
            created_email=None,
            error=None,
        ),
    )


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
        admin_users = [u for u in users if u.role not in {Role.APPLICANT.value, 'reviewer_contact_proxy'}]
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
    admin_users = [u for u in users if u.role not in {Role.APPLICANT.value, 'reviewer_contact_proxy'}]
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
    return RedirectResponse('/system-admin', status_code=303)


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
    stage_map = document_stage_map(db, [d.id for d in documents])
    fresh_documents = [d for d in documents if not stage_map.get(d.id) or stage_map[d.id].submission_kind == 'fresh']
    college_revision_documents = [d for d in documents if stage_map.get(d.id) and stage_map[d.id].submission_kind == 'college_revision']
    irb_revision_documents = [d for d in documents if stage_map.get(d.id) and stage_map[d.id].submission_kind == 'irb_revision']
    screening_checks = document_check_map(db, app.id) if user.role in {Role.IRB_SECRETARIAT.value, Role.IRB_CHAIR.value, Role.SUPERADMIN.value} else {}
    required_rows = required_document_rows(app, documents) if can_docs else []
    attention_requests = db.scalars(
        select(SecretariatAttentionRequest)
        .where(SecretariatAttentionRequest.application_id == app.id)
        .order_by(SecretariatAttentionRequest.requested_at.desc())
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
    report_documents = report_documents_map(db, assignments)
    assignment_documents = review_assignment_documents_map(db, assignments)
    prior_college_reviewers = previous_college_reviewers(db, app.id) if user.role == Role.COLLEGE_ADMIN.value and user.college_id == app.college_id else []

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
    public_history_notes = {h.id: applicant_public_history_note(h.note) for h in status_history}
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
            fresh_documents=fresh_documents,
            college_revision_documents=college_revision_documents,
            irb_revision_documents=irb_revision_documents,
            document_stages=stage_map,
            screening_checks=screening_checks,
            required_document_rows=required_rows,
            attention_requests=attention_requests,
            access_requests=access,
            assignments=assignments,
            declarations=declarations,
            assignment_meta=assignment_meta,
            report_documents=report_documents,
            assignment_documents=assignment_documents,
            previous_college_reviewers=prior_college_reviewers,
            college_reviewers=college_reviewers,
            irb_reviewers=irb_reviewers,
            reviewer_workload=reviewer_workload(db, irb_reviewers or college_reviewers),
            college_decisions=college_decisions,
            classifications=classifications,
            irb_decisions=irb_decisions,
            certificates=certificates,
            post_requests=post_requests,
            status_history=status_history,
            public_history_notes=public_history_notes,
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
    db.flush()
    submission_kind, round_no = current_upload_stage(db, app)
    db.add(DocumentSubmissionMeta(
        document_id=doc.id, application_id=app.id, submission_kind=submission_kind, round_no=round_no
    ))
    audit(db, user.id, 'document_uploaded', app.id, f'{document_type} v{doc.version}: {original} | {submission_kind} round {round_no}')
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
    present = {d.document_type for d in docs}

    # Core completeness rules are based on the current UCC-IRB application instructions.
    # Study-specific items such as consent/assent and data collection instruments remain
    # subject to the nature of the study and are also checked during Secretariat screening.
    missing = set()
    if not ({'Research Protocol', 'Completed UCC-IRB Composite Form'} & present):
        missing.add('Research Protocol / Completed UCC-IRB Composite Form')
    required = {'Application Letter', 'Similarity Report', 'Applicant Abridged CV'}
    if app.applicant_type.lower() == 'student':
        required.update({'Supervisor Approval', 'Head of Unit Support Letter', 'Supervisor Abridged CV'})
    missing.update(required - present)

    # The Composite Form contains the UCC-IRB checklist. When applicants upload a separate
    # protocol instead, require the completed checklist as an additional document.
    if 'Completed UCC-IRB Composite Form' not in present and 'Completed IRB Checklist' not in present:
        missing.add('Completed IRB Checklist')

    if missing:
        raise HTTPException(400, f'Missing required document(s): {", ".join(sorted(missing))}')
    if not app.reference_no:
        seq = (db.scalar(select(func.count(EthicsApplication.id)).where(EthicsApplication.submitted_at.is_not(None))) or 0) + 1
        app.reference_no = f'UCC-IRB-{datetime.utcnow().year}-{seq:05d}'
    app.submitted_at = datetime.utcnow()
    existing_fresh = db.scalar(select(ApplicationSubmission.id).where(
        ApplicationSubmission.application_id == app.id, ApplicationSubmission.submission_kind == 'fresh'
    ))
    if not existing_fresh:
        db.add(ApplicationSubmission(application_id=app.id, submission_kind='fresh', round_no=1, submitted_by=user.id, submitted_at=app.submitted_at))
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
        submission_kind = 'college_revision'
        note = 'Applicant submitted revised documents directly to the College Scientific Committee'
    elif app.status == AppStatus.IRB_REVISION.value:
        required_type = 'Response to IRB Review'
        next_status = AppStatus.IRB_REVISED.value
        submission_kind = 'irb_revision'
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
    round_no = (db.scalar(select(func.count(ApplicationSubmission.id)).where(
        ApplicationSubmission.application_id == app.id,
        ApplicationSubmission.submission_kind == submission_kind,
    )) or 0) + 1
    db.add(ApplicationSubmission(
        application_id=app.id, submission_kind=submission_kind, round_no=round_no, submitted_by=user.id
    ))
    transition(db, app, next_status, user.id, note)
    audit(db, user.id, 'revised_application_submitted', app.id, f'{submission_kind} round {round_no}')
    db.commit()

    if submission_kind == 'college_revision' and gmail_configured():
        college_admins = db.scalars(select(User).where(
            User.role == Role.COLLEGE_ADMIN.value, User.college_id == app.college_id, User.active == True
        )).all()
        for officer in college_admins:
            try:
                college_revision_submitted_email(
                    officer_name=officer.full_name, officer_email=officer.email, applicant_name=app.applicant.full_name,
                    reference_no=app.reference_no or '', research_title=app.title, college_name=app.college.name,
                    application_url=f'{_base_url(request)}/applications/{app.id}'
                )
            except Exception as exc:
                audit(db, user.id, 'college_revision_notification_failed', app.id, str(exc)[:500])
                db.commit()
    return RedirectResponse(f'/applications/{app.id}', status_code=303)


@router.post('/secretariat/{app_id}/checklist')
def save_secretariat_checklist(
    request: Request,
    app_id: str,
    checked_document_ids: list[str] = Form([]),
    checklist_note: str = Form(''),
    db: Session = Depends(get_db),
):
    user = require_user(request, db)
    require_roles(user, Role.IRB_SECRETARIAT.value, Role.IRB_CHAIR.value, Role.SUPERADMIN.value)
    app = get_app_or_404(db, app_id)
    if app.status not in {AppStatus.SUBMITTED.value, AppStatus.SECRETARIAT_SCREENING.value}:
        raise HTTPException(400, 'The Secretariat checklist is only available during initial screening.')
    documents = db.scalars(select(ApplicationDocument).where(ApplicationDocument.application_id == app.id)).all()
    valid_ids = {d.id for d in documents}
    checked = set(checked_document_ids) & valid_ids
    existing = document_check_map(db, app.id)
    now = datetime.utcnow()
    for doc in documents:
        row = existing.get(doc.id)
        if not row:
            row = SecretariatDocumentCheck(application_id=app.id, document_id=doc.id)
            db.add(row)
        row.verified = doc.id in checked
        row.note = checklist_note.strip() or None
        row.checked_by = user.id
        row.checked_at = now
    if app.status == AppStatus.SUBMITTED.value:
        transition(db, app, AppStatus.SECRETARIAT_SCREENING.value, user.id, 'IRB Secretariat document checklist started')
    audit(db, user.id, 'secretariat_document_checklist_saved', app.id, f'{len(checked)}/{len(documents)} documents verified')
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

    documents = db.scalars(select(ApplicationDocument).where(ApplicationDocument.application_id == app.id)).all()
    if outcome == 'complete':
        required_rows = required_document_rows(app, documents)
        missing = [label for label, present in required_rows if not present]
        checks = document_check_map(db, app.id)
        unchecked = [d for d in documents if not checks.get(d.id) or not checks[d.id].verified]
        if missing:
            raise HTTPException(400, 'Cannot mark complete. Required item(s) missing: ' + ', '.join(missing))
        if unchecked:
            raise HTTPException(400, 'Cannot mark complete until every submitted document is checked in the Secretariat checklist.')

        transition(db, app, AppStatus.ADMIN_COMPLETE.value, user.id, note or 'Administrative screening complete')
        resolve_attention_requests(db, app.id, user.id, 'Secretariat screening completed')
        if is_scientific_committee_college(app.college):
            for doc in documents:
                doc.active_for_college = True
            transition(
                db, app, AppStatus.FORWARDED_TO_COLLEGE.value, user.id,
                f'Administratively complete and forwarded to {app.college.name} Scientific Committee'
            )
        else:
            transition(db, app, AppStatus.DIRECT_IRB.value, user.id, 'Affiliation has no College Scientific Committee. Application retained by the IRB Secretariat for direct IRB review classification')
    elif outcome == 'return':
        app.secretariat_note = note
        resolve_attention_requests(db, app.id, user.id, 'Application returned to applicant for administrative correction')
        transition(db, app, AppStatus.RETURNED_ADMIN.value, user.id, note or 'Returned for administrative correction')
    else:
        raise HTTPException(400, 'Unknown screening outcome')
    db.commit()
    return RedirectResponse(f'/applications/{app.id}', status_code=303)


@router.post('/college/{app_id}/request-secretariat-attention')
def request_secretariat_attention(
    request: Request,
    app_id: str,
    note: str = Form(''),
    db: Session = Depends(get_db),
):
    user = require_user(request, db)
    require_roles(user, Role.COLLEGE_ADMIN.value)
    app = get_app_or_404(db, app_id)
    if user.college_id != app.college_id or not is_scientific_committee_college(app.college):
        raise HTTPException(403)
    if app.status not in {AppStatus.SUBMITTED.value, AppStatus.SECRETARIAT_SCREENING.value}:
        raise HTTPException(400, 'This application is no longer waiting at the IRB Secretariat for initial screening.')
    existing = db.scalar(select(SecretariatAttentionRequest.id).where(
        SecretariatAttentionRequest.application_id == app.id,
        SecretariatAttentionRequest.status == 'pending',
    ))
    if existing:
        raise HTTPException(409, 'A Secretariat attention request is already pending for this application.')
    req = SecretariatAttentionRequest(
        application_id=app.id, college_id=app.college_id, requested_by=user.id, note=note.strip() or None
    )
    db.add(req)
    audit(db, user.id, 'college_requested_secretariat_attention', app.id, note.strip() or 'Please attend to this first submission')
    db.commit()
    return RedirectResponse('/dashboard', status_code=303)


@router.post('/secretariat/attention/{request_id}/resolve')
def resolve_secretariat_attention(
    request: Request,
    request_id: str,
    note: str = Form(''),
    db: Session = Depends(get_db),
):
    user = require_user(request, db)
    require_roles(user, Role.IRB_SECRETARIAT.value, Role.IRB_CHAIR.value, Role.SUPERADMIN.value)
    req = db.get(SecretariatAttentionRequest, request_id)
    if not req or req.status != 'pending':
        raise HTTPException(404, 'Pending attention request not found.')
    req.status = 'resolved'
    req.resolved_by = user.id
    req.resolved_at = datetime.utcnow()
    req.resolution_note = note.strip() or 'Acknowledged by IRB Secretariat'
    audit(db, user.id, 'secretariat_attention_request_resolved', req.application_id, req.resolution_note)
    db.commit()
    return RedirectResponse(f'/applications/{req.application_id}', status_code=303)


@router.get('/secretariat/register')
def secretariat_register(request: Request, db: Session = Depends(get_db)):
    user = require_user(request, db)
    require_roles(user, Role.IRB_SECRETARIAT.value, Role.IRB_CHAIR.value, Role.SUPERADMIN.value)
    apps = db.scalars(
        select(EthicsApplication)
        .options(joinedload(EthicsApplication.applicant), joinedload(EthicsApplication.college))
        .where(EthicsApplication.status != AppStatus.DRAFT.value)
    ).unique().all()
    kinds = latest_submission_kind_map(db, [a.id for a in apps])
    latest_submissions = latest_submission_map(db, [a.id for a in apps])
    direct_units = sorted({a.department for a in apps if a.college and is_direct_irb_affiliation(a.college) and a.department})
    colleges = get_scientific_committee_colleges(db)

    q = (request.query_params.get('q') or '').strip().lower()
    affiliation = request.query_params.get('affiliation') or ''
    status_filter = request.query_params.get('status') or ''
    kind_filter = request.query_params.get('submission_kind') or ''
    sort_by = request.query_params.get('sort') or 'submitted_at'
    direction = request.query_params.get('direction') or 'desc'

    def matches(a):
        if q and q not in ' '.join([a.reference_no or '', a.applicant.full_name, a.title, a.department or '', affiliation_label(a)]).lower():
            return False
        if affiliation:
            if affiliation.startswith('unit:'):
                if not (is_direct_irb_affiliation(a.college) and (a.department or '') == affiliation[5:]):
                    return False
            elif a.college_id != affiliation:
                return False
        if status_filter and a.status != status_filter:
            return False
        if kind_filter and kinds.get(a.id, 'fresh') != kind_filter:
            return False
        return True

    rows = [a for a in apps if matches(a)]
    key_map = {
        'reference': lambda a: a.reference_no or '',
        'applicant': lambda a: a.applicant.full_name.lower(),
        'affiliation': lambda a: affiliation_label(a).lower(),
        'status': lambda a: a.status,
        'updated_at': lambda a: a.updated_at or datetime.min,
        'submitted_at': lambda a: (latest_submissions.get(a.id).submitted_at if latest_submissions.get(a.id) else a.submitted_at) or datetime.min,
    }
    rows.sort(key=key_map.get(sort_by, key_map['submitted_at']), reverse=direction != 'asc')

    if request.query_params.get('format') == 'csv':
        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow(['S/N', 'Reference', 'Applicant', 'Applicant Type', 'College/Unit', 'Department/Unit', 'Research Title', 'Submission Type', 'Submitted', 'Status', 'Last Updated'])
        for idx, a in enumerate(rows, start=1):
            writer.writerow([idx, a.reference_no or '', a.applicant.full_name, a.applicant_type, affiliation_label(a), a.department or '', a.title, kinds.get(a.id, 'fresh'), (latest_submissions.get(a.id).submitted_at.isoformat() if latest_submissions.get(a.id) else (a.submitted_at.isoformat() if a.submitted_at else '')), a.status, a.updated_at.isoformat() if a.updated_at else ''])
        return Response(output.getvalue(), media_type='text/csv', headers={'Content-Disposition': 'attachment; filename="ucc-irb-submission-register.csv"'})

    statuses = sorted({a.status for a in apps})
    return request.app.state.templates.TemplateResponse(
        request, 'secretariat_register.html',
        ctx(request, user, rows=rows, colleges=colleges, direct_units=direct_units, statuses=statuses,
            kinds=kinds, latest_submissions=latest_submissions, filters={'q': q, 'affiliation': affiliation, 'status': status_filter, 'submission_kind': kind_filter, 'sort': sort_by, 'direction': direction})
    )


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
    report_file: UploadFile = File(...),
    annotated_protocol: UploadFile | None = File(None),
    supporting_file: UploadFile | None = File(None),
    db: Session = Depends(get_db),
):
    user = require_user(request, db)
    assignment = get_assignment_or_404(db, assignment_id)
    if assignment.reviewer_id != user.id or assignment.status not in {'accepted', 'assigned'}:
        raise HTTPException(403)
    declaration = db.scalar(select(ReviewerDeclaration).where(ReviewerDeclaration.assignment_id == assignment.id))
    if not declaration or declaration.declaration != 'clear':
        raise HTTPException(400, 'Submit a no-conflict declaration before completing the review.')

    allowed = (
        {'scientifically_recommended', 'minor_revision', 'major_revision', 'specialist_review', 'not_recommended'}
        if assignment.level == 'college'
        else {'approve', 'approve_conditions', 'minor_revision', 'major_revision', 'full_board', 'reject'}
    )
    if recommendation not in allowed:
        raise HTTPException(400, 'Select a valid review recommendation.')
    if not comments.strip():
        raise HTTPException(400, 'Review comments are required.')

    app = get_app_or_404(db, assignment.application_id)
    uploads = [
        ('review_report', report_file),
        ('annotated_protocol', annotated_protocol),
        ('supporting', supporting_file),
    ]
    for kind, upload in uploads:
        if not upload or not upload.filename:
            continue
        stored, original = save_upload(upload, app.id)
        db.add(ReviewReportDocument(
            assignment_id=assignment.id,
            document_kind=kind,
            original_name=original,
            stored_name=stored,
        ))

    complete_review_assignment(db, assignment, user.id, recommendation, comments.strip())
    db.commit()
    return RedirectResponse(f'/applications/{app.id}', status_code=303)


@router.post('/college/{app_id}/revision-disposition')
def college_revision_disposition(
    request: Request,
    app_id: str,
    review_action: str = Form(...),
    reviewer_ids: list[str] = Form([]),
    due_days: int = Form(REVIEW_DUE_DAYS),
    db: Session = Depends(get_db),
):
    user = require_user(request, db)
    require_roles(user, Role.COLLEGE_ADMIN.value)
    app = get_app_or_404(db, app_id)
    if user.college_id != app.college_id or app.status != AppStatus.COLLEGE_REVISED.value:
        raise HTTPException(403, 'This revised submission is not available for College disposition.')

    if review_action == 'administrative':
        transition(db, app, AppStatus.AWAITING_COLLEGE_DECISION.value, user.id, 'Revised submission administratively reviewed by College Scientific Committee')
        audit(db, user.id, 'college_revision_administratively_reviewed', app.id, 'Proceed to College Scientific Committee decision without reviewer resubmission')
        db.commit()
        return RedirectResponse(f'/applications/{app.id}', status_code=303)

    if review_action != 'resubmit':
        raise HTTPException(400, 'Choose whether to resubmit the revision for review or review it administratively.')
    if not reviewer_ids:
        raise HTTPException(400, 'Select at least one previous reviewer for re-review.')
    if not gmail_configured():
        raise HTTPException(503, 'Gmail delivery must be configured to email revised documents to previous reviewers.')

    valid_previous = db.scalars(
        select(ReviewerAssignment)
        .where(
            ReviewerAssignment.application_id == app.id,
            ReviewerAssignment.level == 'college',
            ReviewerAssignment.status == 'completed',
            ReviewerAssignment.reviewer_id.in_(list(dict.fromkeys(reviewer_ids))),
        )
        .order_by(ReviewerAssignment.completed_at.desc())
    ).all()
    valid_ids = {a.reviewer_id for a in valid_previous}
    requested_ids = list(dict.fromkeys(reviewer_ids))
    if any(rid not in valid_ids for rid in requested_ids):
        raise HTTPException(400, 'One or more selected reviewers did not review a previous College round for this application.')

    revision_docs = latest_revision_documents(db, app.id, 'college_revision')
    if not revision_docs:
        raise HTTPException(400, 'No revised documents were found for the latest College revision round.')

    due_days = max(1, min(int(due_days), 90))
    now = datetime.utcnow()
    due_at = now + timedelta(days=due_days)
    link_days = max(REVIEW_ASSIGNMENT_LINK_EXPIRY_DAYS, due_days + 7)
    assigning_office = user.college.name + ' Scientific Committee' if user.college else 'College Scientific Committee'
    sent = 0
    failures = []

    for reviewer_id in requested_ids:
        reviewer = db.get(User, reviewer_id)
        if not reviewer:
            failures.append('A previous reviewer record could not be found.')
            continue
        token = new_review_token()
        batch = ReviewAssignmentBatch(
            reference=next_review_batch_reference(db, 'college'),
            reviewer_id=reviewer.id,
            level='college',
            token_hash=review_token_hash(token),
            link_expires_at=now + timedelta(days=link_days),
            due_at=due_at,
            message='Revised submission returned for re-review. Please focus on the applicant response and revised documents.',
            email_status='pending',
            created_by=user.id,
        )
        db.add(batch)
        db.flush()
        assignment = ReviewerAssignment(
            application_id=app.id, reviewer_id=reviewer.id, level='college', assignment_type='re-review',
            status='assigned', assigned_by=user.id,
        )
        db.add(assignment)
        db.flush()
        db.add(ReviewAssignmentMeta(assignment_id=assignment.id, due_at=due_at))
        db.add(ReviewAssignmentBatchItem(batch_id=batch.id, assignment_id=assignment.id, work_no=1))
        for doc in revision_docs:
            db.add(ReviewAssignmentDocument(assignment_id=assignment.id, document_id=doc.id, selected_by=user.id))
        db.flush()

        contact = reviewer_contact_for_proxy(db, reviewer.id)
        reviewer_name = (' '.join(x for x in [contact.title or '', contact.first_name, contact.last_name] if x).strip() if contact else reviewer.full_name)
        reviewer_email = contact.email if contact else reviewer.email
        secure_url = f'{_base_url(request)}/secure/reviews/{token}'
        try:
            attachments, attachment_note = review_email_attachments_for_batch(db, batch)
            review_assignment_email(
                reviewer_name=reviewer_name, reviewer_email=reviewer_email, level='college', count=1,
                secure_url=secure_url, due_at=due_at, link_expires_at=batch.link_expires_at,
                message=batch.message or '', assigning_office=assigning_office,
                attachments=attachments, attachment_note=attachment_note,
            )
            batch.email_status = 'sent'
            batch.sent_at = datetime.utcnow()
            sent += 1
        except Exception as exc:
            batch.email_status = 'failed'
            batch.last_email_error = str(exc)[:1000]
            failures.append(f'{reviewer_name}: {exc}')
        audit(db, user.id, 'college_revision_reviewer_reassigned', app.id, f'{batch.reference} | previous reviewer | re-review')

    if sent == 0 and failures:
        db.rollback()
        raise HTTPException(502, 'Could not email the revised submission to the selected reviewer(s): ' + '; '.join(failures[:3]))

    transition(db, app, AppStatus.COLLEGE_REVIEW.value, user.id, 'Revised submission sent to previous scientific reviewer(s)')
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
    if decision in {'minor_revision', 'major_revision'} and gmail_configured():
        try:
            college_revision_request_email(
                applicant_name=app.applicant.full_name, applicant_email=app.applicant.email,
                reference_no=app.reference_no or '', research_title=app.title, college_name=app.college.name,
                decision=decision.replace('_', ' ').title(), comments=comments.strip(),
                application_url=f'{_base_url(request)}/applications/{app.id}'
            )
        except Exception as exc:
            audit(db, user.id, 'college_revision_email_failed', app.id, str(exc)[:500])
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
    transition(db, app, AppStatus.IRB_REVIEW.value, user.id, 'Assigned IRB ethical reviewer')
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

# ---------------------------------------------------------------------------
# Phase 2B: secure reviewer-assignment workflow, adapted from the academic
# submission portal. One batch link can contain several applications, while
# each application is declared, downloaded and reviewed separately.
# ---------------------------------------------------------------------------

def _review_queue_data(db: Session, user: User, level: str):
    if level == 'college':
        require_roles(user, Role.COLLEGE_ADMIN.value)
        statuses = [
            AppStatus.FORWARDED_TO_COLLEGE.value,
            AppStatus.AWAITING_COLLEGE_REVIEWER.value,
            AppStatus.COLLEGE_REVIEW.value,
            AppStatus.COLLEGE_REVISED.value,
        ]
        apps = db.scalars(
            select(EthicsApplication)
            .options(joinedload(EthicsApplication.applicant), joinedload(EthicsApplication.college))
            .where(EthicsApplication.college_id == user.college_id, EthicsApplication.status.in_(statuses))
            .order_by(EthicsApplication.updated_at.desc())
        ).unique().all()
        reviewers = db.scalars(
            select(User).where(
                User.role == Role.COLLEGE_REVIEWER.value,
                User.college_id == user.college_id,
                User.active == True,
            ).order_by(User.full_name)
        ).all()
        batches = db.scalars(
            select(ReviewAssignmentBatch)
            .options(joinedload(ReviewAssignmentBatch.reviewer), joinedload(ReviewAssignmentBatch.items))
            .where(ReviewAssignmentBatch.level == 'college')
            .order_by(ReviewAssignmentBatch.created_at.desc())
        ).unique().all()
        batches = [b for b in batches if b.reviewer and b.reviewer.college_id == user.college_id][:50]
    else:
        require_roles(user, Role.IRB_SECRETARIAT.value, Role.IRB_CHAIR.value, Role.SUPERADMIN.value)
        statuses = [
            AppStatus.AWAITING_IRB_REVIEWER.value,
            AppStatus.IRB_REVIEW.value,
            AppStatus.IRB_REVISED.value,
        ]
        apps = db.scalars(
            select(EthicsApplication)
            .options(joinedload(EthicsApplication.applicant), joinedload(EthicsApplication.college))
            .where(EthicsApplication.status.in_(statuses))
            .order_by(EthicsApplication.updated_at.desc())
        ).unique().all()
        reviewers = db.scalars(
            select(User).where(User.role == Role.IRB_REVIEWER.value, User.active == True).order_by(User.full_name)
        ).all()
        batches = db.scalars(
            select(ReviewAssignmentBatch)
            .options(joinedload(ReviewAssignmentBatch.reviewer), joinedload(ReviewAssignmentBatch.items))
            .where(ReviewAssignmentBatch.level == 'irb')
            .order_by(ReviewAssignmentBatch.created_at.desc())
        ).unique().all()[:50]

    counts = {a.id: assignment_count_for_application(db, a.id, level) for a in apps}
    workloads = reviewer_workload(db, reviewers)
    batch_stats = {}
    for b in batches:
        assignment_ids = [i.assignment_id for i in b.items]
        rows = db.scalars(select(ReviewerAssignment).where(ReviewerAssignment.id.in_(assignment_ids))).all() if assignment_ids else []
        batch_stats[b.id] = {
            'total': len(rows),
            'completed': sum(1 for x in rows if x.status == 'completed'),
            'declined': sum(1 for x in rows if x.status == 'declined'),
            'pending': sum(1 for x in rows if x.status in {'assigned', 'accepted'}),
        }
    return apps, reviewers, batches, counts, workloads, batch_stats


def _render_review_queue(request: Request, db: Session, user: User, level: str):
    apps, reviewers, batches, counts, workloads, batch_stats = _review_queue_data(db, user, level)
    kinds = latest_submission_kind_map(db, [a.id for a in apps])
    fresh_apps = [a for a in apps if kinds.get(a.id, 'fresh') == 'fresh']
    revised_apps = [a for a in apps if kinds.get(a.id) in {'college_revision', 'irb_revision'}]
    batch_contacts = reviewer_contact_map_for_batches(db, batches)
    saved_contacts = []
    if level == 'college':
        saved_contacts = db.scalars(select(ReviewerContact).where(
            ReviewerContact.level == 'college', ReviewerContact.college_id == user.college_id, ReviewerContact.active == True
        ).order_by(ReviewerContact.last_name, ReviewerContact.first_name)).all()
    return request.app.state.templates.TemplateResponse(
        request,
        'review_queue.html',
        ctx(
            request,
            user,
            level=level,
            apps=apps,
            fresh_apps=fresh_apps,
            revised_apps=revised_apps,
            submission_kinds=kinds,
            reviewers=reviewers,
            saved_contacts=saved_contacts,
            batches=batches,
            batch_contacts=batch_contacts,
            assignment_counts=counts,
            reviewer_workload=workloads,
            batch_stats=batch_stats,
            max_reviewers=MAX_REVIEWERS_PER_APPLICATION,
            default_due_days=REVIEW_DUE_DAYS,
            email_ready=gmail_configured(),
            review_material_options=REVIEW_MATERIAL_OPTIONS,
            default_review_materials=DEFAULT_REVIEW_MATERIALS,
            error=None,
        ),
    )


@router.get('/college/review-queue')
def college_review_queue(request: Request, db: Session = Depends(get_db)):
    user = require_user(request, db)
    return _render_review_queue(request, db, user, 'college')


@router.get('/irb/review-queue')
def irb_review_queue(request: Request, db: Session = Depends(get_db)):
    user = require_user(request, db)
    return _render_review_queue(request, db, user, 'irb')


def _base_url(request: Request) -> str:
    if PUBLIC_BASE_URL and 'localhost' not in PUBLIC_BASE_URL:
        return PUBLIC_BASE_URL.rstrip('/')
    return str(request.base_url).rstrip('/')


@router.post('/review-batches/assign')
def assign_review_batch(
    request: Request,
    level: str = Form(...),
    application_ids: list[str] = Form(...),
    reviewer_id: str = Form(''),
    reviewer_title: str = Form(''),
    reviewer_first_name: str = Form(''),
    reviewer_last_name: str = Form(''),
    reviewer_email: str = Form(''),
    reviewer_phone: str = Form(''),
    assignment_type: str = Form('primary'),
    due_days: int = Form(REVIEW_DUE_DAYS),
    message: str = Form(''),
    document_types: list[str] = Form([]),
    db: Session = Depends(get_db),
):
    user = require_user(request, db)
    if level not in {'college', 'irb'}:
        raise HTTPException(400, 'Invalid review level.')
    apps, reviewers, _, _, _, _ = _review_queue_data(db, user, level)
    contact = None
    if level == 'college':
        contact, reviewer = get_or_create_reviewer_contact(
            db, user, level='college', title=reviewer_title, first_name=reviewer_first_name,
            last_name=reviewer_last_name, email=reviewer_email, phone=reviewer_phone
        )
    else:
        reviewer = next((r for r in reviewers if r.id == reviewer_id), None)
        if not reviewer:
            raise HTTPException(400, 'Select an active IRB reviewer available to this review level.')

    ids = list(dict.fromkeys(application_ids))
    if not ids:
        raise HTTPException(400, 'Select at least one application.')
    eligible = {a.id: a for a in apps}
    selected = [eligible.get(i) for i in ids]
    if any(a is None for a in selected):
        raise HTTPException(400, 'One or more selected applications are not available in this review queue.')

    problems = []
    for app in selected:
        if active_duplicate_assignment(db, app.id, reviewer.id, level):
            problems.append(f'{app.reference_no}: reviewer already assigned in this review round')
        elif assignment_count_for_application(db, app.id, level) >= MAX_REVIEWERS_PER_APPLICATION:
            problems.append(f'{app.reference_no}: maximum of {MAX_REVIEWERS_PER_APPLICATION} reviewers reached')
    if problems:
        raise HTTPException(400, '; '.join(problems[:10]))

    due_days = max(1, min(int(due_days), 90))
    now = datetime.utcnow()
    due_at = now + timedelta(days=due_days)
    link_days = max(REVIEW_ASSIGNMENT_LINK_EXPIRY_DAYS, due_days + 7)
    link_expires_at = now + timedelta(days=link_days)
    token = new_review_token()
    batch = ReviewAssignmentBatch(
        reference=next_review_batch_reference(db, level),
        reviewer_id=reviewer.id,
        level=level,
        token_hash=review_token_hash(token),
        link_expires_at=link_expires_at,
        due_at=due_at,
        message=message.strip()[:4000] or None,
        email_status='pending',
        created_by=user.id,
    )
    db.add(batch)
    db.flush()

    for idx, app in enumerate(selected, start=1):
        assignment = ReviewerAssignment(
            application_id=app.id,
            reviewer_id=reviewer.id,
            level=level,
            assignment_type=assignment_type,
            status='assigned',
            assigned_by=user.id,
        )
        db.add(assignment)
        db.flush()
        db.add(ReviewAssignmentMeta(assignment_id=assignment.id, due_at=due_at))
        db.add(ReviewAssignmentBatchItem(batch_id=batch.id, assignment_id=assignment.id, work_no=idx))
        selected_docs = latest_documents_for_types(db, app.id, document_types or None)
        for selected_doc in selected_docs:
            db.add(ReviewAssignmentDocument(
                assignment_id=assignment.id, document_id=selected_doc.id, selected_by=user.id
            ))
        if level == 'college':
            if app.status != AppStatus.COLLEGE_REVIEW.value:
                transition(db, app, AppStatus.COLLEGE_REVIEW.value, user.id, 'Assigned scientific reviewer')
        else:
            if app.status != AppStatus.IRB_REVIEW.value:
                transition(db, app, AppStatus.IRB_REVIEW.value, user.id, 'Assigned IRB ethical reviewer')
        audit(db, user.id, 'reviewer_assignment_created', app.id, f'{batch.reference} | {reviewer.full_name} | {level}')

    db.commit()
    secure_url = f'{_base_url(request)}/secure/reviews/{token}'
    delivery_message = None
    try:
        if not gmail_configured():
            batch.email_status = 'not_configured'
            delivery_message = 'Email is not configured. Copy the secure link shown below and send it to the reviewer through an approved institutional channel.'
        else:
            delivery_name = f"{contact.title + ' ' if contact and contact.title else ''}{contact.first_name} {contact.last_name}".strip() if contact else reviewer.full_name
            delivery_email = contact.email if contact else reviewer.email
            assigning_office = user.college.name + ' Scientific Committee' if level == 'college' and user.college else 'Institutional Review Board Secretariat'
            attachments, attachment_note = review_email_attachments_for_batch(db, batch)
            review_assignment_email(
                reviewer_name=delivery_name,
                reviewer_email=delivery_email,
                level=level,
                count=len(selected),
                secure_url=secure_url,
                due_at=due_at,
                link_expires_at=link_expires_at,
                message=message.strip(),
                assigning_office=assigning_office,
                attachments=attachments,
                attachment_note=attachment_note,
            )
            batch.email_status = 'sent'
            batch.sent_at = datetime.utcnow()
    except Exception as exc:
        batch.email_status = 'failed'
        batch.last_email_error = str(exc)[:1000]
        delivery_message = f'The assignments were saved, but email delivery failed: {exc}. Copy the secure link below and send it through an approved institutional channel.'
    db.commit()

    return _render_review_batch_detail(request, db, user, batch, secure_url=secure_url if batch.email_status != 'sent' else None, notice=delivery_message or 'Assignment created and secure review invitation sent.')


def _get_batch_or_404(db: Session, batch_id: str):
    batch = db.scalar(
        select(ReviewAssignmentBatch)
        .options(joinedload(ReviewAssignmentBatch.reviewer), joinedload(ReviewAssignmentBatch.items))
        .where(ReviewAssignmentBatch.id == batch_id)
    )
    if not batch:
        raise HTTPException(404, 'Review assignment batch not found.')
    return batch


def _can_manage_batch(user: User, batch: ReviewAssignmentBatch) -> bool:
    if user.role == Role.SUPERADMIN.value:
        return True
    if batch.level == 'irb':
        return user.role in {Role.IRB_SECRETARIAT.value, Role.IRB_CHAIR.value}
    return user.role == Role.COLLEGE_ADMIN.value and batch.reviewer and user.college_id == batch.reviewer.college_id


def _render_review_batch_detail(request: Request, db: Session, user: User, batch: ReviewAssignmentBatch,
                                secure_url: str | None = None, notice: str | None = None):
    if not _can_manage_batch(user, batch):
        raise HTTPException(403)
    items = db.scalars(
        select(ReviewAssignmentBatchItem)
        .options(
            joinedload(ReviewAssignmentBatchItem.assignment).joinedload(ReviewerAssignment.application).joinedload(EthicsApplication.applicant)
        )
        .where(ReviewAssignmentBatchItem.batch_id == batch.id)
        .order_by(ReviewAssignmentBatchItem.work_no)
    ).unique().all()
    declarations = declaration_map(db, [i.assignment for i in items])
    reports = report_documents_map(db, [i.assignment for i in items])
    assignment_documents = review_assignment_documents_map(db, [i.assignment for i in items])
    return request.app.state.templates.TemplateResponse(
        request,
        'review_batch_detail.html',
        ctx(request, user, batch=batch, review_contact=reviewer_contact_for_proxy(db, batch.reviewer_id), items=items, declarations=declarations, reports=reports, assignment_documents=assignment_documents, secure_url=secure_url, notice=notice, now=datetime.utcnow()),
    )


@router.get('/review-batches/{batch_id}')
def review_batch_detail(request: Request, batch_id: str, db: Session = Depends(get_db)):
    user = require_user(request, db)
    batch = _get_batch_or_404(db, batch_id)
    return _render_review_batch_detail(request, db, user, batch)


@router.post('/review-batches/{batch_id}/resend')
def resend_review_batch(request: Request, batch_id: str, db: Session = Depends(get_db)):
    user = require_user(request, db)
    batch = _get_batch_or_404(db, batch_id)
    if not _can_manage_batch(user, batch):
        raise HTTPException(403)
    if batch.revoked_at:
        raise HTTPException(400, 'A revoked assignment cannot be resent. Create a new assignment instead.')
    if not gmail_configured():
        raise HTTPException(503, 'Gmail delivery is not configured.')

    token = new_review_token()
    batch.token_hash = review_token_hash(token)
    batch.link_expires_at = datetime.utcnow() + timedelta(days=max(REVIEW_ASSIGNMENT_LINK_EXPIRY_DAYS, 7))
    secure_url = f'{_base_url(request)}/secure/reviews/{token}'
    try:
        contact = reviewer_contact_for_proxy(db, batch.reviewer_id)
        reviewer_name = (f"{contact.title + ' ' if contact and contact.title else ''}{contact.first_name} {contact.last_name}".strip() if contact else batch.reviewer.full_name)
        reviewer_email = contact.email if contact else batch.reviewer.email
        assigning_office = (user.college.name + ' Scientific Committee') if batch.level == 'college' and user.college else 'Institutional Review Board Secretariat'
        attachments, attachment_note = review_email_attachments_for_batch(db, batch)
        review_assignment_email(
            reviewer_name=reviewer_name,
            reviewer_email=reviewer_email,
            level=batch.level,
            count=len(batch.items),
            secure_url=secure_url,
            due_at=batch.due_at,
            link_expires_at=batch.link_expires_at,
            message=batch.message or '',
            assigning_office=assigning_office,
            attachments=attachments,
            attachment_note=attachment_note,
        )
        batch.email_status = 'sent'
        batch.sent_at = datetime.utcnow()
        batch.resend_count += 1
        batch.last_email_error = None
        audit(db, user.id, 'review_assignment_link_resent', None, batch.reference)
        db.commit()
        return RedirectResponse(f'/review-batches/{batch.id}', status_code=303)
    except Exception as exc:
        batch.email_status = 'failed'
        batch.last_email_error = str(exc)[:1000]
        db.commit()
        return _render_review_batch_detail(request, db, user, batch, secure_url=secure_url, notice=f'Email delivery failed: {exc}. The regenerated secure link is shown below.')


@router.post('/review-batches/{batch_id}/revoke')
def revoke_review_batch(request: Request, batch_id: str, db: Session = Depends(get_db)):
    user = require_user(request, db)
    batch = _get_batch_or_404(db, batch_id)
    if not _can_manage_batch(user, batch):
        raise HTTPException(403)
    if batch.revoked_at:
        return RedirectResponse(f'/review-batches/{batch.id}', status_code=303)
    batch.revoked_at = datetime.utcnow()
    batch.email_status = 'revoked'
    items = db.scalars(select(ReviewAssignmentBatchItem).where(ReviewAssignmentBatchItem.batch_id == batch.id)).all()
    affected_apps = set()
    for item in items:
        assignment = db.get(ReviewerAssignment, item.assignment_id)
        if assignment and assignment.status in {'assigned', 'accepted'}:
            assignment.status = 'revoked'
            affected_apps.add((assignment.application_id, assignment.level))
    db.flush()
    for app_id, level in affected_apps:
        app = get_app_or_404(db, app_id)
        remaining = db.scalar(
            select(func.count(ReviewerAssignment.id)).where(
                ReviewerAssignment.application_id == app_id,
                ReviewerAssignment.level == level,
                ReviewerAssignment.status.in_(['assigned', 'accepted']),
            )
        ) or 0
        if remaining == 0:
            target = AppStatus.AWAITING_COLLEGE_REVIEWER.value if level == 'college' else AppStatus.AWAITING_IRB_REVIEWER.value
            transition(db, app, target, user.id, f'{batch.reference} revoked; reassignment required')
    audit(db, user.id, 'review_assignment_batch_revoked', None, batch.reference)
    db.commit()
    return RedirectResponse(f'/review-batches/{batch.id}', status_code=303)


def _batch_item_assignment(db: Session, batch: ReviewAssignmentBatch, assignment_id: str):
    item = db.scalar(
        select(ReviewAssignmentBatchItem).where(
            ReviewAssignmentBatchItem.batch_id == batch.id,
            ReviewAssignmentBatchItem.assignment_id == assignment_id,
        )
    )
    if not item:
        raise HTTPException(403, 'This application is not part of the secure assignment.')
    assignment = db.scalar(
        select(ReviewerAssignment)
        .options(joinedload(ReviewerAssignment.application).joinedload(EthicsApplication.applicant), joinedload(ReviewerAssignment.application).joinedload(EthicsApplication.college))
        .where(ReviewerAssignment.id == assignment_id)
    )
    if not assignment:
        raise HTTPException(404, 'Assigned review not found.')
    return item, assignment


@router.get('/secure/reviews/{token}')
def secure_review_workspace(request: Request, token: str, db: Session = Depends(get_db)):
    batch = get_review_batch_by_token(db, token)
    ok, status, message = validate_review_batch(batch)
    if not ok:
        return request.app.state.templates.TemplateResponse(request, 'secure_review_workspace.html', {'batch': None, 'error': message, 'request': request}, status_code=status)
    batch.last_accessed_at = datetime.utcnow()
    batch.access_count += 1
    items = db.scalars(
        select(ReviewAssignmentBatchItem)
        .options(
            joinedload(ReviewAssignmentBatchItem.assignment).joinedload(ReviewerAssignment.application).joinedload(EthicsApplication.applicant),
            joinedload(ReviewAssignmentBatchItem.assignment).joinedload(ReviewerAssignment.application).joinedload(EthicsApplication.college),
        )
        .where(ReviewAssignmentBatchItem.batch_id == batch.id)
        .order_by(ReviewAssignmentBatchItem.work_no)
    ).unique().all()
    assignments = [i.assignment for i in items]
    declarations = declaration_map(db, assignments)
    reports = report_documents_map(db, assignments)
    assignment_documents = review_assignment_documents_map(db, assignments)
    db.commit()
    return request.app.state.templates.TemplateResponse(
        request,
        'secure_review_workspace.html',
        {
            'request': request,
            'batch': batch,
            'review_contact': reviewer_contact_for_proxy(db, batch.reviewer_id),
            'items': items,
            'declarations': declarations,
            'reports': reports,
            'assignment_documents': assignment_documents,
            'token': token,
            'error': None,
            'now': datetime.utcnow(),
        },
        headers={'Cache-Control': 'no-store', 'Referrer-Policy': 'no-referrer', 'X-Robots-Tag': 'noindex, nofollow'},
    )


@router.post('/secure/reviews/{token}/items/{assignment_id}/declaration')
def secure_review_declaration(
    request: Request,
    token: str,
    assignment_id: str,
    declaration: str = Form(...),
    note: str = Form(''),
    db: Session = Depends(get_db),
):
    batch = get_review_batch_by_token(db, token)
    ok, status, message = validate_review_batch(batch)
    if not ok:
        raise HTTPException(status, message)
    _, assignment = _batch_item_assignment(db, batch, assignment_id)
    if assignment.status not in {'assigned', 'accepted'}:
        raise HTTPException(409, 'This assigned review is no longer awaiting a declaration.')
    if declaration not in {'clear', 'conflict'}:
        raise HTTPException(400, 'Choose a valid conflict-of-interest declaration.')
    existing = db.scalar(select(ReviewerDeclaration).where(ReviewerDeclaration.assignment_id == assignment.id))
    if existing:
        raise HTTPException(409, 'A conflict-of-interest declaration has already been recorded for this application.')
    db.add(ReviewerDeclaration(assignment_id=assignment.id, declaration=declaration, note=note.strip() or None))
    app = assignment.application
    if declaration == 'clear':
        assignment.status = 'accepted'
        audit(db, batch.reviewer_id, 'reviewer_conflict_declaration_clear', app.id, f'Secure batch {batch.reference}')
    else:
        assignment.status = 'declined'
        audit(db, batch.reviewer_id, 'reviewer_conflict_declared', app.id, note.strip() or f'Secure batch {batch.reference}')
        db.flush()
        remaining = db.scalar(
            select(func.count(ReviewerAssignment.id)).where(
                ReviewerAssignment.application_id == app.id,
                ReviewerAssignment.level == assignment.level,
                ReviewerAssignment.status.in_(['assigned', 'accepted']),
            )
        ) or 0
        if remaining == 0:
            target = AppStatus.AWAITING_COLLEGE_REVIEWER.value if assignment.level == 'college' else AppStatus.AWAITING_IRB_REVIEWER.value
            transition(db, app, target, batch.reviewer_id, 'Reviewer declared a conflict; reassignment required')
    db.commit()
    return RedirectResponse(f'/secure/reviews/{token}', status_code=303)


def _safe_zip_name(value: str) -> str:
    cleaned = ''.join(c if c.isalnum() or c in ' ._-()' else '_' for c in (value or '')).strip()
    return cleaned[:160] or 'document'


@router.get('/secure/reviews/{token}/items/{assignment_id}/package')
def secure_review_package(token: str, assignment_id: str, db: Session = Depends(get_db)):
    batch = get_review_batch_by_token(db, token)
    ok, status, message = validate_review_batch(batch)
    if not ok:
        raise HTTPException(status, message)
    _, assignment = _batch_item_assignment(db, batch, assignment_id)
    declaration = db.scalar(select(ReviewerDeclaration).where(ReviewerDeclaration.assignment_id == assignment.id))
    if not declaration or declaration.declaration != 'clear':
        raise HTTPException(403, 'Complete a no-conflict declaration before accessing research documents.')

    app = assignment.application
    selected_docs = selected_review_documents(db, assignment.id)
    if selected_docs:
        package_docs = selected_docs
    else:
        package_docs = latest_documents_for_types(db, app.id)

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, 'w', zipfile.ZIP_DEFLATED) as zf:
        summary = (
            f'Application reference: {app.reference_no}\n'
            f'Applicant: {app.applicant.full_name}\n'
            f'Research title: {app.title}\n'
            f'Affiliation: {app.college.name}\n'
            f'Department/Unit: {app.department or "Not stated"}\n'
            f'Review level: {assignment.level}\n'
        )
        zf.writestr('00 - Application Summary.txt', summary)
        for idx, doc in enumerate(package_docs, start=1):
            path = storage_path(app.id, doc.stored_name)
            if path.exists():
                suffix = path.suffix or ''
                label = _safe_zip_name(doc.document_type)
                original_stem = _safe_zip_name(doc.original_name.rsplit('.', 1)[0])
                zf.write(path, f'{idx:02d} - {label} - {original_stem}{suffix}')
    buffer.seek(0)
    headers = {
        'Content-Disposition': f'attachment; filename="{_safe_zip_name(app.reference_no or app.id)}-review-package.zip"',
        'Cache-Control': 'no-store',
        'Referrer-Policy': 'no-referrer',
    }
    batch.last_accessed_at = datetime.utcnow()
    batch.access_count += 1
    db.commit()
    return StreamingResponse(buffer, media_type='application/zip', headers=headers)


@router.post('/secure/reviews/{token}/items/{assignment_id}/submit')
def secure_review_submit(
    token: str,
    assignment_id: str,
    recommendation: str = Form(...),
    comments: str = Form(...),
    report_file: UploadFile = File(...),
    annotated_protocol: UploadFile | None = File(None),
    supporting_file: UploadFile | None = File(None),
    db: Session = Depends(get_db),
):
    batch = get_review_batch_by_token(db, token)
    ok, status, message = validate_review_batch(batch)
    if not ok:
        raise HTTPException(status, message)
    _, assignment = _batch_item_assignment(db, batch, assignment_id)
    if assignment.status not in {'assigned', 'accepted'}:
        raise HTTPException(409, 'A review for this application has already been submitted or the assignment is no longer active.')
    declaration = db.scalar(select(ReviewerDeclaration).where(ReviewerDeclaration.assignment_id == assignment.id))
    if not declaration or declaration.declaration != 'clear':
        raise HTTPException(403, 'Complete the no-conflict declaration before submitting a review.')
    allowed = (
        {'scientifically_recommended', 'minor_revision', 'major_revision', 'specialist_review', 'not_recommended'}
        if assignment.level == 'college'
        else {'approve', 'approve_conditions', 'minor_revision', 'major_revision', 'full_board', 'reject'}
    )
    if recommendation not in allowed:
        raise HTTPException(400, 'Select a valid review recommendation.')
    if not comments.strip():
        raise HTTPException(400, 'Review comments are required.')

    app = assignment.application
    uploads = [
        ('review_report', report_file),
        ('annotated_protocol', annotated_protocol),
        ('supporting', supporting_file),
    ]
    for kind, upload in uploads:
        if not upload or not upload.filename:
            continue
        stored, original = save_upload(upload, app.id)
        db.add(ReviewReportDocument(
            assignment_id=assignment.id,
            document_kind=kind,
            original_name=original,
            stored_name=stored,
        ))
    complete_review_assignment(db, assignment, batch.reviewer_id, recommendation, comments.strip())
    db.commit()
    return RedirectResponse(f'/secure/reviews/{token}', status_code=303)


@router.get('/review-reports/{document_id}/download')
def download_review_report(request: Request, document_id: str, db: Session = Depends(get_db)):
    user = require_user(request, db)
    doc = db.get(ReviewReportDocument, document_id)
    if not doc:
        raise HTTPException(404, 'Review report file not found.')
    assignment = get_assignment_or_404(db, doc.assignment_id)
    app = assignment.application
    allowed = False
    if user.role in {Role.SUPERADMIN.value, Role.IRB_SECRETARIAT.value, Role.IRB_CHAIR.value}:
        allowed = True
    elif user.role == Role.COLLEGE_ADMIN.value and assignment.level == 'college' and user.college_id == app.college_id:
        allowed = True
    elif user.id == assignment.reviewer_id:
        allowed = True
    elif user.id == app.applicant_id and doc.document_kind == 'review_report' and assignment.status == 'completed':
        allowed = True
    if not allowed:
        raise HTTPException(403)
    path = storage_path(app.id, doc.stored_name)
    if not path.exists():
        raise HTTPException(404, 'Review report file is unavailable.')
    filename = doc.original_name
    if user.id == app.applicant_id:
        suffix = path.suffix or ''
        label = 'Scientific-Review-Report' if assignment.level == 'college' else 'IRB-Review-Report'
        filename = f'{app.reference_no or "UCC-IRB"}-{label}{suffix}'
    return FileResponse(path, filename=filename, headers={'Cache-Control': 'no-store'})
