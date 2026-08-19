from fastapi import APIRouter, Depends, Form, Query, Request
import logging
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..database import get_db
from ..models import College, Role, User
from ..services.routing import get_applicant_affiliations
from ..services.auth import hash_password, password_needs_rehash, verify_password
from ..security import (clear_login_failures, log_security_event, login_locked, superadmin_ip_allowed)

router = APIRouter()
logger = logging.getLogger("ucc_irb.auth")

ADMIN_ROLES = {
    Role.IRB_SECRETARIAT.value,
    Role.COLLEGE_ADMIN.value,
    Role.COLLEGE_REVIEWER.value,
    Role.IRB_REVIEWER.value,
    Role.IRB_MEMBER.value,
    Role.IRB_CHAIR.value,
}


def normalise_portal(portal: str | None) -> str:
    if portal == 'administrative':
        return 'administrative'
    if portal in {'system_admin', 'system-administrator', 'superadmin'}:
        return 'system_admin'
    return 'applicant'


def password_error(password: str) -> str | None:
    if len(password) < 10:
        return 'Password must be at least 10 characters long.'
    categories = sum([any(c.islower() for c in password), any(c.isupper() for c in password), any(c.isdigit() for c in password), any(not c.isalnum() for c in password)])
    if categories < 3:
        return 'Use at least three of these: lowercase letters, uppercase letters, numbers and symbols.'
    return None


@router.get('/register')
def register_page(request: Request, db: Session = Depends(get_db)):
    if request.session.get('user_id'):
        return RedirectResponse('/dashboard', status_code=303)
    colleges = get_applicant_affiliations(db)
    return request.app.state.templates.TemplateResponse(
        request,
        'register.html',
        {'error': None, 'colleges': colleges},
    )


