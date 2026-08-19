from __future__ import annotations
from datetime import datetime
from enum import Enum
import uuid

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, LargeBinary, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .database import Base


def uuid4_str():
    return str(uuid.uuid4())


class Role(str, Enum):
    APPLICANT = "applicant"
    IRB_SECRETARIAT = "irb_secretariat"
    COLLEGE_ADMIN = "college_admin"
    COLLEGE_REVIEWER = "college_reviewer"
    IRB_REVIEWER = "irb_reviewer"
    IRB_MEMBER = "irb_member"
    IRB_CHAIR = "irb_chair"
    SUPERADMIN = "superadmin"


class AppStatus(str, Enum):
    DRAFT = "draft"
    SUBMITTED = "submitted_to_irb_secretariat"
    SECRETARIAT_SCREENING = "irb_secretariat_screening"
    RETURNED_ADMIN = "returned_to_applicant_admin"
    ADMIN_COMPLETE = "administratively_complete"
    FORWARDED_TO_COLLEGE = "forwarded_to_college_scientific_committee"
    VISIBLE_TO_COLLEGE = "visible_to_college"
    COLLEGE_ACCESS_REQUESTED = "college_review_access_requested"
    COLLEGE_REVIEW_AUTHORISED = "college_review_authorised"
    AWAITING_COLLEGE_REVIEWER = "awaiting_college_reviewer_assignment"
    COLLEGE_REVIEW = "under_college_scientific_review"
    COLLEGE_REVISION = "college_revision_required"
    COLLEGE_REVISED = "college_revised_submission_received"
    AWAITING_COLLEGE_DECISION = "awaiting_college_decision"
    SCIENTIFICALLY_RECOMMENDED = "scientifically_recommended"
    NOT_SCIENTIFICALLY_RECOMMENDED = "not_scientifically_recommended"
    RETURNED_TO_IRB = "returned_to_irb_secretariat"
    DIRECT_IRB = "direct_irb_secretariat_pathway"
    IRB_CLASSIFICATION = "irb_review_classification"
    EXEMPT_DETERMINATION_PENDING = "exempt_determination_pending"
    EXEMPT_DETERMINED = "exempt_determination_confirmed"
    AWAITING_IRB_REVIEWER = "awaiting_irb_reviewer_assignment"
    IRB_REVIEW = "under_irb_ethical_review"
    IRB_REVISION = "irb_revision_required"
    IRB_REVISED = "irb_revised_submission_received"
    FULL_BOARD = "scheduled_for_full_board_review"
    AWAITING_FINAL_DECISION = "awaiting_final_irb_decision"
    CONDITIONAL_APPROVAL_PENDING_RATIFICATION = "ethical_approval_pending_board_ratification"
    BOARD_RATIFIED = "board_ratification_completed"
    FINAL_APPROVAL = "final_irb_approval_granted"
    FINAL_APPROVAL_CONDITIONS = "final_irb_approval_with_conditions"
    APPROVED = "approved"
    APPROVED_CONDITIONS = "approved_with_conditions"
    DEFERRED = "deferred"
    REJECTED = "rejected"
    CLEARANCE_ISSUED = "ethical_clearance_issued"
    ACTIVE = "active_study"
    AMENDMENT_PENDING = "amendment_pending"
    RENEWAL_PENDING = "renewal_pending"
    ADVERSE_EVENT_PENDING = "adverse_event_pending"
    CLOSURE_PENDING = "study_closure_pending"
    EXPIRED = "expired"
    SUSPENDED = "suspended"
    REVOKED = "revoked"
    CLOSED = "closed"


class College(Base):
    __tablename__ = "colleges"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=uuid4_str)
    code: Mapped[str] = mapped_column(String(20), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(200), unique=True)
    active: Mapped[bool] = mapped_column(Boolean, default=True)

    users = relationship("User", back_populates="college")
    applications = relationship("EthicsApplication", back_populates="college")


