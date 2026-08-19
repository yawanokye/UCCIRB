# UCC IRB Ethical Clearance Portal — V22

Safe Browsing remediation build. Adds explicit service identity disclosures, an About & Verification page, official UCC IRB reference/contact details, login/reviewer safety notices, and keeps the Google Search Console verification tag.

Build: `2026-08-19-next-action-irb-v24`

# UCC IRB Ethical Clearance Portal — Phase 2 V19

Build ID: `2026-08-18-secure-review-csrf-v20`

V19 improves the applicant and reviewer experience while keeping routing controls internal.

## V19 changes

- Applicants can remove a wrongly attached document while the item is still editable. Submitted historical records remain locked, and replacement versions can be uploaded when a submitted item is already part of a review record.
- Applicant-facing return states now say **Returned to Applicant by IRB Secretariat**, **Returned to Applicant by College Scientific Committee**, or **Returned to Applicant by IRB**.
- Public/applicant screens no longer expose the old Access and Routing panel or the direct-routing label. The Other UCC option is displayed simply as **Other UCC Academic/Administrative Unit**. Internal officers still retain the operational routing controls required to process applications.
- Expired authenticated sessions are redirected to the appropriate login page with an expiry notice. A user who was simply not signed in is sent to the appropriate login page without a false expiry message.
- Core workflow prerequisites redirect the user to the required step instead of leaving the user on a raw error. The required section and missing checklist items are highlighted in red.
- The public title is **UCC IRB Ethical Clearance Portal**.
- The **UCC-IRB Research Ethics Reviewer Assessment Form** is built into review assignment. It is attached to reviewer invitation email where email delivery is configured, added to secure review packages, and available to authorised reviewers through protected download routes. It is not exposed as a public static resource.
- The reviewer form supports fresh and revised review, conflict/confidentiality controls, structured scientific and ethical assessment, revision verification, overall recommendation, and applicant-facing privacy controls.

## Deployment

No new environment variable is required. Existing deployments may change `APP_NAME` to `UCC IRB Ethical Clearance Portal` for consistent service/email naming. Use `/healthz` to confirm the V19 build after deployment.

---

# V17 security compatibility update

Build ID: `2026-08-10-no-origin-block-v17`

V17 removes Origin/Referer same-origin blocking across the portal because reverse proxies and institutional networks can cause false 403 responses. Applicant login and registration remain low-friction. Protected workflow POST forms continue to use session-bound CSRF tokens, role checks, rate limiting, secure sessions, password hashing and audit/security logging.

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

## V18 fixes

- Reviewer files are now stored in normal private storage **and** copied into a protected database-backed fallback table. This prevents future review reports from disappearing after a redeploy or filesystem-mount problem.
- Existing review-report files that are still present at startup are automatically backfilled into the database fallback.
- Missing legacy reviewer files are shown clearly as unavailable, with a College/IRB action to reopen the assignment for reviewer re-upload.
- College administrators and authorised users can open reviewer reports inline. PDF files open in-browser and DOCX reports are rendered into a secure HTML preview. Download remains available separately.
- College Revised Submissions are detected from the actual uploaded College-revision package. A Response to College Review plus at least one revised work document is enough to surface the work even when a legacy status transition was missed.
- A revision surfaced from upload evidence can be sent to a new reviewer or back to a previous reviewer. The workflow normalises the revision into the formal received state when the College takes action.
- Review-material checkboxes and labels are left aligned consistently.

### Important for older missing reports

If a reviewer report disappeared before V18 because `/app/storage` was not persistent, V18 cannot recreate the lost bytes. The assignment page will show **Request report re-upload**. After reopening the assignment, use **Regenerate & Resend Secure Link** if the reviewer needs a new link. All newly uploaded reviewer files receive the database fallback automatically.


## V20 secure reviewer form fix

- Secure reviewer Conflict-of-Interest Declaration now carries a stateless HMAC security token bound to the emailed review link.
- Secure reviewer report submission uses the same link-bound token, including multipart upload submissions.
- Reviewers continue to authenticate with the secure emailed assignment link and do not need an applicant or administrative login.
- A stale reviewer form-security check redirects back to the secure reviewer workspace rather than exposing a raw JSON 403 page.


## V21 Google Search Console verification

The public base template includes the Google site verification token supplied for `https://ucc-irb-portal.onrender.com/`. After deployment, verify the URL-prefix property in Google Search Console, then inspect **Security & Manual Actions → Security Issues** before submitting a Safe Browsing review.


