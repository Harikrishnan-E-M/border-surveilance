"""Initial schema - create all tables.

Revision ID: 001_initial
Revises: None
Create Date: 2026-02-25

This migration creates the entire VisionAI database schema from
Base.metadata. All model tables, indexes, and constraints are
created in a single migration for a clean initial deployment.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "001_initial"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Create all tables from SQLAlchemy metadata.

    Uses the connection-bound metadata.create_all() to emit
    CREATE TABLE statements for every registered model, respecting
    foreign-key ordering automatically.
    """
    # Import all models to register them with Base.metadata
    import app.models  # noqa: F401
    from app.database import Base

    bind = op.get_bind()
    Base.metadata.create_all(bind=bind, checkfirst=True)

    # Ensure pgvector extension is available (for face embeddings / CLIP)
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")


def downgrade() -> None:
    """Drop all tables in reverse dependency order."""
    import app.models  # noqa: F401
    from app.database import Base

    bind = op.get_bind()
    Base.metadata.drop_all(bind=bind, checkfirst=True)
