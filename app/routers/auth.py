from fastapi import APIRouter, Depends, Form, Query, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..database import get_db
from ..models import College, Role, User
from ..services.routing import get_applicant_affiliations
from ..services.auth import hash_password, verify_password

router = APIRouter()

ADMIN_ROLES = {
    Role.IRB_SECRETARIAT.value,
    Role.COLLEGE_ADMIN.value,
    Role.COLLEGE_REVIEWER.value,
    Role.IRB_REVIEWER.value,
    Role.IRB_CHAIR.value,
    Role.SUPERADMIN.value,
}


def normalise_portal(portal: str | None) -> str:
    return 'administrative' if portal == 'administrative' else 'applicant'


def password_error(password: str) -> str | None:
    if len(password) < 8:
        return 'Password must be at least 8 characters long.'
    if not any(c.isalpha() for c in password) or not any(c.isdigit() for c in password):
        return 'Password must contain at least one letter and one number.'
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

    request.session['user_id'] = user.id
    request.session['portal'] = 'applicant'
    return RedirectResponse('/applications/new?welcome=1', status_code=303)


@router.get('/login')
def login_page(request: Request, portal: str = Query('applicant')):
    portal = normalise_portal(portal)
    return request.app.state.templates.TemplateResponse(
        request,
        'login.html',
        {'error': None, 'portal': portal},
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
    user = db.scalar(select(User).where(User.email == email.lower().strip()))

    if not user or not user.active or not verify_password(password, user.password_hash):
        return request.app.state.templates.TemplateResponse(
            request,
            'login.html',
            {'error': 'Invalid email or password', 'portal': portal},
            status_code=400,
        )

    if portal == 'applicant' and user.role != Role.APPLICANT.value:
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
        return request.app.state.templates.TemplateResponse(
            request,
            'login.html',
            {
                'error': 'Applicant accounts must use the Applicant Login.',
                'portal': 'administrative',
            },
            status_code=403,
        )

    request.session['user_id'] = user.id
    request.session['portal'] = portal
    return RedirectResponse('/dashboard', status_code=303)


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
    return request.app.state.templates.TemplateResponse(
        request, 'change_password.html', {'user': user, 'error': None, 'success': 'Password changed successfully.'}
    )


@router.post('/logout')
def logout(request: Request):
    portal = request.session.get('portal', 'applicant')
    request.session.clear()
    return RedirectResponse(f'/login?portal={normalise_portal(portal)}', status_code=303)
