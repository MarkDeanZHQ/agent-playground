"""memory retrieval feedback fields

Revision ID: 0002_memory_retrieval_feedback_fields
Revises: 0001_initial_schema
Create Date: 2026-06-18
"""
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0002_memory_retrieval_feedback_fields"
down_revision: str | None = "0001_initial_schema"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("memories", sa.Column("use_count", sa.Integer(), nullable=False, server_default="0"))
    op.add_column("memories", sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("memories", sa.Column("conflict_key", sa.String(length=120), nullable=True))
    op.create_index(op.f("ix_memories_conflict_key"), "memories", ["conflict_key"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_memories_conflict_key"), table_name="memories")
    op.drop_column("memories", "conflict_key")
    op.drop_column("memories", "last_used_at")
    op.drop_column("memories", "use_count")
