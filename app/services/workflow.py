from datetime import datetime
from sqlalchemy.orm import Session
from ..models import AppStatus, AuditLog, EthicsApplication, StatusHistory


def transition(db: Session, app: EthicsApplication, to_status: str, user_id: str, note: str | None = None):
    old = app.status
    app.status = to_status
    app.updated_at = datetime.utcnow()
    db.add(StatusHistory(application_id=app.id, from_status=old, to_status=to_status, changed_by=user_id, note=note))
    db.add(AuditLog(user_id=user_id, application_id=app.id, action='status_change', detail=f'{old} -> {to_status}' + (f' | {note}' if note else '')))
    return app


def audit(db: Session, user_id: str, action: str, application_id: str | None = None, detail: str | None = None):
    db.add(AuditLog(user_id=user_id, application_id=application_id, action=action, detail=detail))


STATUS_LABELS = {
    AppStatus.DRAFT.value: 'Draft',
    AppStatus.SUBMITTED.value: 'Submitted to IRB Secretariat',
    AppStatus.SECRETARIAT_SCREENING.value: 'Under Review by IRB Secretariat',
    AppStatus.RETURNED_ADMIN.value: 'Returned to Applicant by IRB Secretariat',
    AppStatus.ADMIN_COMPLETE.value: 'Administrative Screening Complete',
    AppStatus.FORWARDED_TO_COLLEGE.value: 'Forwarded to College Scientific Committee',
    AppStatus.AWAITING_COLLEGE_REVIEWER.value: 'Awaiting Scientific Reviewer Assignment',
    AppStatus.COLLEGE_REVIEW.value: 'Under College Scientific Review',
    AppStatus.COLLEGE_REVISION.value: 'Returned to Applicant by College Scientific Committee',
    AppStatus.COLLEGE_REVISED.value: 'Revised Submission Received by College Scientific Committee',
    AppStatus.AWAITING_COLLEGE_DECISION.value: 'Awaiting College Scientific Committee Decision',
    AppStatus.SCIENTIFICALLY_RECOMMENDED.value: 'Scientifically Recommended',
    AppStatus.NOT_SCIENTIFICALLY_RECOMMENDED.value: 'Not Scientifically Recommended',
    AppStatus.RETURNED_TO_IRB.value: 'Returned to IRB Secretariat for IRB Processing',
    AppStatus.DIRECT_IRB.value: 'With IRB Secretariat for IRB Processing',
    AppStatus.IRB_CLASSIFICATION.value: 'Internal IRB Review Classification',
    AppStatus.EXEMPT_DETERMINATION_PENDING.value: 'Exempt Determination Pending',
    AppStatus.EXEMPT_DETERMINED.value: 'Exemption Determination Confirmed',
    AppStatus.AWAITING_IRB_REVIEWER.value: 'Awaiting IRB Reviewer Assignment',
    AppStatus.IRB_REVIEW.value: 'Under IRB Ethical Review',
    AppStatus.IRB_REVISION.value: 'Returned to Applicant by IRB',
    AppStatus.IRB_REVISED.value: 'Revised Submission Received by IRB',
    AppStatus.FULL_BOARD.value: 'Scheduled for Full Board Review',
    AppStatus.AWAITING_FINAL_DECISION.value: 'Awaiting Final IRB Decision',
    AppStatus.CONDITIONAL_APPROVAL_PENDING_RATIFICATION.value: 'Ethical Approval Granted – Pending Board Ratification',
    AppStatus.BOARD_RATIFIED.value: 'IRB Board Ratification Completed',
    AppStatus.FINAL_APPROVAL.value: 'Final IRB Approval Granted',
    AppStatus.FINAL_APPROVAL_CONDITIONS.value: 'Final IRB Approval Granted with Conditions',
    AppStatus.APPROVED.value: 'Final IRB Approval Granted',
    AppStatus.APPROVED_CONDITIONS.value: 'Final IRB Approval Granted with Conditions',
    AppStatus.DEFERRED.value: 'Deferred',
    AppStatus.REJECTED.value: 'Rejected',
    AppStatus.CLEARANCE_ISSUED.value: 'Ethics Certificate Issued',
    AppStatus.ACTIVE.value: 'Active Study',
    AppStatus.AMENDMENT_PENDING.value: 'Amendment Pending',
    AppStatus.RENEWAL_PENDING.value: 'Renewal Pending',
    AppStatus.ADVERSE_EVENT_PENDING.value: 'Adverse Event Review Pending',
    AppStatus.CLOSURE_PENDING.value: 'Study Closure Pending',
    AppStatus.EXPIRED.value: 'Expired',
    AppStatus.SUSPENDED.value: 'Suspended',
    AppStatus.REVOKED.value: 'Revoked',
    AppStatus.CLOSED.value: 'Closed',
}

def status_label(status: str | None) -> str:
    if not status:
        return ''
    return STATUS_LABELS.get(status, status.replace('_', ' ').title())


# Internal IRB routing/classification should not be exposed in applicant-facing history.
APPLICANT_HIDDEN_STATUSES = {
    AppStatus.IRB_CLASSIFICATION.value,
}

APPLICANT_STATUS_LABELS = {
    AppStatus.RETURNED_TO_IRB.value: 'Returned to IRB Secretariat for IRB Processing',
    AppStatus.DIRECT_IRB.value: 'IRB Processing',
    AppStatus.EXEMPT_DETERMINATION_PENDING.value: 'IRB Determination Pending',
    AppStatus.EXEMPT_DETERMINED.value: 'IRB Determination Completed',
    AppStatus.AWAITING_IRB_REVIEWER.value: 'IRB Review in Progress',
    AppStatus.IRB_REVIEW.value: 'IRB Review in Progress',
    AppStatus.FULL_BOARD.value: 'IRB Review in Progress',
    AppStatus.AWAITING_FINAL_DECISION.value: 'Awaiting Final IRB Decision',
    AppStatus.CONDITIONAL_APPROVAL_PENDING_RATIFICATION.value: 'Ethical Approval Granted – Pending Board Ratification',
    AppStatus.BOARD_RATIFIED.value: 'IRB Board Ratification Completed',
    AppStatus.FINAL_APPROVAL.value: 'Final IRB Approval Granted',
    AppStatus.FINAL_APPROVAL_CONDITIONS.value: 'Final IRB Approval Granted with Conditions',
    AppStatus.APPROVED.value: 'Final IRB Approval Granted',
    AppStatus.APPROVED_CONDITIONS.value: 'Final IRB Approval Granted with Conditions',
    AppStatus.CLEARANCE_ISSUED.value: 'Ethics Certificate Issued',
}

def applicant_status_label(status: str | None) -> str:
    if not status:
        return ''
    return APPLICANT_STATUS_LABELS.get(status, status_label(status))
