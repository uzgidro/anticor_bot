"""matrix DMs: users.matrix_room_id

Revision ID: 0003
Revises: 0002
Create Date: 2026-09-14
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0003"
down_revision: Union[str, None] = "0002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # batch mode so the same revision works on SQLite (tests) and Postgres.
    with op.batch_alter_table("users") as batch:
        batch.add_column(sa.Column("matrix_room_id", sa.String(length=255), nullable=True))
        batch.create_unique_constraint("uq_users_matrix_room_id", ["matrix_room_id"])


def downgrade() -> None:
    with op.batch_alter_table("users") as batch:
        batch.drop_constraint("uq_users_matrix_room_id", type_="unique")
        batch.drop_column("matrix_room_id")
