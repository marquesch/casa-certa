"""price quotes per group size

Revision ID: da6f61dd4ee4
Revises: b6b419520522
Create Date: 2026-10-31 13:13:53.913980

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'da6f61dd4ee4'
down_revision: Union[str, Sequence[str], None] = 'b6b419520522'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# Dropping a column also drops the unique constraint that used it (SQLite batch mode rebuilds the table).
QUOTE_KEY = ['house_id', 'check_in', 'check_out']


def upgrade() -> None:
    """Replace adults/children by guests (their sum), allow unavailable (null) totals, record the source."""
    with op.batch_alter_table('price_quote', schema=None) as batch_op:
        batch_op.add_column(sa.Column('guests', sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column('source', sa.String(length=40), nullable=True))
    op.execute('UPDATE price_quote SET guests = adults + children')
    with op.batch_alter_table('price_quote', schema=None) as batch_op:
        batch_op.alter_column('guests', existing_type=sa.Integer(), nullable=False)
        batch_op.alter_column('total_cents', existing_type=sa.Integer(), nullable=True)
        batch_op.drop_column('adults')
        batch_op.drop_column('children')
        batch_op.create_unique_constraint('uq_price_quote_guests', [*QUOTE_KEY, 'guests', 'infants'])


def downgrade() -> None:
    """Back to adults/children (all guests as adults). Unavailable quotes have no old equivalent and are dropped."""
    with op.batch_alter_table('price_quote', schema=None) as batch_op:
        batch_op.add_column(sa.Column('adults', sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column('children', sa.Integer(), nullable=True))
    op.execute('UPDATE price_quote SET adults = guests, children = 0')
    op.execute('DELETE FROM price_quote WHERE total_cents IS NULL')
    with op.batch_alter_table('price_quote', schema=None) as batch_op:
        batch_op.alter_column('adults', existing_type=sa.Integer(), nullable=False)
        batch_op.alter_column('children', existing_type=sa.Integer(), nullable=False)
        batch_op.alter_column('total_cents', existing_type=sa.Integer(), nullable=False)
        batch_op.drop_column('guests')
        batch_op.drop_column('source')
        batch_op.create_unique_constraint('uq_price_quote_people', [*QUOTE_KEY, 'adults', 'children', 'infants'])
