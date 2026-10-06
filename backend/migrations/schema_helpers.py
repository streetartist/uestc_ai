"""Accept compatible tables already created by AUTO_CREATE_SCHEMA on reload."""
from alembic import op
import sqlalchemy as sa


def ensure_table(name, *declarations):
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if not inspector.has_table(name):
        op.create_table(name, *declarations)
        return
    actual = {column['name']: column for column in inspector.get_columns(name)}
    for column in declarations:
        if not isinstance(column, sa.Column):
            continue
        saved = actual.get(column.name)
        if (saved is None or saved['nullable'] != column.nullable
                or saved['type'].compile(dialect=bind.dialect) != column.type.compile(dialect=bind.dialect)):
            raise RuntimeError(f'Existing table {name} has incompatible column {column.name}')


def ensure_index(name, table, columns):
    found = next((item for item in sa.inspect(op.get_bind()).get_indexes(table) if item['name'] == name), None)
    if found is None:
        op.create_index(name, table, columns)
    elif found['column_names'] != columns or found['unique']:
        raise RuntimeError(f'Existing index {name} is incompatible')