class User(Base):
    __tablename__ = "users"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=uuid4_str)
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    full_name: Mapped[str] = mapped_column(String(200))
    password_hash: Mapped[str] = mapped_column(String(500))
    role: Mapped[str] = mapped_column(String(50), default=Role.APPLICANT.value)
    college_id: Mapped[str | None] = mapped_column(ForeignKey("colleges.id"), nullable=True)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    college = relationship("College", back_populates="users")
    applications = relationship("EthicsApplication", back_populates="applicant")


class EthicsApplication(Base):
    __tablename__ = "applications"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=uuid4_str)
    reference_no: Mapped[str | None] = mapped_column(String(50), unique=True, index=True, nullable=True)
    applicant_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    college_id: Mapped[str] = mapped_column(ForeignKey("colleges.id"), index=True)
    title: Mapped[str] = mapped_column(String(500))
    programme: Mapped[str | None] = mapped_column(String(200), nullable=True)
    department: Mapped[str | None] = mapped_column(String(200), nullable=True)
    applicant_type: Mapped[str] = mapped_column(String(100), default="Student")
    study_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(80), default=AppStatus.DRAFT.value, index=True)
    secretariat_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    applicant = relationship("User", back_populates="applications")
    college = relationship("College", back_populates="applications")
    documents = relationship("ApplicationDocument", back_populates="application", cascade="all, delete-orphan")
    access_requests = relationship("CollegeAccessRequest", back_populates="application", cascade="all, delete-orphan")
    assignments = relationship("ReviewerAssignment", back_populates="application", cascade="all, delete-orphan")
    status_history = relationship("StatusHistory", back_populates="application", cascade="all, delete-orphan")


class ApplicationDocument(Base):
    __tablename__ = "application_documents"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=uuid4_str)
    application_id: Mapped[str] = mapped_column(ForeignKey("applications.id"), index=True)
    document_type: Mapped[str] = mapped_column(String(100))
    original_name: Mapped[str] = mapped_column(String(255))
    stored_name: Mapped[str] = mapped_column(String(255), unique=True)
    version: Mapped[int] = mapped_column(Integer, default=1)
    active_for_college: Mapped[bool] = mapped_column(Boolean, default=False)
    uploaded_by: Mapped[str] = mapped_column(ForeignKey("users.id"))
    uploaded_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    application = relationship("EthicsApplication", back_populates="documents")


class CollegeAccessRequest(Base):
    __tablename__ = "college_access_requests"
    __table_args__ = (UniqueConstraint("application_id", "status", name="uq_access_request_active"),)
    id: Mapped[str] = mapped_column(String, primary_key=True, default=uuid4_str)
    application_id: Mapped[str] = mapped_column(ForeignKey("applications.id"), index=True)
    requested_by: Mapped[str] = mapped_column(ForeignKey("users.id"))
    status: Mapped[str] = mapped_column(String(30), default="pending")
    request_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    decision_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    decided_by: Mapped[str | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    requested_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    application = relationship("EthicsApplication", back_populates="access_requests")


class ReviewerAssignment(Base):
    __tablename__ = "reviewer_assignments"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=uuid4_str)
    application_id: Mapped[str] = mapped_column(ForeignKey("applications.id"), index=True)
    reviewer_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    level: Mapped[str] = mapped_column(String(30), default="college")
    assignment_type: Mapped[str] = mapped_column(String(30), default="primary")
    status: Mapped[str] = mapped_column(String(30), default="assigned")
    comments: Mapped[str | None] = mapped_column(Text, nullable=True)
    recommendation: Mapped[str | None] = mapped_column(String(80), nullable=True)
    assigned_by: Mapped[str] = mapped_column(ForeignKey("users.id"))
    assigned_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    application = relationship("EthicsApplication", back_populates="assignments")
    reviewer = relationship("User", foreign_keys=[reviewer_id])


class ReviewAssignmentMeta(Base):
    __tablename__ = "review_assignment_meta"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=uuid4_str)
    assignment_id: Mapped[str] = mapped_column(ForeignKey("reviewer_assignments.id"), unique=True, index=True)
    due_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    assignment = relationship("ReviewerAssignment")


