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

from unittest import mock

from oslo_utils.fixture import uuidsentinel as uuids

from nova.compute import migration_list
from nova import context as nova_context
from nova import exception
from nova import test


class TestMigrationListMarker(test.NoDBTestCase):
    """get_marker_record must not disown a marker in an unreachable cell.

    The marker is looked up with a scatter across the cells; a cell that
    did not answer may hold the marker, so MarkerNotFound (a 400 telling
    the client the marker it was handed on the previous page is invalid)
    would be a lie. Only a miss from every responsive cell with no
    unreachable cells is a genuine MarkerNotFound.
    """

    def setUp(self):
        super(TestMigrationListMarker, self).setUp()
        self.context = nova_context.RequestContext()
        self.lister = migration_list.MigrationLister(None, None)

    @mock.patch('nova.context.scatter_gather_skip_cell0')
    def test_marker_unreachable_cell_raises_cell_timeout(self, mock_sg):
        mock_sg.return_value = {
            uuids.cell1: exception.MigrationNotFound(migration_id=1),
            uuids.cell2: nova_context.did_not_respond_sentinel,
        }

        self.assertRaises(exception.CellTimeout,
                          self.lister.get_marker_record,
                          self.context, uuids.marker)

    @mock.patch('nova.context.scatter_gather_skip_cell0')
    def test_marker_down_exception_raises_cell_timeout(self, mock_sg):
        mock_sg.return_value = {
            uuids.cell1: exception.MigrationNotFound(migration_id=1),
            uuids.cell2: exception.CellTimeout(),
        }

        self.assertRaises(exception.CellTimeout,
                          self.lister.get_marker_record,
                          self.context, uuids.marker)

    @mock.patch('nova.context.scatter_gather_skip_cell0')
    def test_marker_missing_everywhere_raises_marker_not_found(self, mock_sg):
        mock_sg.return_value = {
            uuids.cell1: exception.MigrationNotFound(migration_id=1),
            uuids.cell2: exception.MigrationNotFound(migration_id=1),
        }

        self.assertRaises(exception.MarkerNotFound,
                          self.lister.get_marker_record,
                          self.context, uuids.marker)

    @mock.patch('nova.context.scatter_gather_skip_cell0')
    def test_marker_found_despite_unreachable_cell(self, mock_sg):
        """The marker resolving anywhere trumps an unreachable sibling."""
        db_migration = {'uuid': uuids.marker}
        mock_sg.return_value = {
            uuids.cell1: nova_context.did_not_respond_sentinel,
            uuids.cell2: db_migration,
        }

        cell_uuid, record = self.lister.get_marker_record(
            self.context, uuids.marker)

        self.assertEqual(uuids.cell2, cell_uuid)
        self.assertEqual(db_migration, record)
