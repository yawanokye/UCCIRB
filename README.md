# V15 Login/CSRF Reliability Patch

- Renders CSRF tokens directly inside applicant, administrative and system-admin login forms.
- Renders CSRF token directly in applicant registration form.
- Makes same-origin checks proxy-aware for Render via X-Forwarded-Host.
- Adds safe server-side diagnostics distinguishing CSRF rejection from portal-role mismatch without logging passwords or raw CSRF tokens.
- Build ID: `2026-08-10-login-csrf-diagnostics-v15`.

# UCC Ethical Clearance Portal — Phase 2 V12

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

`Applicant → IRB Secretariat receipt/checklist → Secretariat marks complete → automatic forwarding to College Scientific Committee → College scientific review/revision cycle → College recommendation → IRB Secretariat → IRB ethical review → final IRB decision`

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
- College metadata visibility while first submissions await Secretariat screening
- Automatic College document activation and forwarding after Secretariat completeness
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
build: 2026-08-10-review-revision-privacy-v12
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

The bootstrap settings are authoritative for the configured System Administrator account. On startup, if the email already exists under another role, the portal promotes it to `superadmin`, activates it, removes any College restriction, and synchronises its password with `BOOTSTRAP_ADMIN_PASSWORD`. After sign-in, the System Administrator can create other authorised administrative accounts from `/system-admin`. Applicants continue to create their own accounts from `/register`.


## V8 bootstrap repair

Build: `2026-08-09-phase2-admin-bootstrap-v8`

This release repairs existing databases where the email configured in `BOOTSTRAP_ADMIN_EMAIL` was already present as an applicant or another administrative role. The environment-defined bootstrap account is synchronised to the System Administrator role at application startup.


## Phase 2 V10 - applicant-first landing page and official resources

The public portal has been reorganised so that **Start New Application** and **Applicant Login** are the dominant actions. Administrative and System Administrator access remains available in a quieter institutional-access section.

Public applicant support now includes `/applicant-guide` with:

- How to apply online
- Required application documents
- Important pre-submission information
- Application fee schedule
- Official UCC-IRB forms and templates
- Explanation of College Scientific Committee vs direct IRB Secretariat routing

Official PDFs are packaged under `app/static/resources/` and are therefore deployed with the application. The Applicant Dashboard, New Application page and application document-upload area all link back to the guide/resources.

### Included resources

- Composite Application Form
- Application Instructions
- Adult Informed Consent Form
- Child Assent Form
- Consent from Records Keepers
- Abridged CV Template
- Ethical Clearance Application Fee Schedule

### Core application completeness validation

Before initial submission the portal now checks for a protocol or Composite Form, application letter, similarity report and applicant CV. Student applications additionally require supervisor approval, Head of Unit support and supervisor CV. If the Composite Form is not uploaded, a completed IRB checklist is also required. Study-specific consent, assent, records-access and data-collection instruments remain conditional and are checked according to the study.

Build ID: `2026-08-10-applicant-resources-v10`

## Phase 2 V11 — Secretariat receiving, College scientific workflow, register and reviewer emailing

Build ID: `2026-08-10-secretariat-college-workflow-v11`

This release changes the first-submission governance flow to:

`Applicant → IRB Secretariat receipt/checklist → Mark complete → automatic forwarding to relevant College Scientific Committee → College reviewer assignment/revision cycle → College scientific recommendation → IRB final ethical review and approval`

For applicants from other UCC academic/administrative units outside the five Scientific Committee Colleges, the complete application remains with the IRB Secretariat and proceeds to the direct IRB pathway.

### New controls

- **Automatic forwarding after Secretariat completeness:** no second College access-permission step is required after an application is marked administratively complete.
- **College monitoring of first submissions:** the relevant College can see metadata while the application is still awaiting initial Secretariat screening. It can click **Request IRB Secretariat Attention** if the submission appears unattended. Documents remain locked at this stage.
- **Persisted Secretariat document checklist:** every submitted file must be opened/reviewed and checked. The portal also shows the core required-document list. The Secretariat cannot mark an application complete until the core requirements are present and every submitted document has been verified.
- **Submission Register:** `/secretariat/register` lists all submitted work and can filter/sort by Scientific Committee College, other UCC unit, submission type, status, applicant/reference, and dates. CSV export is included.
- **Fresh and revised separation:** College dashboards and assignment queues show fresh submissions separately from revised submissions. Document history also separates fresh, College-revision and IRB-revision files.
- **Direct College revision route:** once the first submission has passed Secretariat screening, College revision communication is between the College Scientific Committee and the applicant. A revised submission is routed directly back to the relevant College, without returning to the Secretariat. It goes back to IRB only after the College scientifically recommends it.
- **Reviewer email workflow adapted from the Academic Submission Portal:** the College selects one or several applications, enters the reviewer title/name/email/phone, and sends one secure link. A reviewer portal account is not required. Each assigned application remains a separate work item with its own conflict declaration, package download and review report submission.
- **Applicant/reviewer email collision protection:** a person may have an applicant account using the same email later used for review. Reviewer contacts are stored separately and mapped to an internal non-login proxy, so the applicant account is not converted or overwritten.
- **Legacy V10 state repair:** existing records in the old visible/access-request states are migrated into the V11 automatic-forwarding College queue at startup, and their documents are activated for the College.

