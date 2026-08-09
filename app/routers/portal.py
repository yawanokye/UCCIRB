from datetime import datetime
from pathlib import Path
import secrets
import string
import uuid
from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, RedirectResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session, joinedload

from ..database import get_db
from ..models import (AppStatus, ApplicationDocument, College, CollegeAccessRequest,
                      EthicsApplication, ReviewerAssignment, Role, User)
from ..services.auth import hash_password, require_roles, require_user
from ..services.storage import save_upload, storage_path
from ..services.workflow import audit, transition

router = APIRouter()


def ctx(request, user, **kwargs):
    return {'request': request, 'user': user, **kwargs}


def get_app_or_404(db: Session, app_id: str):
    app = db.scalar(select(EthicsApplication).options(joinedload(EthicsApplication.applicant), joinedload(EthicsApplication.college)).where(EthicsApplication.id == app_id))
    if not app:
        raise HTTPException(404, 'Application not found')
    return app


def can_view_metadata(user: User, app: EthicsApplication) -> bool:
    if user.role in {Role.SUPERADMIN.value, Role.IRB_SECRETARIAT.value, Role.IRB_CHAIR.value, Role.IRB_REVIEWER.value}:
        return True
    if user.role in {Role.COLLEGE_ADMIN.value, Role.COLLEGE_REVIEWER.value} and user.college_id == app.college_id:
        return True
    return user.id == app.applicant_id


def can_view_documents(db: Session, user: User, app: EthicsApplication) -> bool:
    if user.id == app.applicant_id or user.role in {Role.SUPERADMIN.value, Role.IRB_SECRETARIAT.value, Role.IRB_CHAIR.value}:
        return True
    if user.role == Role.COLLEGE_ADMIN.value and user.college_id == app.college_id:
        return app.status in {AppStatus.COLLEGE_REVIEW_AUTHORISED.value, AppStatus.AWAITING_COLLEGE_REVIEWER.value, AppStatus.COLLEGE_REVIEW.value, AppStatus.COLLEGE_REVISION.value, AppStatus.COLLEGE_REVISED.value, AppStatus.AWAITING_COLLEGE_DECISION.value, AppStatus.SCIENTIFICALLY_RECOMMENDED.value, AppStatus.RETURNED_TO_IRB.value}
    if user.role in {Role.COLLEGE_REVIEWER.value, Role.IRB_REVIEWER.value}:
        assigned = db.scalar(select(ReviewerAssignment).where(ReviewerAssignment.application_id == app.id, ReviewerAssignment.reviewer_id == user.id))
        return assigned is not None
    return False

@router.get('/')
def home(request: Request):
    return request.app.state.templates.TemplateResponse(request, 'home.html', {})

@router.get('/dashboard')
def dashboard(request: Request, db: Session = Depends(get_db)):
    user = require_user(request, db)
    if user.role == Role.APPLICANT.value:
        apps = db.scalars(select(EthicsApplication).where(EthicsApplication.applicant_id == user.id).order_by(EthicsApplication.created_at.desc())).all()
        return request.app.state.templates.TemplateResponse(request, 'dashboard_applicant.html', ctx(request, user, apps=apps))
    if user.role == Role.COLLEGE_ADMIN.value:
        apps = db.scalars(select(EthicsApplication).where(EthicsApplication.college_id == user.college_id, EthicsApplication.status != AppStatus.DRAFT.value).order_by(EthicsApplication.updated_at.desc())).all()
        counts = {s: sum(1 for a in apps if a.status == s) for s in set(a.status for a in apps)}
        return request.app.state.templates.TemplateResponse(request, 'dashboard_college.html', ctx(request, user, apps=apps, counts=counts))
    if user.role == Role.COLLEGE_REVIEWER.value:
        assignments = db.scalars(select(ReviewerAssignment).options(joinedload(ReviewerAssignment.application)).where(ReviewerAssignment.reviewer_id == user.id).order_by(ReviewerAssignment.assigned_at.desc())).all()
        return request.app.state.templates.TemplateResponse(request, 'dashboard_reviewer.html', ctx(request, user, assignments=assignments))
    if user.role == Role.SUPERADMIN.value:
        users = db.scalars(select(User).options(joinedload(User.college)).order_by(User.created_at.desc())).all()
        colleges = db.scalars(select(College).where(College.active == True).order_by(College.name)).all()
        admin_users = [u for u in users if u.role != Role.APPLICANT.value]
        applicant_count = sum(1 for u in users if u.role == Role.APPLICANT.value)
        return request.app.state.templates.TemplateResponse(
            request, 'dashboard_admin.html',
            ctx(request, user, users=admin_users, colleges=colleges, applicant_count=applicant_count, created_password=None, created_email=None, error=None)
        )
    if user.role in {Role.IRB_SECRETARIAT.value, Role.IRB_CHAIR.value}:
        apps = db.scalars(select(EthicsApplication).where(EthicsApplication.status != AppStatus.DRAFT.value).order_by(EthicsApplication.updated_at.desc())).all()
        pending_access = db.scalars(select(CollegeAccessRequest).options(joinedload(CollegeAccessRequest.application)).where(CollegeAccessRequest.status == 'pending').order_by(CollegeAccessRequest.requested_at)).all()
        return request.app.state.templates.TemplateResponse(request, 'dashboard_secretariat.html', ctx(request, user, apps=apps, pending_access=pending_access))
    return request.app.state.templates.TemplateResponse(request, 'dashboard_reviewer.html', ctx(request, user, assignments=[]))

