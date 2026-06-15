# This file defines the reports table.
# Every generated PDF or Markdown report is tracked here.

from sqlalchemy import Column, Integer, String, Text, DateTime, ForeignKey, Enum
from sqlalchemy.sql import func
import enum
from app.db.session import Base


class ReportType(str, enum.Enum):
    EXECUTIVE = "executive"   # short summary for business people
    TECHNICAL = "technical"   # detailed report with all agent findings
    WEEKLY = "weekly"         # weekly summary across investigations


class ReportFormat(str, enum.Enum):
    PDF = "pdf"
    MARKDOWN = "markdown"


class Report(Base):
    __tablename__ = "reports"

    id = Column(Integer, primary_key=True, autoincrement=True)

    # Which investigation this report belongs to
    investigation_id = Column(Integer, ForeignKey("investigations.id"), nullable=False)

    # Which user generated this report
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)

    report_type = Column(Enum(ReportType), nullable=False)
    report_format = Column(Enum(ReportFormat), nullable=False)

    # The file path on disk where the report is saved
    # e.g. D:/Business Intelligent System/data/reports/report_5.pdf
    file_path = Column(String(500), nullable=True)

    # A short title for the report
    title = Column(String(255), nullable=True)

    # The full report content stored as text as well
    content = Column(Text, nullable=True)

    created_at = Column(DateTime, server_default=func.now())

    def __repr__(self):
        return f"<Report id={self.id} type={self.report_type}>"