"""Yaptığın değişikliğin kısa notu

Revision ID: 44e2a871cd80
Revises: 616e5eb8da3a
Create Date: 2025-09-05 19:51:38.736367

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '44e2a871cd80'
down_revision = '616e5eb8da3a'
branch_labels = None
depends_on = None


def upgrade():
    # sadece code alanını stringe çevir ve unique constraint ekle
    with op.batch_alter_table('product', schema=None) as batch_op:
        batch_op.alter_column('code',
               existing_type=sa.INTEGER(),
               type_=sa.String(length=20),
               existing_nullable=False)
        batch_op.create_unique_constraint('_code_company_uc', ['code', 'company_id'])



    # ### end Alembic commands ###


def downgrade():
    with op.batch_alter_table('product', schema=None) as batch_op:
        batch_op.drop_constraint('_code_company_uc', type_='unique')
        batch_op.alter_column('code',
               existing_type=sa.String(length=20),
               type_=sa.INTEGER(),
               existing_nullable=False)


    # ### end Alembic commands ###
