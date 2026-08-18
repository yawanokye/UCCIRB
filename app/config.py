from pathlib import Path
import os

BASE_DIR = Path(__file__).resolve().parent.parent
APP_NAME = os.getenv("APP_NAME", "UCC IRB Ethical Clearance Portal")
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
PUBLIC_BASE_URL = os.getenv("PUBLIC_BASE_URL", "http://localhost:8000").rstrip("/")
REVIEW_DUE_DAYS = int(os.getenv("REVIEW_DUE_DAYS", "14"))
CLEARANCE_VALIDITY_DAYS = int(os.getenv("CLEARANCE_VALIDITY_DAYS", "365"))
MAX_UPLOAD_MB = int(os.getenv("MAX_UPLOAD_MB", "25"))
ALLOWED_EXTENSIONS = {".pdf", ".doc", ".docx", ".xls", ".xlsx"}

BOOTSTRAP_ADMIN_EMAIL = os.getenv("BOOTSTRAP_ADMIN_EMAIL", "").strip().lower()
BOOTSTRAP_ADMIN_NAME = os.getenv("BOOTSTRAP_ADMIN_NAME", "System Administrator").strip()
BOOTSTRAP_ADMIN_PASSWORD = os.getenv("BOOTSTRAP_ADMIN_PASSWORD", "")

# Reviewer assignment delivery, adapted from the academic submission portal.
GMAIL_CLIENT_ID = os.getenv('GMAIL_CLIENT_ID', '').strip()
GMAIL_CLIENT_SECRET = os.getenv('GMAIL_CLIENT_SECRET', '').strip()
GMAIL_REFRESH_TOKEN = os.getenv('GMAIL_REFRESH_TOKEN', '').strip()
GMAIL_SENDER_EMAIL = os.getenv('GMAIL_SENDER_EMAIL', '').strip()
REVIEW_ASSIGNMENT_LINK_EXPIRY_DAYS = int(os.getenv('REVIEW_ASSIGNMENT_LINK_EXPIRY_DAYS', '30'))
MAX_REVIEWERS_PER_APPLICATION = int(os.getenv('MAX_REVIEWERS_PER_APPLICATION', '3'))

# Security hardening
SESSION_MAX_AGE_SECONDS = int(os.getenv("SESSION_MAX_AGE_SECONDS", "28800"))
SESSION_IDLE_MINUTES = int(os.getenv("SESSION_IDLE_MINUTES", "30"))
LOGIN_MAX_FAILURES = int(os.getenv("LOGIN_MAX_FAILURES", "5"))
LOGIN_LOCKOUT_MINUTES = int(os.getenv("LOGIN_LOCKOUT_MINUTES", "15"))
RATE_LIMIT_POSTS_PER_MINUTE = int(os.getenv("RATE_LIMIT_POSTS_PER_MINUTE", "120"))
RATE_LIMIT_SECURE_REVIEW_PER_MINUTE = int(os.getenv("RATE_LIMIT_SECURE_REVIEW_PER_MINUTE", "60"))
ENABLE_API_DOCS = os.getenv("ENABLE_API_DOCS", "false").lower() == "true"
ALLOWED_HOSTS = [h.strip() for h in os.getenv("ALLOWED_HOSTS", "*.onrender.com,localhost,127.0.0.1,testserver").split(",") if h.strip()]
SUPERADMIN_ALLOWED_IPS = {ip.strip() for ip in os.getenv("SUPERADMIN_ALLOWED_IPS", "").split(",") if ip.strip()}
ALLOW_LEGACY_OFFICE = os.getenv("ALLOW_LEGACY_OFFICE", "false").lower() == "true"
if not ALLOW_LEGACY_OFFICE:
    ALLOWED_EXTENSIONS = {".pdf", ".docx", ".xlsx"}
