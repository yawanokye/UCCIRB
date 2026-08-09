from pathlib import Path
import os

BASE_DIR = Path(__file__).resolve().parent.parent
APP_NAME = os.getenv("APP_NAME", "UCC Ethical Clearance Portal")
SECRET_KEY = os.getenv("SECRET_KEY", "dev-change-me")
DATABASE_URL = os.getenv("DATABASE_URL", f"sqlite:///{BASE_DIR / 'ucc_irb.db'}")

# Render and some PostgreSQL providers expose URLs as postgresql:// or postgres://.
# This project uses Psycopg 3, so make the SQLAlchemy driver explicit.
if DATABASE_URL.startswith("postgresql://"):
    DATABASE_URL = DATABASE_URL.replace("postgresql://", "postgresql+psycopg://", 1)
elif DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql+psycopg://", 1)
STORAGE_DIR = Path(os.getenv("STORAGE_DIR", str(BASE_DIR / "storage")))
SESSION_HTTPS_ONLY = os.getenv("SESSION_HTTPS_ONLY", "false").lower() == "true"
MAX_UPLOAD_MB = 25
ALLOWED_EXTENSIONS = {".pdf", ".doc", ".docx", ".xls", ".xlsx"}

BOOTSTRAP_ADMIN_EMAIL = os.getenv("BOOTSTRAP_ADMIN_EMAIL", "").strip().lower()
BOOTSTRAP_ADMIN_NAME = os.getenv("BOOTSTRAP_ADMIN_NAME", "System Administrator").strip()
BOOTSTRAP_ADMIN_PASSWORD = os.getenv("BOOTSTRAP_ADMIN_PASSWORD", "")
