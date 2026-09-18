"""An item remembers which engagement it belongs to, by the accounting system's id.

An engagement is a product in QuickBooks now (decision 36), and its client and
consultant names come from that product's category path. Names get tidied:
renaming the category from `MasTec` to `MasTec Inc` would have orphaned every
item in flight, because items were found by consultant and client name alone.

The accounting system's id for the engagement does not change when a name does,
so the item carries it and is found by it. The names stay on the item as the
label a person reads, and are refreshed when they change (decision 39).

Null means an item made before this, or one made in manual mode, where there is
no accounting system to have an id in. Those are still found by name.

Revision ID: 0004
Revises: 0003
Create Date: 2026-09-18
"""

import sqlalchemy as sa
from alembic import op

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("items") as batch:
        batch.add_column(sa.Column("engagement_ref", sa.String(), nullable=True))
    op.create_index("ix_items_engagement_ref", "items", ["engagement_ref"])


def downgrade() -> None:
    op.drop_index("ix_items_engagement_ref", table_name="items")
    with op.batch_alter_table("items") as batch:
        batch.drop_column("engagement_ref")
