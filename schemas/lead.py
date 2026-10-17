from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, field_validator

Priority = Literal["High", "Medium", "Low"]


class LeadSubmission(BaseModel):
    """Arguments of the `submit_lead` tool, filled in by the model."""

    client_name: str = Field(description="Client's name")
    contact_info: str = Field(
        description="Phone, email, or explicit confirmation that Telegram is OK for contact"
    )
    project_description: str = Field(description="What the client wants to build")
    budget_or_timeline: str = Field(description="Approximate budget and/or deadline")
    ai_summary: str = Field(
        description="1-2 sentence summary of the project for the manager"
    )
    lead_priority: Priority = Field(
        description="High / Medium / Low based on budget, urgency and specificity"
    )
    category: str = Field(
        description='e.g. "Telegram Bot", "AI Agent", "CRM Integration", "Other"'
    )

    @field_validator(
        "client_name",
        "contact_info",
        "project_description",
        "budget_or_timeline",
        "ai_summary",
        "category",
        mode="after",
    )
    @classmethod
    def _not_blank(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("must not be empty")
        return v


class Lead(LeadSubmission):
    """A lead enriched with Telegram metadata, ready to be stored."""

    created_at: datetime
    telegram_id: int
    telegram_username: str | None = None
    status: str = "New"

    @property
    def username_display(self) -> str:
        return f"@{self.telegram_username}" if self.telegram_username else "—"