def generate_temporary_password(length: int = 14) -> str:
    alphabet = string.ascii_letters + string.digits + '!@#$%?'
    # Guarantee at least one upper, lower and digit, then shuffle.
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
    colleges = db.scalars(select(College).where(College.active == True).order_by(College.name)).all()

    def render_error(message: str):
        admin_users = [u for u in users if u.role != Role.APPLICANT.value]
        applicant_count = sum(1 for u in users if u.role == Role.APPLICANT.value)
        return request.app.state.templates.TemplateResponse(
            request, 'dashboard_admin.html',
            ctx(request, admin, users=admin_users, colleges=colleges, applicant_count=applicant_count, created_password=None, created_email=None, error=message),
            status_code=400,
        )

    if role not in allowed_roles:
        return render_error('Select a valid administrative role.')
    if len(full_name) < 3 or '@' not in email:
        return render_error('Enter the officer\'s full name and a valid email address.')
    if db.scalar(select(User).where(User.email == email)):
        return render_error('An account already exists with this email address.')
    if role in {Role.COLLEGE_ADMIN.value, Role.COLLEGE_REVIEWER.value}:
        if not college_id or not db.get(College, college_id):
            return render_error('A College must be assigned to College Scientific Committee users.')
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
        request, 'dashboard_admin.html',
        ctx(
            request, admin, users=admin_users, colleges=colleges, applicant_count=applicant_count,
            created_password=password if generated else '(password set by administrator)',
            created_email=email, error=None
        )
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
    user = require_user(request, db); require_roles(user, Role.APPLICANT.value)
    colleges = db.scalars(select(College).where(College.active == True).order_by(College.name)).all()
    return request.app.state.templates.TemplateResponse(request, 'application_new.html', ctx(request, user, colleges=colleges))

@router.post('/applications/new')
def new_application(request: Request, title: str = Form(...), college_id: str = Form(...), department: str = Form(''), programme: str = Form(''), applicant_type: str = Form('Student'), study_summary: str = Form(''), db: Session = Depends(get_db)):
    user = require_user(request, db); require_roles(user, Role.APPLICANT.value)
    app = EthicsApplication(applicant_id=user.id, college_id=college_id, title=title.strip(), department=department.strip() or None, programme=programme.strip() or None, applicant_type=applicant_type, study_summary=study_summary.strip() or None)
    db.add(app); db.flush(); audit(db, user.id, 'application_created', app.id, app.title); db.commit()
    return RedirectResponse(f'/applications/{app.id}', status_code=303)

@router.get('/applications/{app_id}')
def application_detail(request: Request, app_id: str, db: Session = Depends(get_db)):
    user = require_user(request, db); app = get_app_or_404(db, app_id)
    if not can_view_metadata(user, app): raise HTTPException(403)
    documents = db.scalars(select(ApplicationDocument).where(ApplicationDocument.application_id == app.id).order_by(ApplicationDocument.document_type, ApplicationDocument.version.desc())).all() if can_view_documents(db,user,app) else []
    access = db.scalars(select(CollegeAccessRequest).where(CollegeAccessRequest.application_id == app.id).order_by(CollegeAccessRequest.requested_at.desc())).all()
    assignments = db.scalars(select(ReviewerAssignment).options(joinedload(ReviewerAssignment.reviewer)).where(ReviewerAssignment.application_id == app.id)).all()
    reviewers = []
    if user.role == Role.COLLEGE_ADMIN.value and user.college_id == app.college_id:
        reviewers = db.scalars(select(User).where(User.college_id == app.college_id, User.role == Role.COLLEGE_REVIEWER.value, User.active == True).order_by(User.full_name)).all()
    return request.app.state.templates.TemplateResponse(request, 'application_detail.html', ctx(request,user,app=app,documents=documents,access_requests=access,assignments=assignments,reviewers=reviewers,can_docs=can_view_documents(db,user,app)))

