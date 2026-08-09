# UCC Ethical Clearance Portal — Phase 1

A working foundation for the University of Cape Coast IRB ethical clearance workflow.

## Implemented in this build

- Central submission to the IRB Secretariat
- Applicant dashboard and draft applications
- Required student uploads: Research Protocol, Data Collection Instrument, Supervisor Approval
- Unique application references such as `UCC-IRB-2026-00001`
- IRB Secretariat administrative screening
- College metadata visibility after Secretariat screening
- Locked research documents before Secretariat authorisation
- College `Request Review Access` workflow
- Secretariat `Grant & Activate Documents` workflow
- College scientific reviewer assignment
- Reviewer workspace and scientific review submission
- Role-based access control
- Versioned document records
- Private file download endpoint with permission checks
- Status history and audit logging
- PostgreSQL-ready SQLAlchemy data model
- Responsive UCC-styled web interface
- Render-compatible current `TemplateResponse(request, name, context)` API
- Dedicated `/healthz` deployment health endpoint

## Technology

- FastAPI
- SQLAlchemy 2
- Jinja2
- PostgreSQL in production, SQLite supported for development
- Signed cookie sessions
- PBKDF2-SHA256 password hashing

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
- IRB Chairperson: `chair@ucc.edu.gh`

The demo College Administrator and Reviewer are attached to the College of Business and Economics. Create the applicant's sample application under that College to test the full flow.

## Workflow to test

1. Sign in as the applicant.
2. Create a new application under College of Business and Economics.
3. Upload Research Protocol, Data Collection Instrument and Supervisor Approval.
4. Submit to IRB Secretariat.
5. Sign in as Secretariat and mark the submission administratively complete.
6. Sign in as College Administrator. The application is visible, but documents are locked. Click `Request Review Access`.
7. Sign in as Secretariat. Grant the request. This activates the documents for the College.
8. Sign in as College Administrator and assign the College Scientific Reviewer.
9. Sign in as the reviewer, open the application, read the documents and submit a scientific review.

## Production notes

Before production deployment:

- Set a strong `SECRET_KEY`.
- Set `DATABASE_URL` to PostgreSQL.
- Set `SESSION_HTTPS_ONLY=true` behind HTTPS.
- Replace local document storage with private S3-compatible or institutional object storage.
- Integrate UCC SSO and institutional email.
- Add CSRF protection to all state-changing forms.
- Add malware scanning for uploaded files.
- Add Alembic migrations and automated backups.
- Add 2FA for privileged accounts.
- Complete IRB ethical reviewer, full-board meeting, revisions, certificate, amendment, renewal, closure and analytics modules.

## Next development tranche

The next logical module is the structured **College Scientific Review and revision cycle**, followed by **IRB ethical review classification and reviewer assignment**.


## Render deployment

Use the Docker runtime. The container binds to Render's `$PORT` automatically.

Recommended health check path:

```text
/healthz
```

Required environment variables:

```env
APP_NAME=UCC Ethical Clearance Portal
SECRET_KEY=<strong-random-secret>
DATABASE_URL=<Render PostgreSQL Internal Database URL>
SESSION_HTTPS_ONLY=true
STORAGE_DIR=/app/storage
```

`postgresql://` and `postgres://` database URLs are automatically normalized to the SQLAlchemy Psycopg 3 dialect `postgresql+psycopg://`.


## Render build verification
After deployment, open `/healthz`. This build should report `build: 2026-08-09-template-positional-v2`. If it does not, Render is serving an older commit/build.

## Split portal login (V3)

The public home page now provides two clearly separated access tabs:

- **Applicant Login** for researchers and students.
- **Administrative Portal** for IRB Secretariat, College Scientific Committee officers, College/IRB reviewers, IRB Chairperson and system administrators.

The login handler validates the selected portal against the user's role. Applicant accounts cannot enter through the Administrative Portal, and administrative accounts are redirected to use the administrative login.

After deployment, `/healthz` should report:

```text
build: 2026-08-09-split-login-v3
```


## Account governance (V4)

The account model now follows the required UCC governance:

- **Applicants self-register** from the public Applicant Login tab. Registration automatically signs the applicant in and opens the New Ethical Clearance Application form.
- **Administrative accounts are not publicly registrable.** They are created only by the **System Administrator** from the administrative dashboard.
- Administrative roles supported: IRB Secretariat, College Scientific Committee Officer, College Scientific Reviewer, IRB Reviewer, IRB Chairperson and System Administrator.
- College-level users must be assigned to a College when their account is created.
- The System Administrator can deactivate/reactivate administrative accounts.
- All authenticated users can change their password.
- If an administrator leaves the temporary-password field blank, the system generates a strong one-time display password.

### Bootstrap the first System Administrator on Render

Add these environment variables before the first production start:

```env
BOOTSTRAP_ADMIN_EMAIL=<institutional administrator email>
BOOTSTRAP_ADMIN_NAME=<administrator full name>
BOOTSTRAP_ADMIN_PASSWORD=<strong initial password>
```

The bootstrap account is created only if that email does not already exist. After successful creation, the variables can remain unchanged or the password variable can be removed.

After deployment, `/healthz` should report:

```text
build: 2026-08-09-account-governance-v4
```
