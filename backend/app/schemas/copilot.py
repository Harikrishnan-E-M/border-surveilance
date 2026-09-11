"""AI Surveillance Copilot Pydantic schemas.

Request and response models for the conversational AI copilot that
provides natural-language access to surveillance data, analytics,
and system health information.
"""

from __future__ import annotations


from datetime import datetime
from typing import Any, Literal, Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


# -- Chat Request / Response ------------------------------------------------


class CopilotChatRequest(BaseModel):
    """Request body for sending a message to the AI copilot."""

    message: str = Field(
        ...,
        min_length=1,
        max_length=4096,
        description="The user message to send to the copilot.",
        examples=["How many alerts were triggered today?"],
    )
    conversation_id: Optional[str] = Field(
        default=None,
        description=(
            "Existing conversation ID to continue. "
            "If omitted a new conversation is started."
        ),
        examples=["conv_abc123"],
    )


class DataCard(BaseModel):
    """A structured data visualization card returned alongside the AI response.

    Cards allow the copilot to surface tables, charts, stat summaries,
    alert lists, and camera grids inline within the conversation.
    """

    type: Literal["table", "chart", "stat", "alert_list", "camera_grid"] = Field(
        ...,
        description="Card visualization type.",
        examples=["table"],
    )
    title: str = Field(
        ...,
        description="Human-readable card title.",
        examples=["Alerts by Severity (Last 24h)"],
    )
    data: Any = Field(
        ...,
        description=(
            "Card payload. Structure depends on ``type``: "
            "table -> {headers: [], rows: []}, "
            "chart -> {labels: [], datasets: []}, "
            "stat -> {label, value, change_pct, trend}, "
            "alert_list -> [{id, title, severity, ...}], "
            "camera_grid -> [{id, name, status, ...}]."
        ),
    )


class CopilotChatResponse(BaseModel):
    """Response returned after the copilot processes a user message."""

    response_text: str = Field(
        ...,
        description="The copilot's natural-language response (Markdown supported).",
    )
    conversation_id: str = Field(
        ...,
        description="Conversation identifier (new or existing).",
    )
    data_cards: list[DataCard] = Field(
        default_factory=list,
        description="Optional structured data cards to render inline.",
    )
    suggested_followups: list[str] = Field(
        default_factory=list,
        description="Suggested follow-up questions the user can click.",
    )


# -- Conversation Management ------------------------------------------------


class ConversationMessage(BaseModel):
    """A single message within a conversation."""

    role: Literal["user", "assistant"] = Field(
        ...,
        description="Who sent the message.",
    )
    content: str = Field(
        ...,
        description="Message text.",
    )
    data_cards: list[DataCard] = Field(
        default_factory=list,
        description="Data cards attached to assistant messages.",
    )
    timestamp: str = Field(
        ...,
        description="ISO-8601 timestamp when the message was sent.",
    )


class ConversationDetail(BaseModel):
    """Full conversation with all messages."""

    id: str = Field(..., description="Conversation identifier.")
    title: str = Field(..., description="Auto-generated conversation title.")
    messages: list[ConversationMessage] = Field(
        default_factory=list,
        description="Ordered list of messages in the conversation.",
    )
    message_count: int = Field(
        ...,
        description="Total number of messages.",
    )
    created_at: str = Field(..., description="Conversation creation timestamp.")
    updated_at: str = Field(..., description="Last activity timestamp.")


class ConversationSummary(BaseModel):
    """Lightweight summary of a conversation for list views."""

    id: str = Field(..., description="Conversation identifier.")
    title: str = Field(
        ...,
        description="Auto-generated title from the first user message.",
        examples=["Alert summary for today"],
    )
    last_message: str = Field(
        ...,
        description="Truncated last message preview.",
    )
    message_count: int = Field(
        ...,
        description="Total messages in the conversation.",
        examples=[12],
    )
    created_at: str = Field(
        ...,
        description="ISO-8601 creation timestamp.",
    )
    updated_at: str = Field(
        ...,
        description="ISO-8601 last activity timestamp.",
    )


class ConversationListResponse(BaseModel):
    """Response for the list-conversations endpoint."""

    status: str = Field(default="success")
    data: list[ConversationSummary] = Field(
        default_factory=list,
        description="List of conversation summaries.",
    )


# -- Suggested Questions ---------------------------------------------------


class SuggestedQuestion(BaseModel):
    """A context-aware suggested question for the copilot."""

    question: str = Field(
        ...,
        description="The suggested question text.",
        examples=["What cameras are currently offline?"],
    )
    category: str = Field(
        ...,
        description="Question category for grouping.",
        examples=["cameras", "alerts", "analytics", "system"],
    )
    icon: str = Field(
        ...,
        description="Lucide icon name for the frontend.",
        examples=["camera", "bell", "bar-chart-2", "activity"],
    )


class SuggestedQuestionsResponse(BaseModel):
    """Response for the suggestions endpoint."""

    status: str = Field(default="success")
    data: list[SuggestedQuestion] = Field(
        default_factory=list,
        description="List of suggested questions.",
    )