@router.post('/applications/{app_id}/documents')
def upload_document(request: Request, app_id: str, document_type: str = Form(...), file: UploadFile = File(...), db: Session = Depends(get_db)):
    user = require_user(request, db); app = get_app_or_404(db, app_id)
    if user.id != app.applicant_id: raise HTTPException(403)
    if app.status not in {AppStatus.DRAFT.value, AppStatus.RETURNED_ADMIN.value, AppStatus.COLLEGE_REVISION.value, AppStatus.IRB_REVISION.value}: raise HTTPException(400, 'Application is locked for editing')
    stored, original = save_upload(file, app.id)
    maxver = db.scalar(select(func.max(ApplicationDocument.version)).where(ApplicationDocument.application_id == app.id, ApplicationDocument.document_type == document_type)) or 0
    doc = ApplicationDocument(application_id=app.id, document_type=document_type, original_name=original, stored_name=stored, version=maxver+1, uploaded_by=user.id)
    db.add(doc); audit(db,user.id,'document_uploaded',app.id,f'{document_type} v{doc.version}: {original}'); db.commit()
    return RedirectResponse(f'/applications/{app.id}', status_code=303)

@router.post('/applications/{app_id}/submit')
def submit_application(request: Request, app_id: str, db: Session = Depends(get_db)):
    user=require_user(request,db); app=get_app_or_404(db,app_id)
    if user.id != app.applicant_id: raise HTTPException(403)
    if app.status not in {AppStatus.DRAFT.value, AppStatus.RETURNED_ADMIN.value}: raise HTTPException(400,'Application cannot be submitted from its current state')
    docs=db.scalars(select(ApplicationDocument).where(ApplicationDocument.application_id==app.id)).all()
    required={'Research Protocol','Data Collection Instrument','Supervisor Approval'} if app.applicant_type.lower()=='student' else {'Research Protocol','Data Collection Instrument'}
    present={d.document_type for d in docs}
    missing=required-present
    if missing: raise HTTPException(400, f'Missing required document(s): {", ".join(sorted(missing))}')
    if not app.reference_no:
        seq=(db.scalar(select(func.count(EthicsApplication.id)).where(EthicsApplication.submitted_at.is_not(None))) or 0)+1
        app.reference_no=f'UCC-IRB-{datetime.utcnow().year}-{seq:05d}'
    app.submitted_at=datetime.utcnow(); transition(db,app,AppStatus.SUBMITTED.value,user.id,'Submitted centrally to IRB Secretariat'); db.commit()
    return RedirectResponse(f'/applications/{app.id}',status_code=303)

@router.post('/secretariat/{app_id}/screen')
def secretariat_screen(request: Request, app_id: str, outcome: str = Form(...), note: str = Form(''), db: Session = Depends(get_db)):
    user=require_user(request,db); require_roles(user,Role.IRB_SECRETARIAT.value,Role.IRB_CHAIR.value,Role.SUPERADMIN.value)
    app=get_app_or_404(db,app_id)
    if outcome=='complete':
        transition(db,app,AppStatus.ADMIN_COMPLETE.value,user.id,note or 'Administrative screening complete')
        transition(db,app,AppStatus.VISIBLE_TO_COLLEGE.value,user.id,'Metadata visible to relevant College Scientific Committee')
    elif outcome=='return':
        app.secretariat_note=note; transition(db,app,AppStatus.RETURNED_ADMIN.value,user.id,note or 'Returned for administrative correction')
    else: raise HTTPException(400,'Unknown screening outcome')
    db.commit(); return RedirectResponse(f'/applications/{app.id}',status_code=303)

@router.post('/college/{app_id}/request-access')
def request_access(request: Request, app_id: str, note: str=Form(''), db: Session=Depends(get_db)):
    user=require_user(request,db); require_roles(user,Role.COLLEGE_ADMIN.value)
    app=get_app_or_404(db,app_id)
    if user.college_id != app.college_id: raise HTTPException(403)
    if app.status != AppStatus.VISIBLE_TO_COLLEGE.value: raise HTTPException(400,'Application is not available for access request')
    req=CollegeAccessRequest(application_id=app.id,requested_by=user.id,request_note=note or None)
    db.add(req); transition(db,app,AppStatus.COLLEGE_ACCESS_REQUESTED.value,user.id,'College requested permission to commence scientific review'); db.commit()
    return RedirectResponse(f'/applications/{app.id}',status_code=303)

