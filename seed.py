from sqlalchemy import select
from app.database import Base, SessionLocal, engine
from app.models import College, Role, User
from app.services.auth import hash_password

Base.metadata.create_all(bind=engine)

def ensure_user(db, email, name, role, password, college=None):
    if db.scalar(select(User).where(User.email == email)):
        return
    db.add(User(email=email, full_name=name, role=role, password_hash=hash_password(password), college_id=college.id if college else None))

with SessionLocal() as db:
    colleges = [
        ('CBE','College of Business and Economics'),
        ('CES','College of Education Studies'),
        ('CHLS','College of Health and Allied Sciences'),
        ('CHAS','College of Humanities and Legal Studies'),
        ('CANS','College of Agriculture and Natural Sciences'),
    ]
    by_code = {}
    for code, name in colleges:
        c = db.scalar(select(College).where(College.code == code))
        if not c:
            c = College(code=code, name=name); db.add(c); db.flush()
        by_code[code] = c
    ensure_user(db,'applicant@ucc.edu.gh','Demo Student Applicant',Role.APPLICANT.value,'Demo123!')
    ensure_user(db,'secretariat@ucc.edu.gh','IRB Secretariat Officer',Role.IRB_SECRETARIAT.value,'Demo123!')
    ensure_user(db,'collegeadmin@ucc.edu.gh','College Scientific Committee Secretary',Role.COLLEGE_ADMIN.value,'Demo123!',by_code['CBE'])
    ensure_user(db,'reviewer@ucc.edu.gh','College Scientific Reviewer',Role.COLLEGE_REVIEWER.value,'Demo123!',by_code['CBE'])
    ensure_user(db,'chair@ucc.edu.gh','IRB Chairperson',Role.IRB_CHAIR.value,'Demo123!')
    db.commit()
print('Seed complete. Demo password for all accounts: Demo123!')
