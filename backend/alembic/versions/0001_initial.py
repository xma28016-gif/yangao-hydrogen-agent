"""Stage 2-1 schema; preserve this revision when later models grow."""

import sqlalchemy as sa
from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    def common():
        return [
            sa.Column("id", sa.String(), primary_key=True),
            sa.Column("owner_id", sa.String(), nullable=False),
            sa.Column("created_at", sa.Float(), nullable=False),
        ]

    columns = {
        "documents": [
            ("filename", sa.String(), False),
            ("stored_name", sa.String(), False),
            ("sha256", sa.String(), False),
            ("size", sa.Integer(), False),
            ("extension", sa.String(), False),
            ("status", sa.String(), False),
            ("chunks_count", sa.Integer(), False),
            ("error_code", sa.String(), True),
        ],
        "chunks": [
            ("document_id", sa.String(), False),
            ("ordinal", sa.Integer(), False),
            ("text", sa.Text(), False),
            ("embedding", sa.JSON(), True),
            ("fingerprint", sa.String(), True),
        ],
        "reports": [
            ("title", sa.String(), False),
            ("target_words", sa.Integer(), False),
            ("user_requirements", sa.Text(), False),
            ("outline", sa.JSON(), False),
            ("notices", sa.JSON(), False),
            ("status", sa.String(), False),
            ("version", sa.Integer(), False),
            ("confirmed_at", sa.Float(), True),
        ],
        "tasks": [
            ("kind", sa.String(), False),
            ("entity_id", sa.String(), False),
            ("status", sa.String(), False),
            ("active_key", sa.String(), True),
            ("worker_id", sa.String(), True),
            ("lease_until", sa.Float(), False),
            ("progress", sa.String(), False),
            ("error_code", sa.String(), True),
            ("attempts", sa.Integer(), False),
        ],
        "model_calls": [
            ("report_id", sa.String(), True),
            ("step", sa.String(), False),
            ("model", sa.String(), False),
            ("month", sa.String(), False),
            ("charged_micro", sa.Integer(), False),
            ("status", sa.String(), False),
            ("request_id", sa.String(), False),
            ("input_tokens", sa.Integer(), True),
            ("output_tokens", sa.Integer(), True),
            ("elapsed", sa.Float(), False),
            ("retries", sa.Integer(), False),
            ("validation", sa.String(), False),
        ],
        "events": [("report_id", sa.String(), False), ("name", sa.String(), False), ("details", sa.JSON(), False)],
    }
    for name, fields in columns.items():
        args = common() + [sa.Column(n, t, nullable=nullable) for n, t, nullable in fields]
        if name == "tasks":
            args.append(sa.UniqueConstraint("active_key"))
        op.create_table(name, *args)
        op.create_index(f"ix_{name}_owner_id", name, ["owner_id"])
    op.create_index("ix_tasks_status", "tasks", ["status"])
    op.create_index("ix_chunks_document_id", "chunks", ["document_id"])


def downgrade():
    # Downgrading deletes business data, so it is intentionally never automatic.
    raise RuntimeError("破坏性降级需要用户批准及备份方案")