@router.post('/secretariat/access/{request_id}/decide')
def decide_access(request: Request, request_id: str, decision: str=Form(...), note: str=Form(''), db: Session=Depends(get_db)):
    user=require_user(request,db); require_roles(user,Role.IRB_SECRETARIAT.value,Role.IRB_CHAIR.value,Role.SUPERADMIN.value)
    req=db.get(CollegeAccessRequest,request_id)
    if not req or req.status!='pending': raise HTTPException(404,'Pending access request not found')
    app=get_app_or_404(db,req.application_id); req.decided_by=user.id; req.decided_at=datetime.utcnow(); req.decision_note=note or None
    if decision=='grant':
        req.status='granted'
        for doc in db.scalars(select(ApplicationDocument).where(ApplicationDocument.application_id==app.id)).all(): doc.active_for_college=True
        transition(db,app,AppStatus.COLLEGE_REVIEW_AUTHORISED.value,user.id,note or 'College document access activated')
        transition(db,app,AppStatus.AWAITING_COLLEGE_REVIEWER.value,user.id,'Awaiting College reviewer assignment')
    elif decision=='withhold':
        req.status='withheld'; transition(db,app,AppStatus.VISIBLE_TO_COLLEGE.value,user.id,note or 'College access withheld')
    else: raise HTTPException(400,'Unknown decision')
    audit(db,user.id,'college_access_decision',app.id,f'{decision}: {note}'); db.commit()
    return RedirectResponse(f'/applications/{app.id}',status_code=303)

@router.post('/college/{app_id}/assign-reviewer')
def assign_college_reviewer(request: Request, app_id: str, reviewer_id: str=Form(...), assignment_type: str=Form('primary'), db: Session=Depends(get_db)):
    user=require_user(request,db); require_roles(user,Role.COLLEGE_ADMIN.value)
    app=get_app_or_404(db,app_id)
    if user.college_id != app.college_id or app.status not in {AppStatus.AWAITING_COLLEGE_REVIEWER.value,AppStatus.COLLEGE_REVIEW.value}: raise HTTPException(403)
    reviewer=db.get(User,reviewer_id)
    if not reviewer or reviewer.role!=Role.COLLEGE_REVIEWER.value or reviewer.college_id!=app.college_id: raise HTTPException(400,'Invalid College reviewer')
    db.add(ReviewerAssignment(application_id=app.id,reviewer_id=reviewer.id,level='college',assignment_type=assignment_type,assigned_by=user.id))
    transition(db,app,AppStatus.COLLEGE_REVIEW.value,user.id,f'Assigned to {reviewer.full_name}'); db.commit()
    return RedirectResponse(f'/applications/{app.id}',status_code=303)

@router.post('/review/{assignment_id}/submit')
def submit_review(request: Request, assignment_id: str, recommendation: str=Form(...), comments: str=Form(...), db: Session=Depends(get_db)):
    user=require_user(request,db); assignment=db.get(ReviewerAssignment,assignment_id)
    if not assignment or assignment.reviewer_id!=user.id: raise HTTPException(403)
    assignment.recommendation=recommendation; assignment.comments=comments; assignment.status='completed'; assignment.completed_at=datetime.utcnow()
    app=get_app_or_404(db,assignment.application_id)
    if assignment.level=='college': transition(db,app,AppStatus.AWAITING_COLLEGE_DECISION.value,user.id,'Scientific review submitted')
    audit(db,user.id,'review_submitted',app.id,recommendation); db.commit()
    return RedirectResponse(f'/applications/{app.id}',status_code=303)

@router.get('/documents/{document_id}/download')
def download_document(request: Request, document_id: str, db: Session=Depends(get_db)):
    user=require_user(request,db); doc=db.get(ApplicationDocument,document_id)
    if not doc: raise HTTPException(404)
    app=get_app_or_404(db,doc.application_id)
    if not can_view_documents(db,user,app): raise HTTPException(403)
    path=storage_path(app.id,doc.stored_name)
    if not path.exists(): raise HTTPException(404,'Stored file missing')
    audit(db,user.id,'document_downloaded',app.id,doc.original_name); db.commit()
    return FileResponse(path,filename=doc.original_name)
