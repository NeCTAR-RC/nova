# Copyright 2012 OpenStack LLC.
# All Rights Reserved.
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
"""
Fakes For Cells tests.

We make up this list of fake cells, hosts and managers:

supposing the test set flags.cell_name to 'cell0':

cell0
    cell4
    cell5
    cell2
        grandchild

"""

from nova.cells import manager
from nova.cells import rpcapi as cells_rpcapi
from nova.cells import utils as cells_utils
from nova import context
from nova import flags

FLAGS = flags.FLAGS

MY_CELL_NAME = FLAGS.cell_name
"""FAKE_CELLS is basically the database content for all cell info.

In real life, each cell has its own database of just the adjacent cells.
If a cell has is_parent=1 it means that's a parent cell, otherwise a
child cell.

each entry in FAKE_CELLS points to a fake DB entry for one of the cells; look
at

    def _cell_get_all(self, context):

below and in nova.cells.manager to see how it's used in the tests.
"""
FAKE_CELLS = {}

FAKE_CELLS_REFRESH = [dict(id=5, name='cell1', is_parent=False),
                      dict(id=4, name='cell2', is_parent=True),
                      dict(id=3, name='cell3', is_parent=True),
                      dict(id=1, name='cell5', is_parent=False),
                      dict(id=7, name='cell6', is_parent=False)]

TEST_METHOD_EXPECTED_KWARGS = {'kwarg1': 10, 'kwarg2': 20}
TEST_METHOD_EXPECTED_RESULT = 'test_method_expected_result'

FAKE_CELL_MANAGERS = {}


def init():
    global FAKE_CELLS
    global FAKE_CELL_MANAGERS
    global FAKE_CELL_NAME

    # cell_name could have been changed after this module was loaded
    MY_CELL_NAME = FLAGS.cell_name
    FAKE_CELLS = {
            MY_CELL_NAME: [dict(id=1, name='cell1', is_parent=True),
                           dict(id=2, name='cell2', is_parent=False),
                           dict(id=3, name='cell3', is_parent=True),
                           dict(id=4, name='cell4', is_parent=False),
                           dict(id=5, name='cell5', is_parent=False)],
            'cell2': [dict(id=1, name=MY_CELL_NAME, is_parent=True),
                      dict(id=2, name='grandchild', is_parent=False)],
            'grandchild': [dict(id=1, name='cell2', is_parent=True)]}
    FAKE_CELL_MANAGERS = {}


class FakeCellsScheduler(object):
    def __init__(self, manager, *args, **kwargs):
        pass

    @property
    def our_path(self):
        return self.manager.our_path

    def schedule_test_method(self):
        pass


class FakeCellsDriver(object):
    def __init__(self, manager, *args, **kwargs):
        self._test_call_info = {'test_method': 0, 'send_message': 0,
                'send_message_fanout': 0}
        pass

    def send_message_to_cell(self, context, cell_info, dest_host, message,
                             rpc_proxy, fanout=False, topic=None):
        pass


class FakeCellsRpcAPI(cells_rpcapi.CellsAPI):
    def __init__(self, *args, **kwargs):
        self._test_call_info = {'send_message': 0, 'send_message_fanout': 0}
        super(FakeCellsRpcAPI, self).__init__(*args, **kwargs)

    def send_message_to_cell(self, context, cell, message,
            dest_host=None, fanout=False, topic=None):
        self._test_call_info['send_message'] += 1
        if fanout:
            self._test_call_info['send_message_fanout'] += 1
        mgr = FAKE_CELL_MANAGERS.get(cell.name)
        if mgr:
            method = getattr(mgr, message['method'])
            method(context, **message['args'])


class FakeCellsManager(manager.CellsManager):
    def __init__(self, *args, **kwargs):
        self._test_case = kwargs.pop('_test_case')
        _my_name = kwargs.pop('_my_name')
        _my_host = kwargs.pop('_my_host', FLAGS.host)
        self._test_call_info = {'test_method': 0}
        # Pretend to have a capacity
        self.capacities = kwargs.pop('capacities', {})
        super(FakeCellsManager, self).__init__(**kwargs)
        self.cells_rpcapi = FakeCellsRpcAPI()
        # Now fudge some things for testing
        self.my_cell_info.name = _my_name
        self.our_path = cells_utils.PATH_CELL_HOST_SEP.join(
                [_my_name, _my_host])
        self._refresh_cells_from_db(context.get_admin_context())
        FAKE_CELL_MANAGERS[_my_name] = self
        # Fudge a unique host
        my_host = "host%s" % len(FAKE_CELL_MANAGERS)
        for i, cell in enumerate(self.child_cells.values() +
                self.parent_cells.values()):
            if cell.name not in FAKE_CELL_MANAGERS:
                # This will end up stored in FAKE_CELL_MANAGERS
                FakeCellsManager(*args, _test_case=self._test_case,
                        _my_name=cell.name,
                        _my_host=my_host, **kwargs)

    def _ask_children_for_capabilities(self, context):
        pass

    def _update_our_capacity(self, context):
        self.my_cell_info.update_capacities(self.capacities)

    def _cell_get_all(self, context):
        return FAKE_CELLS.get(self.my_cell_info.name, [])

    def test_method(self, context, routing_path, **kwargs):
        self._test_case.assertEqual(kwargs, TEST_METHOD_EXPECTED_KWARGS)
        self._test_call_info['test_method'] += 1
        self._test_call_info['routing_path'] = routing_path
        return TEST_METHOD_EXPECTED_RESULT


def stubout_cell_get_all_for_refresh(mgr):
    def _cell_get_all(context):
        return FAKE_CELLS_REFRESH

    mgr._test_case.stubs.Set(mgr, '_cell_get_all', _cell_get_all)
    return FAKE_CELLS_REFRESH


def find_a_child_cell(my_name):
    cells = FAKE_CELLS[my_name]
    return [cell for cell in cells if not cell['is_parent']][0]


def find_a_parent_cell(my_name):
    cells = FAKE_CELLS[my_name]
    return [cell for cell in cells if cell['is_parent']][0]