class ReviewerDeclaration(Base):
    __tablename__ = "reviewer_declarations"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=uuid4_str)
    assignment_id: Mapped[str] = mapped_column(ForeignKey("reviewer_assignments.id"), unique=True, index=True)
    declaration: Mapped[str] = mapped_column(String(30))  # clear | conflict
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    declared_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    assignment = relationship("ReviewerAssignment")


class CollegeDecision(Base):
    __tablename__ = "college_decisions"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=uuid4_str)
    application_id: Mapped[str] = mapped_column(ForeignKey("applications.id"), index=True)
    decision: Mapped[str] = mapped_column(String(80))
    comments: Mapped[str | None] = mapped_column(Text, nullable=True)
    meeting_reference: Mapped[str | None] = mapped_column(String(120), nullable=True)
    decided_by: Mapped[str] = mapped_column(ForeignKey("users.id"))
    decided_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    application = relationship("EthicsApplication")


class IRBClassification(Base):
    __tablename__ = "irb_classifications"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=uuid4_str)
    application_id: Mapped[str] = mapped_column(ForeignKey("applications.id"), index=True)
    classification: Mapped[str] = mapped_column(String(30))  # exempt | expedited | full_board
    rationale: Mapped[str | None] = mapped_column(Text, nullable=True)
    classified_by: Mapped[str] = mapped_column(ForeignKey("users.id"))
    classified_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    application = relationship("EthicsApplication")


class IRBMeeting(Base):
    __tablename__ = "irb_meetings"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=uuid4_str)
    meeting_no: Mapped[str] = mapped_column(String(80), unique=True, index=True)
    meeting_date: Mapped[datetime] = mapped_column(DateTime)
    venue: Mapped[str | None] = mapped_column(String(255), nullable=True)
    meeting_link: Mapped[str | None] = mapped_column(String(500), nullable=True)
    status: Mapped[str] = mapped_column(String(30), default="scheduled")
    created_by: Mapped[str] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    items = relationship("IRBMeetingItem", back_populates="meeting", cascade="all, delete-orphan")


class IRBMeetingItem(Base):
    __tablename__ = "irb_meeting_items"
    __table_args__ = (UniqueConstraint("meeting_id", "application_id", name="uq_meeting_application"),)
    id: Mapped[str] = mapped_column(String, primary_key=True, default=uuid4_str)
    meeting_id: Mapped[str] = mapped_column(ForeignKey("irb_meetings.id"), index=True)
    application_id: Mapped[str] = mapped_column(ForeignKey("applications.id"), index=True)
    agenda_no: Mapped[str | None] = mapped_column(String(30), nullable=True)
    added_by: Mapped[str] = mapped_column(ForeignKey("users.id"))
    added_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    meeting = relationship("IRBMeeting", back_populates="items")
    application = relationship("EthicsApplication")


class IRBDecision(Base):
    __tablename__ = "irb_decisions"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=uuid4_str)
    application_id: Mapped[str] = mapped_column(ForeignKey("applications.id"), index=True)
    decision: Mapped[str] = mapped_column(String(50))
    conditions: Mapped[str | None] = mapped_column(Text, nullable=True)
    meeting_id: Mapped[str | None] = mapped_column(ForeignKey("irb_meetings.id"), nullable=True)
    decided_by: Mapped[str] = mapped_column(ForeignKey("users.id"))
    decided_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    application = relationship("EthicsApplication")
    meeting = relationship("IRBMeeting")


class ClearanceCertificate(Base):
    __tablename__ = "clearance_certificates"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=uuid4_str)
    application_id: Mapped[str] = mapped_column(ForeignKey("applications.id"), index=True)
    certificate_no: Mapped[str] = mapped_column(String(80), unique=True, index=True)
    verification_token: Mapped[str] = mapped_column(String(120), unique=True, index=True)
    issue_date: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    expiry_date: Mapped[datetime] = mapped_column(DateTime)
    status: Mapped[str] = mapped_column(String(30), default="valid")
    conditions: Mapped[str | None] = mapped_column(Text, nullable=True)
    issued_by: Mapped[str] = mapped_column(ForeignKey("users.id"))
    pdf_stored_name: Mapped[str | None] = mapped_column(String(255), nullable=True)

    application = relationship("EthicsApplication")


