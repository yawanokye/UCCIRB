from __future__ import annotations
from datetime import datetime
from enum import Enum
import uuid

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
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
    IRB_CHAIR = "irb_chair"
    SUPERADMIN = "superadmin"


class AppStatus(str, Enum):
    DRAFT = "draft"
    SUBMITTED = "submitted_to_irb_secretariat"
    SECRETARIAT_SCREENING = "irb_secretariat_screening"
    RETURNED_ADMIN = "returned_to_applicant_admin"
    ADMIN_COMPLETE = "administratively_complete"
    VISIBLE_TO_COLLEGE = "visible_to_college"
    COLLEGE_ACCESS_REQUESTED = "college_review_access_requested"
    COLLEGE_REVIEW_AUTHORISED = "college_review_authorised"
    AWAITING_COLLEGE_REVIEWER = "awaiting_college_reviewer_assignment"
    COLLEGE_REVIEW = "under_college_scientific_review"
    COLLEGE_REVISION = "college_revision_required"
    COLLEGE_REVISED = "college_revised_submission_received"
    AWAITING_COLLEGE_DECISION = "awaiting_college_decision"
    SCIENTIFICALLY_RECOMMENDED = "scientifically_recommended"
    RETURNED_TO_IRB = "returned_to_irb_secretariat"
    IRB_CLASSIFICATION = "irb_review_classification"
    AWAITING_IRB_REVIEWER = "awaiting_irb_reviewer_assignment"
    IRB_REVIEW = "under_irb_ethical_review"
    IRB_REVISION = "irb_revision_required"
    IRB_REVISED = "irb_revised_submission_received"
    FULL_BOARD = "scheduled_for_full_board_review"
    AWAITING_FINAL_DECISION = "awaiting_final_irb_decision"
    APPROVED = "approved"
    APPROVED_CONDITIONS = "approved_with_conditions"
    DEFERRED = "deferred"
    REJECTED = "rejected"
    CLEARANCE_ISSUED = "ethical_clearance_issued"
    ACTIVE = "active_study"
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
