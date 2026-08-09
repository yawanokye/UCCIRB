from sqlalchemy import select
from app.database import Base, SessionLocal, engine
from app.models import College, Role, User
from app.services.auth import hash_password
from app.services.routing import ensure_routing_units

Base.metadata.create_all(bind=engine)


def ensure_user(db, email, name, role, password, college=None):
    existing = db.scalar(select(User).where(User.email == email))
    if existing:
        return existing
    user = User(
        email=email,
        full_name=name,
        role=role,
        password_hash=hash_password(password),
        college_id=college.id if college else None,
    )
    db.add(user)
    db.flush()
    return user


with SessionLocal() as db:
    ensure_routing_units(db)
    code = db.scalar(select(College).where(College.code == 'CODE'))

    ensure_user(db, 'applicant@ucc.edu.gh', 'Demo Student Applicant', Role.APPLICANT.value, 'Demo123!')
    ensure_user(db, 'secretariat@ucc.edu.gh', 'IRB Secretariat Officer', Role.IRB_SECRETARIAT.value, 'Demo123!')
    ensure_user(db, 'collegeadmin@ucc.edu.gh', 'College Scientific Committee Secretary', Role.COLLEGE_ADMIN.value, 'Demo123!', code)
    ensure_user(db, 'reviewer@ucc.edu.gh', 'College Scientific Reviewer', Role.COLLEGE_REVIEWER.value, 'Demo123!', code)
    ensure_user(db, 'irbreviewer@ucc.edu.gh', 'IRB Ethical Reviewer', Role.IRB_REVIEWER.value, 'Demo123!')
    ensure_user(db, 'chair@ucc.edu.gh', 'IRB Chairperson', Role.IRB_CHAIR.value, 'Demo123!')
    ensure_user(db, 'admin@ucc.edu.gh', 'System Administrator', Role.SUPERADMIN.value, 'Demo123!')
    db.commit()

print('Seed complete. Demo password for all accounts: Demo123!')
