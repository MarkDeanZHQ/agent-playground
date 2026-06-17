"""session summaries

Revision ID: 0003_session_summaries
Revises: 0002_memory_retrieval_feedback_fields
Create Date: 2026-06-18
"""
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0003_session_summaries"
down_revision: str | None = "0002_memory_retrieval_feedback_fields"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "session_summaries",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("session_id", sa.String(), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("covered_message_count", sa.Integer(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["session_id"], ["sessions.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("session_id"),
        if_not_exists=True,
    )
    op.create_index(
        op.f("ix_session_summaries_session_id"),
        "session_summaries",
        ["session_id"],
        unique=True,
        if_not_exists=True,
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_session_summaries_session_id"), table_name="session_summaries")
    op.drop_table("session_summaries")
