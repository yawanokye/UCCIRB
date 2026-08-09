from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session
from ..database import get_db
from ..models import User
from ..services.auth import verify_password

router = APIRouter()

@router.get('/login')
def login_page(request: Request):
    return request.app.state.templates.TemplateResponse(request=request, name='login.html', context={'error': None})

@router.post('/login')
def login(request: Request, email: str = Form(...), password: str = Form(...), db: Session = Depends(get_db)):
    user = db.scalar(select(User).where(User.email == email.lower().strip()))
    if not user or not verify_password(password, user.password_hash):
        return request.app.state.templates.TemplateResponse(request=request, name='login.html', context={'error': 'Invalid email or password'}, status_code=400)
    request.session['user_id'] = user.id
    return RedirectResponse('/dashboard', status_code=303)

@router.post('/logout')
def logout(request: Request):
    request.session.clear()
    return RedirectResponse('/login', status_code=303)
