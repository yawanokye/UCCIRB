from __future__ import annotations

from pathlib import Path
import io
import re
import uuid
import zipfile

from fastapi import HTTPException, UploadFile
from ..config import STORAGE_DIR, MAX_UPLOAD_MB, ALLOWED_EXTENSIONS

_PDF_BLOCKED_MARKERS = (b'/JavaScript', b'/JS', b'/Launch', b'/EmbeddedFile', b'/OpenAction')
_CONTROL_CHARS = re.compile(r'[\x00-\x1f\x7f]+')


def safe_original_name(name: str | None, fallback: str) -> str:
    base = Path(name or fallback).name
    base = _CONTROL_CHARS.sub('', base).strip().replace('"', "'")
    return base[:180] or fallback


def _validate_office_zip(content: bytes, ext: str):
    try:
        with zipfile.ZipFile(io.BytesIO(content)) as zf:
            infos = zf.infolist()
            if len(infos) > 500:
                raise HTTPException(status_code=400, detail='Office document contains too many embedded entries')
            total_uncompressed = 0
            for info in infos:
                name = info.filename.replace('\\', '/')
                if name.startswith('/') or '..' in name.split('/'):
                    raise HTTPException(status_code=400, detail='Unsafe path detected inside Office document')
                total_uncompressed += info.file_size
                if info.compress_size > 0 and info.file_size / info.compress_size > 150:
                    raise HTTPException(status_code=400, detail='Office document has an unsafe compression ratio')
            if total_uncompressed > max(MAX_UPLOAD_MB * 4, 100) * 1024 * 1024:
                raise HTTPException(status_code=400, detail='Office document expands beyond the safe processing limit')
            names = set(zf.namelist())
            if '[Content_Types].xml' not in names:
                raise HTTPException(status_code=400, detail='Invalid Office document structure')
            required_prefix = 'word/' if ext == '.docx' else 'xl/'
            if not any(n.startswith(required_prefix) for n in names):
                raise HTTPException(status_code=400, detail='File contents do not match the selected Office document type')
    except zipfile.BadZipFile:
        raise HTTPException(status_code=400, detail='Invalid or corrupted Office document')


def _validate_content(content: bytes, ext: str):
    if not content:
        raise HTTPException(status_code=400, detail='Empty files cannot be uploaded')
    if ext == '.pdf':
        if not content.startswith(b'%PDF-'):
            raise HTTPException(status_code=400, detail='File extension and PDF content do not match')
        head = content[:2_000_000]
        if any(marker in head for marker in _PDF_BLOCKED_MARKERS):
            raise HTTPException(status_code=400, detail='Active or embedded PDF content is not permitted. Save a clean PDF and upload again.')
    elif ext in {'.docx', '.xlsx'}:
        if not content.startswith(b'PK'):
            raise HTTPException(status_code=400, detail='File extension and Office document content do not match')
        _validate_office_zip(content, ext)
    else:
        raise HTTPException(status_code=400, detail='Legacy Office files are disabled for security. Convert the file to PDF, DOCX or XLSX.')


def save_upload(upload: UploadFile, application_id: str) -> tuple[str, str]:
    ext = Path(upload.filename or '').suffix.lower()
    if ext not in ALLOWED_EXTENSIONS:
        allowed = ', '.join(sorted(ALLOWED_EXTENSIONS))
        raise HTTPException(status_code=400, detail=f'Unsupported file type. Allowed types: {allowed}')
    content = upload.file.read(MAX_UPLOAD_MB * 1024 * 1024 + 1)
    if len(content) > MAX_UPLOAD_MB * 1024 * 1024:
        raise HTTPException(status_code=400, detail=f'File exceeds {MAX_UPLOAD_MB} MB limit')
    _validate_content(content, ext)

    app_dir = (STORAGE_DIR / application_id).resolve()
    storage_root = STORAGE_DIR.resolve()
    if storage_root not in app_dir.parents and app_dir != storage_root:
        raise HTTPException(status_code=400, detail='Invalid storage location')
    app_dir.mkdir(parents=True, exist_ok=True)
    stored_name = f'{uuid.uuid4().hex}{ext}'
    (app_dir / stored_name).write_bytes(content)
    return stored_name, safe_original_name(upload.filename, stored_name)


def storage_path(application_id: str, stored_name: str) -> Path:
    app_dir = (STORAGE_DIR / application_id).resolve()
    candidate = (app_dir / Path(stored_name).name).resolve()
    if app_dir not in candidate.parents:
        raise HTTPException(status_code=400, detail='Invalid file path')
    return candidate
