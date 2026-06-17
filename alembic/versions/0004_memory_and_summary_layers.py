"""memory and summary layers

Revision ID: 0004_memory_and_summary_layers
Revises: 0003_session_summaries
Create Date: 2026-06-18
"""
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0004_memory_and_summary_layers"
down_revision: str | None = "0003_session_summaries"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("session_summaries", sa.Column("summary_json", sa.Text(), nullable=False, server_default="{}"))
    op.add_column("memories", sa.Column("scope", sa.String(length=20), nullable=False, server_default="project"))
    op.add_column("memories", sa.Column("category", sa.String(length=50), nullable=False, server_default="preference"))
    op.add_column("memories", sa.Column("source_kind", sa.String(length=50), nullable=False, server_default="manual"))
    op.add_column("memories", sa.Column("confidence", sa.Integer(), nullable=False, server_default="3"))
    op.add_column("memories", sa.Column("session_id", sa.String(), nullable=True))
    op.add_column("memories", sa.Column("owner_id", sa.String(length=120), nullable=True))
    op.add_column("memories", sa.Column("sensitivity", sa.String(length=20), nullable=False, server_default="public"))
    op.add_column("memories", sa.Column("supersedes_memory_id", sa.String(), nullable=True))
    op.add_column("memories", sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True))
    op.create_index(op.f("ix_memories_session_id"), "memories", ["session_id"], unique=False)
    op.create_index(op.f("ix_memories_supersedes_memory_id"), "memories", ["supersedes_memory_id"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_memories_supersedes_memory_id"), table_name="memories")
    op.drop_index(op.f("ix_memories_session_id"), table_name="memories")
    op.drop_column("memories", "expires_at")
    op.drop_column("memories", "supersedes_memory_id")
    op.drop_column("memories", "sensitivity")
    op.drop_column("memories", "owner_id")
    op.drop_column("memories", "session_id")
    op.drop_column("memories", "confidence")
    op.drop_column("memories", "source_kind")
    op.drop_column("memories", "category")
    op.drop_column("memories", "scope")
    op.drop_column("session_summaries", "summary_json")