@router.post('/register')
def register_applicant(
    request: Request,
    full_name: str = Form(...),
    email: str = Form(...),
    password: str = Form(...),
    confirm_password: str = Form(...),
    college_id: str = Form(''),
    db: Session = Depends(get_db),
):
    full_name = full_name.strip()
    email = email.lower().strip()
    colleges = get_applicant_affiliations(db)

    error = None
    if len(full_name) < 3:
        error = 'Enter your full name.'
    elif '@' not in email:
        error = 'Enter a valid email address.'
    elif db.scalar(select(User).where(User.email == email)):
        error = 'An account already exists with this email address. Please sign in instead.'
    elif password != confirm_password:
        error = 'The passwords do not match.'
    else:
        error = password_error(password)

    if error:
        return request.app.state.templates.TemplateResponse(
            request,
            'register.html',
            {'error': error, 'colleges': colleges},
            status_code=400,
        )

    if college_id and not db.get(College, college_id):
        return request.app.state.templates.TemplateResponse(
            request,
            'register.html',
            {'error': 'The selected College could not be found.', 'colleges': colleges},
            status_code=400,
        )

    user = User(
        email=email,
        full_name=full_name,
        password_hash=hash_password(password),
        role=Role.APPLICANT.value,
        college_id=college_id or None,
        active=True,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    log_security_event(db, request, 'applicant_registered', email=email, user_id=user.id)

    request.session.clear()
    request.session['user_id'] = user.id
    request.session['portal'] = 'applicant'
    return RedirectResponse('/applications/new?welcome=1', status_code=303)


@router.get('/login')
def login_page(request: Request, portal: str = Query('applicant'), expired: int = Query(0)):
    portal = normalise_portal(portal)
    error = 'Your session has expired. Please sign in again to continue.' if expired else None
    return request.app.state.templates.TemplateResponse(
        request,
        'login.html',
        {'error': error, 'portal': portal},
    )


@router.post('/login')
def login(
    request: Request,
    email: str = Form(...),
    password: str = Form(...),
    portal: str = Form('applicant'),
    db: Session = Depends(get_db),
):
    portal = normalise_portal(portal)
    email = email.lower().strip()
    if login_locked(db, request, email, include_ip=(portal != 'applicant')):
        log_security_event(db, request, 'login_blocked', email=email, detail=f'portal={portal}')
        return request.app.state.templates.TemplateResponse(
            request, 'login.html',
            {'error': 'Too many unsuccessful login attempts. Please wait before trying again.', 'portal': portal},
            status_code=429,
        )
    user = db.scalar(select(User).where(User.email == email))

    if not user or not user.active or not verify_password(password, user.password_hash):
        log_security_event(db, request, 'login_failed', email=email, user_id=user.id if user else None, detail=f'portal={portal}')
        return request.app.state.templates.TemplateResponse(
            request,
            'login.html',
            {'error': 'Invalid email or password', 'portal': portal},
            status_code=400,
        )

    if portal == 'applicant' and user.role != Role.APPLICANT.value:
        logger.warning('Login portal role mismatch portal=applicant role=%s user_id=%s', user.role, user.id)
        return request.app.state.templates.TemplateResponse(
            request,
            'login.html',
            {
                'error': 'This account belongs to the Administrative Portal. Please use Administrative Login.',
                'portal': 'applicant',
            },
            status_code=403,
        )

    if portal == 'administrative' and user.role not in ADMIN_ROLES:
        logger.warning('Login portal role mismatch portal=administrative role=%s user_id=%s', user.role, user.id)
        message = (
            'System Administrator accounts must use the System Administrator Portal.'
            if user.role == Role.SUPERADMIN.value
            else 'Applicant accounts must use the Applicant Login.'
        )
        return request.app.state.templates.TemplateResponse(
            request,
            'login.html',
            {'error': message, 'portal': 'administrative'},
            status_code=403,
        )

    if portal == 'system_admin' and user.role != Role.SUPERADMIN.value:
        return request.app.state.templates.TemplateResponse(
            request,
            'login.html',
            {
                'error': 'This login is restricted to authorised System Administrator accounts.',
                'portal': 'system_admin',
            },
            status_code=403,
        )

    if user.role == Role.SUPERADMIN.value and not superadmin_ip_allowed(request):
        log_security_event(db, request, 'superadmin_ip_blocked', email=email, user_id=user.id)
        return request.app.state.templates.TemplateResponse(
            request, 'login.html',
            {'error': 'System Administrator access is not permitted from this network.', 'portal': 'system_admin'},
            status_code=403,
        )
    if password_needs_rehash(user.password_hash):
        user.password_hash = hash_password(password)
        db.commit()
    clear_login_failures(db, request, email)
    request.session.clear()
    request.session['user_id'] = user.id
    request.session['portal'] = portal
    target = '/system-admin' if portal == 'system_admin' else '/dashboard'
    return RedirectResponse(target, status_code=303)


@router.get('/system-admin/login')
def system_admin_login_page(request: Request, expired: int = Query(0), db: Session = Depends(get_db)):
    current_id = request.session.get('user_id')
    if current_id:
        current = db.get(User, current_id)
        if current and current.active and current.role == Role.SUPERADMIN.value:
            return RedirectResponse('/system-admin', status_code=303)
    return request.app.state.templates.TemplateResponse(
        request,
        'login.html',
        {'error': 'Your session has expired. Please sign in again to continue.' if expired else None, 'portal': 'system_admin'},
    )


@router.post('/system-admin/login')
def system_admin_login(
    request: Request,
    email: str = Form(...),
    password: str = Form(...),
    db: Session = Depends(get_db),
):
    email = email.lower().strip()
    if login_locked(db, request, email):
        log_security_event(db, request, 'login_blocked', email=email, detail='portal=system_admin')
        return request.app.state.templates.TemplateResponse(
            request, 'login.html',
            {'error': 'Too many unsuccessful login attempts. Please wait before trying again.', 'portal': 'system_admin'},
            status_code=429,
        )
    user = db.scalar(select(User).where(User.email == email))
    if not user or not user.active or not verify_password(password, user.password_hash):
        log_security_event(db, request, 'login_failed', email=email, user_id=user.id if user else None, detail='portal=system_admin')
        return request.app.state.templates.TemplateResponse(
            request,
            'login.html',
            {'error': 'Invalid email or password', 'portal': 'system_admin'},
            status_code=400,
        )
    if user.role != Role.SUPERADMIN.value:
        log_security_event(db, request, 'system_admin_role_denied', email=email, user_id=user.id)
        return request.app.state.templates.TemplateResponse(
            request,
            'login.html',
            {'error': 'This login is restricted to authorised System Administrator accounts.', 'portal': 'system_admin'},
            status_code=403,
        )
    if not superadmin_ip_allowed(request):
        log_security_event(db, request, 'superadmin_ip_blocked', email=email, user_id=user.id)
        return request.app.state.templates.TemplateResponse(
            request, 'login.html',
            {'error': 'System Administrator access is not permitted from this network.', 'portal': 'system_admin'},
            status_code=403,
        )
    if password_needs_rehash(user.password_hash):
        user.password_hash = hash_password(password)
        db.commit()
    clear_login_failures(db, request, email)
    request.session.clear()
    request.session['user_id'] = user.id
    request.session['portal'] = 'system_admin'
    return RedirectResponse('/system-admin', status_code=303)


@router.get('/account/password')
def password_page(request: Request, db: Session = Depends(get_db)):
    from ..services.auth import require_user
    user = require_user(request, db)
    return request.app.state.templates.TemplateResponse(
        request, 'change_password.html', {'user': user, 'error': None, 'success': None}
    )


@router.post('/account/password')
def change_password(
    request: Request,
    current_password: str = Form(...),
    new_password: str = Form(...),
    confirm_password: str = Form(...),
    db: Session = Depends(get_db),
):
    from ..services.auth import require_user
    user = require_user(request, db)
    error = None
    if not verify_password(current_password, user.password_hash):
        error = 'Current password is incorrect.'
    elif new_password != confirm_password:
        error = 'The new passwords do not match.'
    else:
        error = password_error(new_password)

    if error:
        return request.app.state.templates.TemplateResponse(
            request, 'change_password.html', {'user': user, 'error': error, 'success': None}, status_code=400
        )

    user.password_hash = hash_password(new_password)
    db.commit()
    log_security_event(db, request, 'password_changed', email=user.email, user_id=user.id)
    return request.app.state.templates.TemplateResponse(
        request, 'change_password.html', {'user': user, 'error': None, 'success': 'Password changed successfully.'}
    )


@router.post('/logout')
def logout(request: Request):
    portal = normalise_portal(request.session.get('portal', 'applicant'))
    request.session.clear()
    if portal == 'system_admin':
        return RedirectResponse('/system-admin/login', status_code=303)
    return RedirectResponse(f'/login?portal={portal}', status_code=303)
