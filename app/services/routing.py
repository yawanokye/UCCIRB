from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import College, EthicsApplication, User

# These are the only UCC Colleges that route through a College Scientific Committee.
SCIENTIFIC_COMMITTEE_COLLEGES = (
    ("CANS", "College of Agriculture and Natural Sciences"),
    ("CODE", "College of Distance Education"),
    ("CES", "College of Education Studies"),
    ("CHLS", "College of Humanities and Legal Studies"),
    ("CHAS", "College of Health and Allied Sciences"),
)
SCIENTIFIC_COMMITTEE_CODES = frozenset(code for code, _ in SCIENTIFIC_COMMITTEE_COLLEGES)

# Routing placeholder for UCC units that do not belong to one of the five Scientific
# Committee Colleges. The exact unit is captured in the application's Department / Unit field.
DIRECT_IRB_CODE = "IRB-DIRECT"
DIRECT_IRB_NAME = "Other UCC Academic/Administrative Unit"


def ensure_routing_units(db: Session) -> None:
    """Ensure fixed routing choices exist while preserving historical applications.

    Earlier development builds used CHLS/CHAS labels in the opposite order. To avoid a
    unique-name collision on an existing database, all existing target-code records are
    temporarily renamed before their authoritative names are applied.
    """
    targets = (*SCIENTIFIC_COMMITTEE_COLLEGES, (DIRECT_IRB_CODE, DIRECT_IRB_NAME))
    existing_by_code = {
        c.code: c
        for c in db.scalars(select(College).where(College.code.in_([code for code, _ in targets]))).all()
    }

    # Phase 1/V4 used CHLS and CHAS labels in the opposite order. Preserve the
    # institutional meaning of any existing users/applications before correcting the codes.
    old_chls = existing_by_code.get("CHLS")
    old_chas = existing_by_code.get("CHAS")
    if (
        old_chls and old_chas
        and old_chls.name == "College of Health and Allied Sciences"
        and old_chas.name == "College of Humanities and Legal Studies"
    ):
        for user in db.scalars(select(User).where(User.college_id.in_([old_chls.id, old_chas.id]))).all():
            user.college_id = old_chas.id if user.college_id == old_chls.id else old_chls.id
        for application in db.scalars(
            select(EthicsApplication).where(EthicsApplication.college_id.in_([old_chls.id, old_chas.id]))
        ).all():
            application.college_id = old_chas.id if application.college_id == old_chls.id else old_chls.id
        db.flush()

    # Free the authoritative names first, including the CHLS/CHAS swap from older builds.
    for code, college in existing_by_code.items():
        college.name = f"__routing_migration__{code}__"
        college.active = True
    db.flush()

    for code, name in targets:
        college = existing_by_code.get(code)
        if college:
            college.name = name
            college.active = True
        else:
            # If a legacy record owns the intended display name under another code, keep
            # that historical record untouched but make its display name unique so the
            # authoritative routing record can be created safely.
            name_owner = db.scalar(select(College).where(College.name == name))
            if name_owner and name_owner.code != code:
                name_owner.name = f"{name_owner.name} (Legacy)"
                db.flush()
            db.add(College(code=code, name=name, active=True))
    db.commit()


def is_scientific_committee_college(college: College | None) -> bool:
    return bool(college and college.code in SCIENTIFIC_COMMITTEE_CODES)


def is_direct_irb_affiliation(college: College | None) -> bool:
    return bool(college and college.code == DIRECT_IRB_CODE)


def get_scientific_committee_colleges(db: Session) -> list[College]:
    return list(
        db.scalars(
            select(College)
            .where(College.code.in_(SCIENTIFIC_COMMITTEE_CODES), College.active == True)
            .order_by(College.name)
        ).all()
    )


def get_direct_irb_affiliation(db: Session) -> College | None:
    return db.scalar(select(College).where(College.code == DIRECT_IRB_CODE, College.active == True))


def get_applicant_affiliations(db: Session) -> list[College]:
    colleges = get_scientific_committee_colleges(db)
    direct = get_direct_irb_affiliation(db)
    if direct:
        colleges.append(direct)
    return colleges
