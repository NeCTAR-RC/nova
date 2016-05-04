# Copyright (c) 2014 The University of Melbourne
# All Rights Reserved
#
#    Licensed under the Apache License, Version 2.0 (the "License"); you may
#    not use this file except in compliance with the License. You may obtain
#    a copy of the License at
#
#         http://www.apache.org/licenses/LICENSE-2.0
#
#    Unless required by applicable law or agreed to in writing, software
#    distributed under the License is distributed on an "AS IS" BASIS, WITHOUT
#    WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied. See the
#    License for the specific language governing permissions and limitations
#    under the License.

from sqlalchemy import MetaData, Column, Table
from sqlalchemy import Text


def upgrade(migrate_engine):
    """Function adds cell capabilities field."""
    meta = MetaData(bind=migrate_engine)

    cells = Table('cells', meta, autoload=True)
    shadow_cells = Table('shadow_cells', meta, autoload=True)

    capabilities = Column('capabilities', Text)
    cells.create_column(capabilities)
    shadow_cells.create_column(capabilities.copy())

    migrate_engine.execute(cells.update().
                           values(capabilities='{}'))
    migrate_engine.execute(shadow_cells.update().
                           values(capabilities='{}'))


def downgrade(migrate_engine):
    """Function removes cell capabilities field."""
    meta = MetaData(bind=migrate_engine)
    cells = Table('cells', meta, autoload=True)
    shadow_cells = Table('shadow_cells', meta, autoload=True)

    cells.c.capabilities.drop()
    shadow_cells.c.capabilities.drop()