class IRBProcessingReset(Base):
    """Administrative correction that restores IRB processing to a known valid point.

    Earlier classifications remain in the audit record but are treated as superseded for
    operational routing when they pre-date the latest reset.
    """
    __tablename__ = "irb_processing_resets"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=uuid4_str)
    application_id: Mapped[str] = mapped_column(ForeignKey("applications.id"), index=True)
    restored_status: Mapped[str] = mapped_column(String(80), default=AppStatus.RETURNED_TO_IRB.value)
    reason: Mapped[str] = mapped_column(Text)
    corrected_by: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    reset_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True)

    application = relationship("EthicsApplication")
    officer = relationship("User", foreign_keys=[corrected_by])


class EthicalApprovalRecord(Base):
    """Authorised ethical approval and subsequent Board ratification record.

    College-pathway applications may receive an administrative ethical approval pending
    formal Board ratification. Direct/final IRB approvals are also recorded here so the
    approved-work register has one durable source of truth.
    """
    __tablename__ = "ethical_approval_records"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=uuid4_str)
    application_id: Mapped[str] = mapped_column(ForeignKey("applications.id"), index=True)
    approval_type: Mapped[str] = mapped_column(String(50), index=True)  # conditional_pending_board | final_irb
    status: Mapped[str] = mapped_column(String(50), default="pending_ratification", index=True)
    approving_authority: Mapped[str] = mapped_column(String(120))
    approved_by: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    recorded_by: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    approval_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    approved_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True)
    certificate_id: Mapped[str | None] = mapped_column(ForeignKey("clearance_certificates.id"), nullable=True, index=True)
    ratification_meeting_id: Mapped[str | None] = mapped_column(ForeignKey("irb_meetings.id"), nullable=True, index=True)
    ratification_decision: Mapped[str | None] = mapped_column(String(60), nullable=True)
    ratification_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    ratified_by: Mapped[str | None] = mapped_column(ForeignKey("users.id"), nullable=True, index=True)
    ratification_recorded_by: Mapped[str | None] = mapped_column(ForeignKey("users.id"), nullable=True, index=True)
    ratified_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, index=True)

    application = relationship("EthicsApplication")
    approving_officer = relationship("User", foreign_keys=[approved_by])
    recording_officer = relationship("User", foreign_keys=[recorded_by])
    certificate = relationship("ClearanceCertificate")
    ratification_meeting = relationship("IRBMeeting")
    ratifying_officer = relationship("User", foreign_keys=[ratified_by])
    ratification_recorder = relationship("User", foreign_keys=[ratification_recorded_by])


class PostApprovalRequest(Base):
    __tablename__ = "post_approval_requests"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=uuid4_str)
    application_id: Mapped[str] = mapped_column(ForeignKey("applications.id"), index=True)
    request_type: Mapped[str] = mapped_column(String(40))  # amendment | renewal | adverse_event | closure
    summary: Mapped[str] = mapped_column(Text)
    supporting_document_id: Mapped[str | None] = mapped_column(ForeignKey("application_documents.id"), nullable=True)
    status: Mapped[str] = mapped_column(String(30), default="pending", index=True)
    submitted_by: Mapped[str] = mapped_column(ForeignKey("users.id"))
    submitted_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    decided_by: Mapped[str | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    decision_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    application = relationship("EthicsApplication")
    supporting_document = relationship("ApplicationDocument")


class StatusHistory(Base):
    __tablename__ = "status_history"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=uuid4_str)
    application_id: Mapped[str] = mapped_column(ForeignKey("applications.id"), index=True)
    from_status: Mapped[str | None] = mapped_column(String(80), nullable=True)
    to_status: Mapped[str] = mapped_column(String(80))
    changed_by: Mapped[str] = mapped_column(ForeignKey("users.id"))
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    application = relationship("EthicsApplication", back_populates="status_history")


class AuditLog(Base):
    __tablename__ = "audit_logs"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=uuid4_str)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    application_id: Mapped[str | None] = mapped_column(ForeignKey("applications.id"), nullable=True, index=True)
    action: Mapped[str] = mapped_column(String(120))
    detail: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True)


