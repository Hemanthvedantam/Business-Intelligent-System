# This file defines the investigations table.
# Every time a user asks a question and the agents run,
# that whole process is called an investigation and stored here.

from sqlalchemy import Column, Integer, String, Text, JSON, DateTime, ForeignKey, Enum
from sqlalchemy.sql import func
import enum
from app.db.session import Base


# These are the possible states an investigation can be in
class InvestigationStatus(str, enum.Enum):
    PENDING = "pending"       # just created, not started yet
    RUNNING = "running"       # agents are currently working
    COMPLETED = "completed"   # agents finished successfully
    FAILED = "failed"         # something went wrong


class Investigation(Base):
    __tablename__ = "investigations"

    id = Column(Integer, primary_key=True, autoincrement=True)

    # Which user started this investigation
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)

    # The question the user asked — e.g. "Why are sales dropping?"
    question = Column(Text, nullable=False)

    # What domain was detected — e.g. "sales", "logistics", "healthcare"
    domain = Column(String(100), nullable=True)

    # Current state of the investigation
    status = Column(
        Enum(InvestigationStatus),
        default=InvestigationStatus.PENDING,
        nullable=False
    )

    # Which dataset file was used for this investigation
    dataset_filename = Column(String(255), nullable=True)

    # Data quality report stored as JSON
    # Contains missing values, duplicates, outliers found
    data_quality_report = Column(JSON, nullable=True)

    # All agent steps and their outputs stored as JSON
    # Each step looks like: {"agent": "planner", "output": "..."}
    agent_steps = Column(JSON, nullable=True)

    # The root causes found — stored as JSON list
    root_causes = Column(JSON, nullable=True)

    # The recommendations made — stored as JSON list
    recommendations = Column(JSON, nullable=True)

    # The evidence graph data for the frontend to visualize
    evidence_graph = Column(JSON, nullable=True)

    # Final summary in plain business language
    final_summary = Column(Text, nullable=True)

    # If something failed, the error message is stored here
    error_message = Column(Text, nullable=True)

    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())

    def __repr__(self):
        return f"<Investigation id={self.id} status={self.status}>"