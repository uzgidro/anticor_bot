"""matrix bridge: users.matrix_id, nullable tg_id, matrix_deliveries

Revision ID: 0002
Revises: 0001
Create Date: 2026-09-11
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0002"
down_revision: Union[str, None] = "0001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # batch mode so the same revision works on SQLite (tests) and Postgres.
    with op.batch_alter_table("users") as batch:
        batch.alter_column("tg_id", existing_type=sa.BigInteger(), nullable=True)
        batch.add_column(sa.Column("matrix_id", sa.String(length=255), nullable=True))
        batch.create_unique_constraint("uq_users_matrix_id", ["matrix_id"])
        batch.create_check_constraint(
            "ck_users_identity", "tg_id IS NOT NULL OR matrix_id IS NOT NULL"
        )

    op.create_table(
        "matrix_deliveries",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("submission_id", sa.Integer(), nullable=False),
        sa.Column("room_id", sa.String(length=255), nullable=False),
        sa.Column("event_id", sa.String(length=255), nullable=False),
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["submission_id"], ["submissions.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("room_id", "event_id"),
    )
    op.create_index(
        op.f("ix_matrix_deliveries_submission_id"), "matrix_deliveries", ["submission_id"]
    )


def downgrade() -> None:
    # tg_id cannot become NOT NULL again while Matrix-only users exist.
    remaining = op.get_bind().execute(
        sa.text("SELECT COUNT(*) FROM users WHERE tg_id IS NULL")
    ).scalar()
    if remaining:
        raise RuntimeError(
            f"Cannot downgrade: {remaining} Matrix-only user(s) have no tg_id. "
            "Delete them first."
        )
    op.drop_index(op.f("ix_matrix_deliveries_submission_id"), table_name="matrix_deliveries")
    op.drop_table("matrix_deliveries")
    with op.batch_alter_table("users") as batch:
        batch.drop_constraint("ck_users_identity", type_="check")
        batch.drop_constraint("uq_users_matrix_id", type_="unique")
        batch.drop_column("matrix_id")
        batch.alter_column("tg_id", existing_type=sa.BigInteger(), nullable=False)