class ReviewAssignmentBatch(Base):
    """One secure reviewer workspace can carry one or many application assignments."""
    __tablename__ = "review_assignment_batches"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=uuid4_str)
    reference: Mapped[str] = mapped_column(String(80), unique=True, index=True)
    reviewer_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    level: Mapped[str] = mapped_column(String(30), index=True)  # college | irb
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    link_expires_at: Mapped[datetime] = mapped_column(DateTime)
    due_at: Mapped[datetime] = mapped_column(DateTime)
    message: Mapped[str | None] = mapped_column(Text, nullable=True)
    email_status: Mapped[str] = mapped_column(String(30), default="pending")
    last_email_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    last_accessed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    access_count: Mapped[int] = mapped_column(Integer, default=0)
    resend_count: Mapped[int] = mapped_column(Integer, default=0)
    created_by: Mapped[str] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    reviewer = relationship("User", foreign_keys=[reviewer_id])
    items = relationship("ReviewAssignmentBatchItem", back_populates="batch", cascade="all, delete-orphan")


class ReviewAssignmentBatchItem(Base):
    __tablename__ = "review_assignment_batch_items"
    __table_args__ = (
        UniqueConstraint("batch_id", "assignment_id", name="uq_review_batch_item"),
        UniqueConstraint("assignment_id", name="uq_review_assignment_in_batch"),
    )
    id: Mapped[str] = mapped_column(String, primary_key=True, default=uuid4_str)
    batch_id: Mapped[str] = mapped_column(ForeignKey("review_assignment_batches.id"), index=True)
    assignment_id: Mapped[str] = mapped_column(ForeignKey("reviewer_assignments.id"), index=True)
    work_no: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    batch = relationship("ReviewAssignmentBatch", back_populates="items")
    assignment = relationship("ReviewerAssignment")


class ReviewAssignmentDocument(Base):
    """Application documents explicitly selected by an assigning office for one reviewer assignment."""
    __tablename__ = "review_assignment_documents"
    __table_args__ = (UniqueConstraint("assignment_id", "document_id", name="uq_review_assignment_document"),)
    id: Mapped[str] = mapped_column(String, primary_key=True, default=uuid4_str)
    assignment_id: Mapped[str] = mapped_column(ForeignKey("reviewer_assignments.id"), index=True)
    document_id: Mapped[str] = mapped_column(ForeignKey("application_documents.id"), index=True)
    selected_by: Mapped[str] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    assignment = relationship("ReviewerAssignment")
    document = relationship("ApplicationDocument")


class ReviewReportDocument(Base):
    """Files submitted by a scientific or IRB reviewer for one assigned application."""
    __tablename__ = "review_report_documents"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=uuid4_str)
    assignment_id: Mapped[str] = mapped_column(ForeignKey("reviewer_assignments.id"), index=True)
    document_kind: Mapped[str] = mapped_column(String(40))  # review_report | annotated_protocol | supporting
    original_name: Mapped[str] = mapped_column(String(255))
    stored_name: Mapped[str] = mapped_column(String(255), unique=True)
    uploaded_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    assignment = relationship("ReviewerAssignment")


class ReviewReportFileBlob(Base):
    """Database-backed copy of reviewer-uploaded files.

    The normal copy remains in private file storage for efficient serving. This blob is a
    resilience fallback so a reviewer report remains available if a Render redeploy or
    storage-mount problem temporarily removes the filesystem copy.
    """
    __tablename__ = "review_report_file_blobs"
    review_document_id: Mapped[str] = mapped_column(ForeignKey("review_report_documents.id"), primary_key=True)
    content: Mapped[bytes] = mapped_column(LargeBinary)
    media_type: Mapped[str | None] = mapped_column(String(120), nullable=True)
    size_bytes: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    document = relationship("ReviewReportDocument")


