# UCC Ethical Clearance Portal — Phase 2 V6

A working FastAPI/PostgreSQL-ready research ethics governance portal for the University of Cape Coast Institutional Review Board.

## Governance routing implemented

All applications are submitted centrally to the **IRB Secretariat**.

Only these five Colleges route through a College Scientific Committee:

1. College of Agriculture and Natural Sciences
2. College of Distance Education
3. College of Education Studies
4. College of Humanities and Legal Studies
5. College of Health and Allied Sciences

Applications from UCC academic or administrative units outside these five Colleges use **Other UCC Academic/Administrative Unit (Direct IRB Secretariat)**. The applicant enters the exact Unit/Directorate/School/Centre in the application. After administrative screening, these applications remain with the IRB Secretariat and move directly to IRB review classification.

For the five Scientific Committee Colleges, the workflow is:

`Applicant → IRB Secretariat screening → College metadata visibility → College requests access → Secretariat activates documents → College scientific review → College decision → IRB Secretariat → IRB ethical review → final IRB decision`

For other UCC units, the workflow is:

`Applicant → IRB Secretariat screening → direct IRB classification → IRB ethical review → final IRB decision`

## Phase 2 features

- UCC-branded templates using the supplied University of Cape Coast logo/header treatment
- UCC navy, red and gold visual identity throughout the portal
- Applicant self-registration and applicant login
- Separate Administrative Portal login
- System Administrator-controlled administrative accounts
- Central application submission and administrative screening
- Five fixed Scientific Committee College routes
- Direct IRB Secretariat route for other UCC academic/administrative units
- Locked College documents until Secretariat access approval
- College review access request and activation
- College scientific reviewer assignment
- Reviewer conflict-of-interest declaration
- Reviewer deadlines and workload indicators
- College Scientific Committee decision and revision cycle
- IRB classification: exempt, expedited or full board
- IRB reviewer assignment and ethical review
- IRB applicant revision cycle
- Full Board meeting creation and agenda assignment
- Authorised final IRB decision
- Ethical clearance PDF generation
- QR-based public clearance verification
- Post-approval amendment, renewal/continuing review, adverse-event and closure requests
- Versioned uploaded documents
- Status history and audit logging
- PostgreSQL-ready SQLAlchemy data model
- Render-compatible `/healthz` deployment endpoint

## Automatic routing migration

This build automatically ensures the five authorised Scientific Committee Colleges and the direct IRB routing option exist when the application starts.

Earlier development builds used the CHLS and CHAS labels in the opposite order. V6 detects that older mapping and preserves existing user/application affiliations while correcting the authoritative College codes and names.

## Run locally

```bash
python -m venv .venv
# Windows
.venv\Scripts\activate
# Linux/macOS
source .venv/bin/activate

pip install -r requirements.txt
python seed.py
uvicorn app.main:app --reload
```

Open `http://127.0.0.1:8000`.

## Demo accounts

All demo accounts use password `Demo123!`.

- Applicant: `applicant@ucc.edu.gh`
- IRB Secretariat: `secretariat@ucc.edu.gh`
- College Scientific Committee Secretary: `collegeadmin@ucc.edu.gh`
- College Scientific Reviewer: `reviewer@ucc.edu.gh`
- IRB Reviewer: `irbreviewer@ucc.edu.gh`
- IRB Chairperson: `chair@ucc.edu.gh`
- System Administrator: `admin@ucc.edu.gh`

The demo College users are assigned to the **College of Distance Education**.

## Render environment variables

```env
APP_NAME=UCC Ethical Clearance Portal
SECRET_KEY=<strong-random-secret>
DATABASE_URL=<Render PostgreSQL Internal Database URL>
SESSION_HTTPS_ONLY=true
STORAGE_DIR=/app/storage
PUBLIC_BASE_URL=https://ucc-irb-portal.onrender.com
REVIEW_DUE_DAYS=14
CLEARANCE_VALIDITY_DAYS=365
MAX_UPLOAD_MB=25
BOOTSTRAP_ADMIN_EMAIL=<system administrator email>
BOOTSTRAP_ADMIN_NAME=System Administrator
BOOTSTRAP_ADMIN_PASSWORD=<strong initial password>
```

Use `/healthz` as the Render health check path. This build reports:

```text
build: 2026-08-09-phase2-routing-v6
```

Uploaded documents should use a persistent disk mounted at `/app/storage` during development/testing. For full institutional production, migrate research documents to private institutional or S3-compatible object storage with malware scanning, encryption and retention controls.

## Important production hardening still required

Before institutional production use, add UCC SSO, email activation and notifications, CSRF protection, 2FA for privileged users, malware scanning, formal Alembic migrations, private object storage, automated backups, security testing, and institutional privacy/data-retention configuration.

## Phase 2 V7: Dedicated System Administrator Portal

The System Administrator is now separated from the normal Administrative Portal.

- Applicant portal: `/login?portal=applicant`
- Administrative portal: `/login?portal=administrative`
- System Administrator portal: `/system-admin/login`
- System Administrator dashboard: `/system-admin`

The normal Administrative Portal accepts IRB Secretariat, College Scientific Committee Officer, College Scientific Reviewer, IRB Reviewer and IRB Chairperson accounts. A `superadmin` account is deliberately rejected there and must use the System Administrator Portal.

For the first production System Administrator, configure these Render environment variables before deploying:

```env
BOOTSTRAP_ADMIN_EMAIL=your-admin-email@ucc.edu.gh
BOOTSTRAP_ADMIN_NAME=System Administrator
BOOTSTRAP_ADMIN_PASSWORD=<strong-initial-password>
```

The bootstrap process creates the account only when that email does not already exist. After sign-in, the System Administrator can create other authorised administrative accounts from `/system-admin`. Applicants continue to create their own accounts from `/register`.
