"""Add writing checkpoints without modifying existing product records."""

import sqlalchemy as sa
from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("reports", sa.Column("truth_base", sa.JSON(), nullable=True))
    op.add_column("reports", sa.Column("corpus_ids", sa.JSON(), nullable=True))
    columns = [
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("owner_id", sa.String(), nullable=False),
        sa.Column("created_at", sa.Float(), nullable=False),
        sa.Column("report_id", sa.String(), nullable=False),
        sa.Column("chapter_id", sa.Integer(), nullable=False),
        sa.Column("title", sa.String(), nullable=False),
        sa.Column("suggested_words", sa.Integer(), nullable=False),
        sa.Column("max_words", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("phase", sa.String(), nullable=False),
    ]
    for name in ("body", "original_body", "summary", "last_ending", "user_notice"):
        columns.append(sa.Column(name, sa.Text(), nullable=False))
    columns.extend(
        [
            sa.Column("retrieved", sa.JSON(), nullable=True),
            sa.Column("missing", sa.JSON(), nullable=False),
            sa.Column("audit", sa.JSON(), nullable=False),
            sa.Column("rewrite_count", sa.Integer(), nullable=False),
            sa.Column("truncated", sa.Boolean(), nullable=False),
            sa.Column("edited", sa.Boolean(), nullable=False),
            sa.Column("version", sa.Integer(), nullable=False),
            sa.UniqueConstraint("report_id", "chapter_id"),
        ]
    )
    op.create_table("chapters", *columns)
    op.create_index("ix_chapters_report_id", "chapters", ["report_id"])
    op.create_index("ix_chapters_owner_id", "chapters", ["owner_id"])


def downgrade():
    raise RuntimeError("含业务数据的降级需要用户批准及备份方案")