class SecretariatDocumentCheck(Base):
    """Persisted Secretariat screening check for each submitted application document."""
    __tablename__ = "secretariat_document_checks"
    __table_args__ = (UniqueConstraint("application_id", "document_id", name="uq_secretariat_document_check"),)
    id: Mapped[str] = mapped_column(String, primary_key=True, default=uuid4_str)
    application_id: Mapped[str] = mapped_column(ForeignKey("applications.id"), index=True)
    document_id: Mapped[str] = mapped_column(ForeignKey("application_documents.id"), index=True)
    verified: Mapped[bool] = mapped_column(Boolean, default=False)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    checked_by: Mapped[str | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    checked_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    application = relationship("EthicsApplication")
    document = relationship("ApplicationDocument")


class SecretariatAttentionRequest(Base):
    """College request asking the IRB Secretariat to attend to a first submission still awaiting screening."""
    __tablename__ = "secretariat_attention_requests"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=uuid4_str)
    application_id: Mapped[str] = mapped_column(ForeignKey("applications.id"), index=True)
    college_id: Mapped[str] = mapped_column(ForeignKey("colleges.id"), index=True)
    requested_by: Mapped[str] = mapped_column(ForeignKey("users.id"))
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(30), default="pending", index=True)
    requested_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    resolved_by: Mapped[str | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    resolution_note: Mapped[str | None] = mapped_column(Text, nullable=True)

    application = relationship("EthicsApplication")
    college = relationship("College")


class ApplicationSubmission(Base):
    """Tracks fresh and revised submission events without overwriting the application record."""
    __tablename__ = "application_submissions"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=uuid4_str)
    application_id: Mapped[str] = mapped_column(ForeignKey("applications.id"), index=True)
    submission_kind: Mapped[str] = mapped_column(String(40), index=True)  # fresh | college_revision | irb_revision
    round_no: Mapped[int] = mapped_column(Integer, default=1)
    submitted_by: Mapped[str] = mapped_column(ForeignKey("users.id"))
    submitted_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True)

    application = relationship("EthicsApplication")


class DocumentSubmissionMeta(Base):
    """Marks whether a file belongs to the fresh submission or a later College/IRB revision round."""
    __tablename__ = "document_submission_meta"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=uuid4_str)
    document_id: Mapped[str] = mapped_column(ForeignKey("application_documents.id"), unique=True, index=True)
    application_id: Mapped[str] = mapped_column(ForeignKey("applications.id"), index=True)
    submission_kind: Mapped[str] = mapped_column(String(40), default="fresh", index=True)
    round_no: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    document = relationship("ApplicationDocument")
    application = relationship("EthicsApplication")


class ReviewerContact(Base):
    """Reviewer contact entered by a College/IRB office for secure email assignment.

    proxy_user_id links into the existing assignment engine while the real reviewer email remains
    separate from login accounts, so an applicant can also be a reviewer without an email collision.
    """
    __tablename__ = "reviewer_contacts"
    __table_args__ = (UniqueConstraint("level", "college_id", "email", name="uq_reviewer_contact_scope_email"),)
    id: Mapped[str] = mapped_column(String, primary_key=True, default=uuid4_str)
    proxy_user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), unique=True, index=True)
    level: Mapped[str] = mapped_column(String(30), index=True)
    college_id: Mapped[str | None] = mapped_column(ForeignKey("colleges.id"), nullable=True, index=True)
    title: Mapped[str | None] = mapped_column(String(40), nullable=True)
    first_name: Mapped[str] = mapped_column(String(100))
    last_name: Mapped[str] = mapped_column(String(100))
    email: Mapped[str] = mapped_column(String(255), index=True)
    phone: Mapped[str | None] = mapped_column(String(80), nullable=True)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_by: Mapped[str] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    proxy_user = relationship("User", foreign_keys=[proxy_user_id])
    college = relationship("College")


class SecurityEvent(Base):
    """Security-relevant events. IP/email are HMAC-hashed to reduce sensitive log data."""
    __tablename__ = "security_events"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=uuid4_str)
    event_type: Mapped[str] = mapped_column(String(80), index=True)
    user_id: Mapped[str | None] = mapped_column(ForeignKey("users.id"), nullable=True, index=True)
    subject_hash: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    ip_hash: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    user_agent_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    detail: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True)
