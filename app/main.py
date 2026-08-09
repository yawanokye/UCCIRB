import fastapi
import starlette
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from fastapi.responses import JSONResponse
from starlette.middleware.sessions import SessionMiddleware

from .config import APP_NAME, BASE_DIR, SECRET_KEY, SESSION_HTTPS_ONLY, STORAGE_DIR
from .database import Base, engine
from .routers import auth, portal

STORAGE_DIR.mkdir(parents=True, exist_ok=True)
Base.metadata.create_all(bind=engine)

app = FastAPI(title=APP_NAME)
app.add_middleware(SessionMiddleware, secret_key=SECRET_KEY, https_only=SESSION_HTTPS_ONLY, same_site='lax')
app.mount('/static', StaticFiles(directory=BASE_DIR / 'app' / 'static'), name='static')
app.state.templates = Jinja2Templates(directory=BASE_DIR / 'app' / 'templates')
app.include_router(auth.router)
app.include_router(portal.router)


BUILD_ID = '2026-08-09-template-positional-v2'

@app.get('/healthz', include_in_schema=False)
def healthz():
    return JSONResponse({
        'status': 'ok',
        'build': BUILD_ID,
        'fastapi': fastapi.__version__,
        'starlette': starlette.__version__,
    })