### Existing Gmail environment variables

The same Gmail OAuth settings continue to deliver reviewer assignments and College/applicant revision notifications:

```env
GMAIL_CLIENT_ID=...
GMAIL_CLIENT_SECRET=...
GMAIL_REFRESH_TOKEN=...
GMAIL_SENDER_EMAIL=...
PUBLIC_BASE_URL=https://ucc-irb-portal.onrender.com
```

No additional environment variable is required for V11.


## Phase 2 V12 — selected review materials, applicant review reports and revision disposition

Build ID: `2026-08-10-review-revision-privacy-v12`

This release strengthens the College Scientific Committee and applicant workflows.

- College officers can tick the specific document types that should be checked by a reviewer. The latest available version of each selected type is bound to that reviewer assignment.
- Selected review documents are added to the secure reviewer package and are also attached to the Gmail review invitation up to a conservative email-size limit. Files beyond the limit remain available in the secure workspace.
- The reviewer workspace explicitly lists the items selected by the assigning office.
- Applicants can download the completed main Scientific or IRB Review Report from their application record. The applicant download filename is generic and does not expose the reviewer name. Internal annotated/supporting reviewer files remain restricted.
- Applicant-facing reviewer identity is hidden. Assignment cards use generic labels such as **Scientific reviewer**, and workflow history converts reviewer-specific assignment notes to **Assigned scientific reviewer** or **Assigned IRB ethical reviewer**.
- The applicant upload area now includes a compact required-document checklist beside the upload controls. Successfully uploaded core items show a green tick. Revision rounds have their own response/revised-document checklist.
- College dashboards keep fresh work and revised work separate. Revised-submission rows show the previous-round reviewer(s) as clickable names.
- When a College revision returns, the College can choose **Yes, resubmit for review** and select one or more previous reviewers, or **No, administratively reviewed** and move directly to the College Committee decision stage.
- Re-review sends only the latest College revision round to the selected previous reviewer(s), with those revised files attached to the email where size permits and available in the secure workspace.

No additional Render environment variable is required for V12. Gmail attachment delivery uses the existing `GMAIL_*` settings.


## V14 revision-queue and College dashboard fix

- Revised submissions are now classified from the current College revision status plus persisted revision evidence.
- Legacy/migrated revisions can also be inferred from document-stage metadata, so they cannot silently fall back into Fresh Submissions.
- The College dashboard is action-oriented: IRB watch, fresh awaiting assignment, revised awaiting action, active reviews, and Committee decisions are separated.
- Revised rows show previous-round reviewers and link directly to the re-review/administrative-review decision.
- The review assignment queue lists revised submissions separately and supports assignment to a new reviewer while preserving the shortcut to previous reviewers.
- Initial Secretariat screening retains the secure in-frame document viewer, per-document checklist/comments, and return-to-applicant workflow.
- Security hardening from V13 remains enabled.


## V16 balanced applicant security

Applicant authentication was adjusted to reduce false 403/429 responses on shared campus networks and mobile connections while preserving stronger controls for privileged portals. POST `/login` and `/register` use same-origin enforcement, SameSite session cookies, password verification and account-level failed-login lockout without requiring a CSRF form token. Applicant login lockout is account-based rather than shared-IP based. Administrative, reviewer, College, IRB and System Administrator workflows retain CSRF protection and the stronger controls from the security-hardening release.


## V17 Render proxy compatibility

- Removed Origin/Referer host-comparison blocking from all portals because reverse proxies can rewrite these headers.
- Applicant login and registration do not require a CSRF form token.
- Protected workflow POST actions still require the per-session CSRF token.
- Password hashing, role authorization, session security, login lockout, rate limiting, upload validation and security-event logging remain enabled.
- Build ID: `2026-08-10-origin-check-removed-v17`.
