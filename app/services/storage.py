from pathlib import Path
import uuid
from fastapi import HTTPException, UploadFile
from ..config import STORAGE_DIR, MAX_UPLOAD_MB, ALLOWED_EXTENSIONS


def save_upload(upload: UploadFile, application_id: str) -> tuple[str, str]:
    ext = Path(upload.filename or '').suffix.lower()
    if ext not in ALLOWED_EXTENSIONS:
        raise HTTPException(status_code=400, detail=f'Unsupported file type: {ext or "unknown"}')
    content = upload.file.read()
    if len(content) > MAX_UPLOAD_MB * 1024 * 1024:
        raise HTTPException(status_code=400, detail=f'File exceeds {MAX_UPLOAD_MB} MB limit')
    app_dir = STORAGE_DIR / application_id
    app_dir.mkdir(parents=True, exist_ok=True)
    stored_name = f'{uuid.uuid4().hex}{ext}'
    (app_dir / stored_name).write_bytes(content)
    return stored_name, upload.filename or stored_name


def storage_path(application_id: str, stored_name: str) -> Path:
    return STORAGE_DIR / application_id / stored_name