## V24 final IRB approval workflow
- Splits IRB processing into Exempt Determination, Expedited Review, and Full Board Review.
- Exempt classification now requires an authorised IRB determination instead of entering the generic final-approval route.
- Existing V22 exempt cases in `awaiting_final_irb_decision` are migrated to `exempt_determination_pending`.
- Internal IRB classification events are hidden from applicant-facing workflow history.
- Expedited and Full Board approvals use a dedicated Final IRB Approval / Decision stage.
- Final approval automatically generates a UCC IRB Ethics Approval Certificate with a unique verification token and QR code.
- QR codes resolve to the public verification page. Certificate numbers can also be verified at `/verify`.
- Confirmed exempt protocols generate a separately labelled QR-verifiable Exemption Determination, not an Ethics Approval Certificate.


## V24 workflow usability correction

- Adds a prominent **Next Required Action** card to staff-facing application records.
- Adds a Secretariat queue for College scientific recommendations that are ready for IRB classification.
- Allows the IRB Secretariat to record an authorised Exempt determination or final IRB decision on behalf of the selected approving authority, while preserving the logged-in officer in the audit trail.
- Requires the approving authority to be identified as IRB Chairperson, IRB Board, or Authorised IRB Officer as applicable.
- Full Board decisions must identify the IRB Board as approving authority.

## V26 workflow update — College recommendation, conditional approval and Board ratification

Build ID: `2026-08-19-college-admin-approval-ratification-v25`

### College Scientific Committee pathway

For the five UCC Colleges with Scientific Committees, Exempt/Expedited/Full Board classification is no longer the normal step after College recommendation. The sequence is:

1. Applicant submits centrally to IRB Secretariat.
2. Secretariat screens and forwards complete applications to the relevant College Scientific Committee.
3. College completes scientific review/revisions and records its recommendation.
4. Application returns to IRB Secretariat for **Administrative IRB Review**.
5. Authorised IRB action may:
   - Grant **Ethical Approval Pending Board Ratification** and issue a QR-verifiable certificate immediately.
   - Refer the College recommendation directly to Full Board review.
   - Return the recommendation to the College Scientific Committee for clarification.
6. Conditional approvals are added to a later IRB Board meeting for formal ratification.
7. The Board may ratify, ratify with conditions, defer, or revoke the conditional approval.

### Direct IRB pathway

Applications from UCC academic/administrative units outside the five College Scientific Committees retain the Exempt / Expedited / Full Board classification workflow.

### IRB processing correction

Authorised IRB officers can restore a College-pathway application to **Returned to IRB Secretariat for IRB Processing** when an erroneous IRB classification was entered after College recommendation. The old classifications remain in the confidential audit trail and are displayed internally as superseded.

### Approved Works Register

`/secretariat/approved-register` maintains a sortable/searchable register containing:

- applicant name and application reference;
- College/UCC unit;
- research title;
- College Scientific reviewer;
- officer who reviewed and approved;
- approval date and current approval/ratification status;
- Board meeting/ratification outcome where applicable;
- certificate number and verification link.

CSV export is available from the register.


### IRB Board Member access
System Administrators can create an **IRB Board Member** account. Board members use the **Board Review Queue** to access applications requiring Board oversight, including the application documents, College Scientific Committee recommendation and reviewer reports. Meeting scheduling is handled outside the portal. Secretariat/Chair officers retain responsibility for recording the formal Board decision or ratification outcome.


## V26 Board review simplification

- IRB meeting scheduling is no longer part of the portal workflow.
- Conditional approvals move directly to **Awaiting Board Ratification**.
- Board members use the **Board Review Queue** to inspect applications, College recommendations and reviewer reports.
- An authorised officer records the Board outcome when communicated, with an optional Board reference/minute number.
- The Approved Works Register now records the person who gave the conditional/initial approval and, once available, the person who gave or confirmed final approval/ratification.
- A separate **Final Approval Register** contains only completed final approvals and includes applicant, College/unit, research title, College reviewer, conditional approver, final approver, approval dates and certificate/QR verification links.


## V27 durable certificate storage

Certificate PDFs are now stored in PostgreSQL as a durable fallback. If Render loses the filesystem copy after a restart or deploy, the download endpoint first uses the database copy and, for older records that have neither copy, automatically regenerates the PDF from the approval record. Regeneration keeps the same certificate number and verification token but rebuilds the QR code using `PUBLIC_BASE_URL` (or Render's `RENDER_EXTERNAL_URL`). Production should set `PUBLIC_BASE_URL` explicitly to the public HTTPS portal URL.
