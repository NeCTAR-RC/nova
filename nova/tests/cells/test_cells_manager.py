# Copyright (c) 2012 Openstack, LLC
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
Tests For CellsManager
"""
import datetime
import inspect
import random
import time
import copy
import mock

from nova.cells import manager as cells_manager
from nova.cells import utils as cells_utils
from nova.cells import consistency
from nova import context
from nova import db
from nova import exception
from nova import flags
from nova.openstack.common.rpc import common as rpc_common
from nova.openstack.common import timeutils
from nova import test
from nova.tests.cells import fakes

flags.DECLARE('cells', 'nova.cells.opts')
FLAGS = flags.FLAGS


class CellsManagerClassTestCase(test.TestCase):
    """Test case for CellsManager class"""

    def setUp(self):
        super(CellsManagerClassTestCase, self).setUp()
        self.flags(host='fake.host.name')
        self.flags(name='me', group='cells')
        fakes.init()

        self.cells_manager = fakes.FakeCellsManager(
                _test_case=self,
                _my_name=FLAGS.cells.name,
                cells_driver_cls=fakes.FakeCellsDriver,
                cells_scheduler_cls=fakes.FakeCellsScheduler)

    def test_setup(self):
        self.assertEqual(self.cells_manager.my_cell_info.name,
                FLAGS.cells.name)
        self.assertTrue(self.cells_manager.my_cell_info.is_me)

    def test_refresh_cells(self):
        fake_context = 'fake_context'

        def verify_cells(cells):
            total_cells_found = (len(self.cells_manager.child_cells) +
                    len(self.cells_manager.parent_cells))
            for cell in cells:
                if cell['is_parent']:
                    self.assertIn(cell['name'],
                            self.cells_manager.parent_cells)
                else:
                    self.assertIn(cell['name'],
                            self.cells_manager.child_cells)
            self.assertEqual(len(cells), total_cells_found)

        verify_cells(fakes.FAKE_CELLS[FLAGS.cells.name])

        # Different list of cells
        fakes.stubout_cell_get_all_for_refresh(self.cells_manager)
        self.cells_manager._refresh_cells_from_db(fake_context)
        verify_cells(fakes.FAKE_CELLS_REFRESH)

    def _find_next_hop(self, dest_cell_name, routing_path, direction):
        return self.cells_manager._find_next_hop(dest_cell_name,
                routing_path, direction)

    def test_find_next_hop_is_me(self):
        cell_info, _host = self._find_next_hop('a!b!c', 'a!b!c', 'up')
        self.assertTrue(cell_info.is_me)
        cell_info, _host = self._find_next_hop('a!b!c', 'a!b!c', 'down')
        self.assertTrue(cell_info.is_me)
        cell_info, _host = self._find_next_hop('a', 'a', 'up')
        self.assertTrue(cell_info.is_me)
        cell_info, _host = self._find_next_hop('a', 'a', 'down')
        self.assertTrue(cell_info.is_me)

    def test_find_next_hop_inconsistency(self):
        self.assertRaises(exception.CellRoutingInconsistency,
                self._find_next_hop, 'a!b!d', 'a!b!c', 'up')
        self.assertRaises(exception.CellRoutingInconsistency,
                self._find_next_hop, 'a!b!d', 'a!b!c', 'down')
        # Too many hops in routing path
        self.assertRaises(exception.CellRoutingInconsistency,
                self._find_next_hop, 'a!b', 'a.b!c', 'down')
        self.assertRaises(exception.CellRoutingInconsistency,
                self._find_next_hop, 'a!b', 'a.b!c', 'up')

    def test_find_next_hop_child_not_found(self):
        dest_cell = 'me.notfound'
        routing_path = 'me'
        self.assertRaises(exception.CellRoutingInconsistency,
                self._find_next_hop, dest_cell, routing_path, 'down')

    def test_find_next_hop_parent_not_found(self):
        dest_cell = 'me.notfound'
        routing_path = 'me'
        self.assertRaises(exception.CellRoutingInconsistency,
                self._find_next_hop, dest_cell, routing_path, 'up')

    def test_find_next_hop_direct_child_cell(self):
        # Find a child cell that we stubbed
        child_cell = fakes.find_a_child_cell(FLAGS.cells.name)

        dest_cell = FLAGS.cells.name + '!' + child_cell['name']
        routing_path = 'me'

        cell_info, _host = self._find_next_hop(dest_cell, routing_path,
                'down')
        self.assertEqual(cell_info.name, child_cell['name'])

    def test_find_next_hop_grandchild_cell(self):
        # Find a child cell that we stubbed
        child_cell = fakes.find_a_child_cell(FLAGS.cells.name)

        dest_cell = FLAGS.cells.name + '!' + child_cell['name'] + '!grandchild'
        routing_path = 'me'

        cell_info, _host = self._find_next_hop(dest_cell, routing_path,
                'down')
        self.assertEqual(cell_info.name, child_cell['name'])

    def test_find_next_hop_direct_parent_cell(self):
        # Find a parent cell that we stubbed
        parent_cell = fakes.find_a_parent_cell(FLAGS.cells.name)

        # When going up, the path is reversed
        dest_cell = FLAGS.cells.name + '!' + parent_cell['name']
        routing_path = 'me'
        cell_info, _host = self._find_next_hop(dest_cell, routing_path,
                'up')
        self.assertEqual(cell_info.name, parent_cell['name'])

        # Multi-level
        dest_cell = 'a!b!me!' + parent_cell['name']
        routing_path = 'a!b!me'
        cell_info, _host = self._find_next_hop(dest_cell, routing_path,
                'up')
        self.assertEqual(cell_info.name, parent_cell['name'])

    def test_find_next_hop_grandparent_cell(self):
        # Find a parent cell that we stubbed
        parent_cell = fakes.find_a_parent_cell(FLAGS.cells.name)

        # When going up, the path is reversed
        dest_cell = (FLAGS.cells.name + '!' + parent_cell['name'] +
                '!grandparent')
        routing_path = 'me'
        cell_info, _host = self._find_next_hop(dest_cell, routing_path,
                'up')
        self.assertEqual(cell_info.name, parent_cell['name'])

        # Multi-level
        dest_cell = 'a!b!me!' + parent_cell['name'] + '!grandparent'
        routing_path = 'a!b!me'
        cell_info, _host = self._find_next_hop(dest_cell, routing_path,
                'up')
        self.assertEqual(cell_info.name, parent_cell['name'])

    def test_route_message_to_self_happy_day(self):
        """Test happy day call to my cell returning a response."""

        fake_context = 'fake_context'
        message = {'method': 'test_method',
                   'args': fakes.TEST_METHOD_EXPECTED_KWARGS}
        args = {'dest_cell_name': FLAGS.cells.name,
                'routing_path': None,
                'direction': 'down',
                'message': message,
                'need_response': True}

        result = self.cells_manager.route_message(fake_context, **args)
        self.assertEqual(result, fakes.TEST_METHOD_EXPECTED_RESULT)

    def test_route_message_to_grandchild_happy_day(self):
        """Test happy day call to grandchild cell returning a response."""
        fake_context = 'fake_context'

        message = {'method': 'test_method',
                   'args': fakes.TEST_METHOD_EXPECTED_KWARGS}
        args = {'dest_cell_name': 'me!cell2!grandchild',
                'routing_path': None,
                'direction': 'down',
                'message': message,
                'need_response': True}

        z2_mgr = fakes.FAKE_CELL_MANAGERS['cell2']
        orig_send = z2_mgr.cells_rpcapi.send_message_to_cell
        info = {}

        def send_message_to_cell(context, cell, message,
                dest_host=None, topic=None):
            # Catch response coming up and store the dest_host
            # so we can make sure responses send to the
            # appropriate host queue of the src
            if cell.name == 'me':
                info['dest_host'] = dest_host
            return orig_send(context, cell, message,
                    dest_host=dest_host)

        self.stubs.Set(z2_mgr.cells_rpcapi, 'send_message_to_cell',
                send_message_to_cell)

        result = self.cells_manager.route_message(fake_context, **args)
        self.assertEqual(result, fakes.TEST_METHOD_EXPECTED_RESULT)
        self.assertEqual(info['dest_host'], FLAGS.host)

    def test_route_message_to_grandchild_with_exception(self):
        """Test call to grandchild cell raising an exception."""
        fake_context = 'fake_context'

        gc_mgr = fakes.FAKE_CELL_MANAGERS['grandchild']

        def fake_test_method(context, **kwargs):
            raise Exception('exception in grandchild')

        self.stubs.Set(gc_mgr, 'test_method', fake_test_method)

        message = {'method': 'test_method',
                   'args': fakes.TEST_METHOD_EXPECTED_KWARGS}
        args = {'dest_cell_name': 'me!cell2!grandchild',
                'routing_path': None,
                'direction': 'down',
                'message': message,
                'need_response': True}

        try:
            self.cells_manager.route_message(fake_context, **args)
        except rpc_common.RemoteError, e:
            self.assertIn('exception in grandchild', str(e))
        else:
            self.fail("rpc.common.RemoteError not raised")

    def test_broadcast_message_down(self):
        """Test broadcast to all child/grandchild cells."""
        fake_context = 'fake_context'

        bcast_message = cells_utils.form_broadcast_message('down',
                'test_method', fakes.TEST_METHOD_EXPECTED_KWARGS)
        self.assertEqual(bcast_message['method'], 'broadcast_message')

        self.cells_manager.broadcast_message(fake_context,
                **bcast_message['args'])

        self.assertEqual(self.cells_manager.cells_rpcapi._test_call_info[
            'send_message'], len(self.cells_manager._get_child_cells()))
        self.assertEqual(self.cells_manager._test_call_info['test_method'], 1)
        z2_mgr = fakes.FAKE_CELL_MANAGERS['cell2']
        self.assertEqual(z2_mgr.cells_rpcapi._test_call_info['send_message'],
                len(z2_mgr._get_child_cells()))
        self.assertEqual(z2_mgr._test_call_info['test_method'], 1)
        gc_mgr = fakes.FAKE_CELL_MANAGERS['grandchild']
        self.assertEqual(gc_mgr.cells_rpcapi._test_call_info['send_message'],
                0)
        self.assertEqual(gc_mgr._test_call_info['test_method'], 1)

    def test_broadcast_message_up(self):
        """Test broadcast from grandchild cells up."""
        fake_context = 'fake_context'

        gc_mgr = fakes.FAKE_CELL_MANAGERS['grandchild']
        bcast_message = cells_utils.form_broadcast_message('up',
                'test_method', fakes.TEST_METHOD_EXPECTED_KWARGS)
        self.assertEqual(bcast_message['method'], 'broadcast_message')

        gc_mgr.broadcast_message(fake_context, **bcast_message['args'])

        self.assertEqual(gc_mgr.cells_rpcapi._test_call_info['send_message'],
                len(gc_mgr._get_parent_cells()))
        self.assertEqual(gc_mgr._test_call_info['test_method'], 1)
        z2_mgr = fakes.FAKE_CELL_MANAGERS['cell2']
        self.assertEqual(z2_mgr.cells_rpcapi._test_call_info['send_message'],
                len(z2_mgr._get_parent_cells()))
        self.assertEqual(z2_mgr._test_call_info['test_method'], 1)
        self.assertEqual(self.cells_manager.cells_rpcapi._test_call_info[
            'send_message'], len(self.cells_manager._get_parent_cells()))
        self.assertEqual(self.cells_manager._test_call_info['test_method'], 1)

    def test_broadcast_message_max_hops(self):
        """Test broadcast stops when reaching max hops."""
        self.flags(max_broadcast_hop_count=1, group='cells')
        fake_context = 'fake_context'

        bcast_message = cells_utils.form_broadcast_message('down',
                'test_method', fakes.TEST_METHOD_EXPECTED_KWARGS)
        self.assertEqual(bcast_message['method'], 'broadcast_message')

        self.cells_manager.broadcast_message(fake_context,
                **bcast_message['args'])

        self.assertEqual(self.cells_manager.cells_rpcapi._test_call_info[
            'send_message'], len(self.cells_manager._get_child_cells()))
        self.assertEqual(self.cells_manager._test_call_info['test_method'], 1)
        z2_mgr = fakes.FAKE_CELL_MANAGERS['cell2']
        self.assertEqual(z2_mgr.cells_rpcapi._test_call_info['send_message'],
                len(z2_mgr._get_child_cells()))
        self.assertEqual(z2_mgr._test_call_info['test_method'], 1)
        gc_mgr = fakes.FAKE_CELL_MANAGERS['grandchild']
        self.assertEqual(gc_mgr.cells_rpcapi._test_call_info['send_message'],
                0)
        self.assertEqual(gc_mgr._test_call_info['test_method'], 0)

    def test_run_service_api_method(self):
        compute_api = self.cells_manager.api_map['compute']

        call_info = {'compute': 0}

        fake_instance = 'fake_instance'
        fake_context = 'fake_context'

        def fake_instance_get(*args, **kwargs):
            return fake_instance

        self.stubs.Set(db, 'instance_get_by_uuid', fake_instance_get)

        def compute_method(context, instance, arg1, arg2,
                kwarg1=None, kwarg2=None):
            self.assertEqual(instance, fake_instance)
            self.assertEqual(context, fake_context)
            self.assertEqual(arg1, 1)
            self.assertEqual(arg2, 2)
            self.assertEqual(kwarg1, 3)
            self.assertEqual(kwarg2, 4)
            call_info['compute'] += 1

        compute_api.compute_method = compute_method

        method_info = {'method': 'compute_method',
                       'method_args': ('uuid', 1, 2),
                       'method_kwargs': {'kwarg1': 3, 'kwarg2': 4}}
        self.cells_manager.run_service_api_method(fake_context,
                'compute', method_info)

    def test_run_service_api_method_unknown_instance(self):
        compute_api = self.cells_manager.api_map['compute']

        fake_context = 'fake_context'
        info = {'compute_called': 0, 'bcast_message': {}}

        def fake_instance_get(*args, **kwargs):
            raise exception.InstanceNotFound(instance_id='uuid')

        self.stubs.Set(db, 'instance_get_by_uuid', fake_instance_get)

        def compute_method(*args, **kwargs):
            info['compute_called'] += 1

        compute_api.compute_method = compute_method

        def fake_broadcast_message(context, **kwargs):
            info['bcast_message'] = kwargs

        self.stubs.Set(self.cells_manager, 'broadcast_message',
                fake_broadcast_message)

        method_info = {'method': 'compute_method',
                       'method_args': ('uuid', 1, 2),
                       'method_kwargs': {'kwarg1': 3, 'kwarg2': 4}}
        self.assertRaises(exception.InstanceNotFound,
                self.cells_manager.run_service_api_method, fake_context,
                'compute', method_info)
        expected_bcast_message = {
                'routing_path': None,
                'hopcount': 0,
                'fanout': False,
                'message': {'args': {'instance_info': {'uuid': 'uuid'}},
                            'method': 'instance_destroy'},
                'direction': 'up'}
        self.assertEqual(info['compute_called'], 0)
        self.assertEqual(info['bcast_message'], expected_bcast_message)

    def test_run_service_api_method_unknown_service(self):
        self.assertRaises(exception.CellServiceAPIMethodNotFound,
                self.cells_manager.run_service_api_method, 'fake_context',
                'unknown', None)

    def test_run_service_api_method_unknown(self):
        method_info = {'method': 'unknown'}
        self.assertRaises(exception.CellServiceAPIMethodNotFound,
                self.cells_manager.run_service_api_method, 'fake_context',
                'compute', method_info)

    def test_instance_update(self):
        fake_context = context.RequestContext('moo', 'cow')

        instance_info = {'uuid': 'fake_uuid', 'updated_at': 'foo'}
        call_info = {'instance_update': 0}

        def fake_instance_update(context, uuid, values, update_cells=True):
            expected_values = instance_info.copy()
            # Need to make sure the correct cell ended up in here based
            # on the routing path.  Since updates flow up, the cell
            # name is the reverse of the routing path
            expected_values['cell_name'] = 'a!b!c!d!e'
            self.assertEqual(uuid, instance_info['uuid'])
            self.assertEqual(values, expected_values)
            call_info['instance_update'] += 1

        self.stubs.Set(db, 'instance_update', fake_instance_update)

        # We have a parent listed in the default cell_get_all, so reset
        # this so we'll update
        self.cells_manager.parent_cells = {}
        self.cells_manager.instance_update(fake_context, instance_info,
                routing_path='e!d!c!b!a')
        self.assertEqual(call_info['instance_update'], 1)

    def test_instance_update_ignored_when_not_at_top(self):
        fake_context = 'fake_context'

        instance_info = {'uuid': 'fake_uuid', 'updated_at': 'foo'}
        call_info = {'instance_update': 0}

        def fake_instance_update(context, uuid, values, update_cells=True):
            call_info['instance_update'] += 1

        self.stubs.Set(db, 'instance_update', fake_instance_update)

        # We have a parent listed in the default cell_get_all
        self.cells_manager.instance_update(fake_context, instance_info,
                routing_path='some_child.me')
        self.assertEqual(call_info['instance_update'], 0)

    def test_instance_update_when_doesnt_exist(self):
        fake_context = context.RequestContext('moo', 'cow')

        instance_info = {'uuid': 'fake_uuid', 'updated_at': 'foo'}
        call_info = {'instance_update': 0, 'instance_create': 0}

        def fake_instance_update(context, uuid, values, update_cells=True):
            expected_values = instance_info.copy()
            # Need to make sure the correct cell ended up in here based
            # on the routing path.  Since updates flow up, the cell
            # name is the reverse of the routing path
            expected_values['cell_name'] = 'a!b!c!d!e'
            # Also, the context should be able to read_deleted
            self.assertEqual(context.read_deleted, 'yes')
            self.assertEqual(uuid, instance_info['uuid'])
            self.assertEqual(values, expected_values)
            call_info['instance_update'] += 1
            raise exception.InstanceNotFound()

        def fake_instance_create(context, values):
            self.assertEqual(values, instance_info)
            call_info['instance_create'] += 1

        self.stubs.Set(db, 'instance_update', fake_instance_update)
        self.stubs.Set(db, 'instance_create', fake_instance_create)

        # We have a parent listed in the default cell_get_all, so reset
        # this so we'll update
        self.cells_manager.parent_cells = {}
        self.cells_manager.instance_update(fake_context, instance_info,
                routing_path='e!d!c!b!a')
        self.assertEqual(call_info['instance_update'], 1)
        self.assertEqual(call_info['instance_create'], 1)

    def test_instance_destroy(self):
        fake_context = 'fake_context'

        instance_info = {'uuid': 'fake_uuid'}
        call_info = {'instance_destroy': 0}

        def fake_instance_destroy(context, uuid, update_cells=True):
            self.assertEqual(uuid, instance_info['uuid'])
            call_info['instance_destroy'] += 1

        self.stubs.Set(db, 'instance_destroy', fake_instance_destroy)

        # We have a parent listed in the default cell_get_all, so reset
        # this so we'll update
        self.cells_manager.parent_cells = {}
        self.cells_manager.instance_destroy(fake_context, instance_info,
                routing_path='some_child!me')
        self.assertEqual(call_info['instance_destroy'], 1)

    def test_instance_destroy_ignored_when_not_at_top(self):
        fake_context = 'fake_context'

        instance_info = {'uuid': 'fake_uuid'}
        call_info = {'instance_destroy': 0}

        def fake_instance_destroy(context, uuid, update_cells=True):
            call_info['instance_destroy'] += 1

        self.stubs.Set(db, 'instance_destroy', fake_instance_destroy)

        # We have a parent listed in the default cell_get_all
        self.cells_manager.instance_destroy(fake_context, instance_info)
        self.assertEqual(call_info['instance_destroy'], 0)

    def test_send_raw_message_to_cell_passes_to_driver(self):
        # We can't use self.cells_manager because it has stubbed
        # send_raw_message_to_cell
        mgr = cells_manager.CellsManager(
                cells_driver_cls=fakes.FakeCellsDriver,
                cells_scheduler_cls=fakes.FakeCellsScheduler)

        fake_context = 'fake_context'
        fake_cell = 'fake_cell'
        fake_message = {'method': 'fake_method', 'args': {}}
        call_info = {'send_message': 0}

        def fake_send_message_to_cell(context, cell, dest_host, message,
                rpc_proxy, fanout=False, topic=None):
            self.assertEqual(context, fake_context)
            self.assertEqual(cell, fake_cell)
            self.assertEqual(message, fake_message)
            self.assertEqual(rpc_proxy, mgr.cells_rpcapi)
            call_info['send_message'] += 1

        self.stubs.Set(mgr.cells_rpcapi.driver, 'send_message_to_cell',
                fake_send_message_to_cell)

        mgr.cells_rpcapi.send_message_to_cell(fake_context, fake_cell,
                fake_message)
        self.assertEqual(call_info['send_message'], 1)

    def test_schedule_calls_get_proxied(self):

        call_info = {'sched_test_method': 0}

        method_kwargs = {'test_arg': 123, 'test_arg2': 456}

        def fake_schedule_test_method(**kwargs):
            self.assertEqual(kwargs, method_kwargs)
            call_info['sched_test_method'] += 1
            pass

        self.stubs.Set(self.cells_manager.scheduler, 'schedule_test_method',
                fake_schedule_test_method)

        self.cells_manager.schedule_test_method(**method_kwargs)
        self.assertEqual(call_info['sched_test_method'], 1)

    def test_get_instances_to_sync(self):
        fake_context = 'fake_context'

        call_info = {'get_all': 0, 'shuffle': 0}

        def random_shuffle(_list):
            call_info['shuffle'] += 1

        def instance_get_all_by_filters(context, filters,
                sort_key, sort_order):
            self.assertEqual(context, fake_context)
            self.assertEqual(sort_key, 'deleted')
            self.assertEqual(sort_order, 'asc')
            call_info['got_filters'] = filters
            call_info['get_all'] += 1
            return ['fake_instance1', 'fake_instance2', 'fake_instance3']

        self.stubs.Set(self.cells_manager.db, 'instance_get_all_by_filters',
                instance_get_all_by_filters)
        self.stubs.Set(random, 'shuffle', random_shuffle)

        instances = self.cells_manager._get_instances_to_sync(fake_context)
        self.assertTrue(inspect.isgenerator(instances))
        self.assertTrue(len([x for x in instances]), 3)
        self.assertEqual(call_info['get_all'], 1)
        self.assertEqual(call_info['got_filters'], {})
        self.assertEqual(call_info['shuffle'], 0)

        instances = self.cells_manager._get_instances_to_sync(fake_context,
                shuffle=True)
        self.assertTrue(inspect.isgenerator(instances))
        self.assertTrue(len([x for x in instances]), 3)
        self.assertEqual(call_info['get_all'], 2)
        self.assertEqual(call_info['got_filters'], {})
        self.assertEqual(call_info['shuffle'], 1)

        instances = self.cells_manager._get_instances_to_sync(fake_context,
                updated_since='fake-updated-since')
        self.assertTrue(inspect.isgenerator(instances))
        self.assertTrue(len([x for x in instances]), 3)
        self.assertEqual(call_info['get_all'], 3)
        self.assertEqual(call_info['got_filters'],
                {'changes-since': 'fake-updated-since'})
        self.assertEqual(call_info['shuffle'], 1)

        instances = self.cells_manager._get_instances_to_sync(fake_context,
                project_id='fake-project',
                updated_since='fake-updated-since', shuffle=True)
        self.assertTrue(inspect.isgenerator(instances))
        self.assertTrue(len([x for x in instances]), 3)
        self.assertEqual(call_info['get_all'], 4)
        self.assertEqual(call_info['got_filters'],
                {'changes-since': 'fake-updated-since',
                 'project_id': 'fake-project'})
        self.assertEqual(call_info['shuffle'], 2)

    def test_heal_instances(self):
        self.flags(instance_updated_at_threshold=1000,
                   instance_update_num_instances=2,
                   # force to update on every call
                   instance_update_interval=-1,
                   group='cells')

        fake_context = context.RequestContext('fake', 'fake')
        stalled_time = timeutils.utcnow()
        updated_since = stalled_time - datetime.timedelta(seconds=1000)

        def utcnow():
            return stalled_time

        call_info = {'get_instances': 0, 'sync_instances': []}

        instances = ['instance1', 'instance2', 'instance3']

        def get_instances_to_sync(context, **kwargs):
            self.assertEqual(context, fake_context)
            call_info['shuffle'] = kwargs.get('shuffle')
            call_info['project_id'] = kwargs.get('project_id')
            call_info['updated_since'] = kwargs.get('updated_since')
            call_info['get_instances'] += 1
            return iter(instances)

        def instance_get_by_uuid(context, uuid):
            return instances[int(uuid[-1]) - 1]

        def sync_instance(context, instance):
            self.assertEqual(context, fake_context)
            call_info['sync_instances'].append(instance)

        self.stubs.Set(self.cells_manager, '_get_instances_to_sync',
                get_instances_to_sync)
        self.stubs.Set(self.cells_manager.db, 'instance_get_by_uuid',
                instance_get_by_uuid)
        self.stubs.Set(self.cells_manager, '_sync_instance',
                sync_instance)
        self.stubs.Set(timeutils, 'utcnow', utcnow)

        self.cells_manager._heal_instances(fake_context)
        self.assertEqual(call_info['shuffle'], True)
        self.assertEqual(call_info['project_id'], None)
        self.assertEqual(call_info['updated_since'], updated_since)
        self.assertEqual(call_info['get_instances'], 1)
        # Only first 2
        self.assertEqual(call_info['sync_instances'],
                instances[:2])

        call_info['sync_instances'] = []
        self.cells_manager._heal_instances(fake_context)
        self.assertEqual(call_info['shuffle'], True)
        self.assertEqual(call_info['project_id'], None)
        self.assertEqual(call_info['updated_since'], updated_since)
        self.assertEqual(call_info['get_instances'], 2)
        # Now the last 1 and the first 1
        self.assertEqual(call_info['sync_instances'],
                [instances[-1], instances[0]])

    def test_sync_instance(self):
        fake_context = 'fake_context'

        call_info = {'broadcast': 0}

        def send_message_to_cells(context, cells, bcast_message,
                **kwargs):
            self.assertEqual(context, fake_context)
            self.assertEqual(bcast_message['method'], 'broadcast_message')
            message = bcast_message['args']['message']
            call_info['method'] = message['method']
            call_info['broadcast'] += 1

        self.stubs.Set(self.cells_manager.cells_rpcapi,
                'send_message_to_cells', send_message_to_cells)

        instance = {'uuid': 'fake', 'deleted': True}
        self.cells_manager._sync_instance(fake_context, instance)
        self.assertEqual(call_info['broadcast'], 1)
        self.assertEqual(call_info['method'], 'instance_destroy')

        instance = {'uuid': 'fake2', 'deleted': False}
        self.cells_manager._sync_instance(fake_context, instance)
        self.assertEqual(call_info['broadcast'], 2)
        self.assertEqual(call_info['method'], 'instance_update')

    def test_security_group_rule_create(self):

        call_info = {
                'create': 0,
                'get': 0,
                }

        fake_context = mock.Mock()
        fake_context.project_id = "fake_project"
        fake_context.to_dict = lambda: {'is_admin': 'True'}
        fake_routing_path = 'fake_routing_path'
        fake_group_pid = 'fake_pid'
        fake_group_name = 'fake_pid'
        fake_group_id = 1
        original_group_id = 2
        fake_rule = {
            'to_port':'fake_to_port',
            'from_port':'fake_from_port',
            'cidr': 'fake_cidr',
            'parent_group_name': fake_group_name,
            'parent_group_pid': fake_group_pid,
            'parent_group_id': original_group_id,
            }

        class group(object):
            def __getattribute__(self, attr):
                if attr == 'id':
                    return fake_group_id
                else:
                    return 'attribute'

        def path_is_us_false(path):
            self.assertEqual(path, fake_routing_path)
            return False

        def security_group_get_by_name_success(context, pid, name):
            call_info['get'] += 1
            self.assertEqual(pid, fake_group_pid)
            self.assertEqual(name, fake_group_name)
            return group()

        def security_group_rule_create(context, security_group_rule, update_cells=False):
            call_info['create'] += 1
            self.assertFalse('parent_group_name' in security_group_rule)
            self.assertFalse('parent_group_pid' in security_group_rule)
            self.assertTrue('parent_group_id' in security_group_rule)
            self.assertEqual(security_group_rule['parent_group_id'], fake_group_id)
            self.assertEqual(update_cells, False)
            return



        self.stubs.Set(self.cells_manager,
                '_path_is_us', path_is_us_false)
        self.stubs.Set(self.cells_manager.db,
                'security_group_get_by_name', security_group_get_by_name_success)
        self.stubs.Set(self.cells_manager.db,
                'security_group_rule_create', security_group_rule_create)

        self.cells_manager.security_group_rule_create(fake_context,
                fake_rule, fake_routing_path)

        self.assertEqual(call_info['create'], 1)
        self.assertEqual(call_info['get'], 1)



    def test_security_group_rule_create_current_cell(self):

        call_info = {
                'create': 0,
                'get': 0,
                }

        fake_context = 'fake_context'
        fake_routing_path = 'fake_routing_path'
        fake_group_id = 1
        fake_group_pid = 'fake_pid'
        fake_group_name = 'fake_pid'
        fake_rule = {
            'to_port':'fake_to_port',
            'from_port':'fake_from_port',
            'cidr': 'fake_cidr',
            'parent_group_name': fake_group_name,
            'parent_group_pid': fake_group_pid,
            }

        class group(object):
            def __getattribute__(self, attr):
                return 'attribute'

        def path_is_us_true(path):
            self.assertEqual(path, fake_routing_path)
            return True

        def security_group_get_by_name_success(context, pid, name):
            call_info['get'] += 1
            self.assertEqual(pid, fake_group_pid)
            self.assertEqual(name, fake_group_name)
            return group()

        def security_group_rule_create(context, security_group_rule, update_cells=False):
            call_info['create'] += 1
            self.assertFalse('parent_group_name' in security_group_rule)
            self.assertFalse('parent_group_pid' in security_group_rule)
            self.assertTrue('parent_group_id' in security_group_rule)
            self.assertEqual(update_cells, False)
            return



        self.stubs.Set(self.cells_manager,
                '_path_is_us', path_is_us_true)
        self.stubs.Set(self.cells_manager.db,
                'security_group_get_by_name', security_group_get_by_name_success)
        self.stubs.Set(self.cells_manager.db,
                'security_group_rule_create', security_group_rule_create)

        self.cells_manager.security_group_rule_create(fake_context,
                fake_rule, fake_routing_path)

        self.assertEqual(call_info['create'], 0)
        self.assertEqual(call_info['get'], 0)


    def test_security_group_rule_missing_parent_group(self):

        call_info = {
                'create': 0,
                'get': 0,
                }

        fake_context = 'fake_context'
        fake_routing_path = 'fake_routing_path'
        fake_group_pid = 'fake_pid'
        fake_group_name = 'fake_pid'
        fake_rule = {
            'to_port':'fake_to_port',
            'from_port':'fake_from_port',
            'cidr': 'fake_cidr',
            'parent_group_name': fake_group_name,
            'parent_group_pid': fake_group_pid,
            }

        def path_is_us_false(path):
            self.assertEqual(path, fake_routing_path)
            return False

        def path_is_us_true(path):
            self.assertEqual(path, fake_routing_path)
            return True

        def security_group_get_by_name_fail(context, pid, name):
            call_info['get'] += 1
            raise exception.SecurityGroupNotFound()

        def security_group_rule_create(context, security_group_rule, update_cells=False):
            call_info['create'] += 1
            return

        self.stubs.Set(self.cells_manager,
                '_path_is_us', path_is_us_false)
        self.stubs.Set(self.cells_manager.db,
                'security_group_get_by_name', security_group_get_by_name_fail)
        self.stubs.Set(self.cells_manager.db,
                'security_group_rule_create', security_group_rule_create)

        self.cells_manager.security_group_rule_create(fake_context,
                fake_rule, fake_routing_path)

        self.assertEqual(call_info['create'], 0)
        self.assertEqual(call_info['get'], 1)

    def test_instance_association_create(self):

        call_info = {
                'create': 0,
                'get': 0,
                }

        fake_context = 'fake_context'
        fake_routing_path = 'fake_routing_path'
        fake_group_pid = 'fake_pid'
        fake_group_name = 'fake_name'
        fake_uuid = 'uuid'
        fake_group_id = 1
        # TODO (shauno) use a proper fake for this
        class group(object):
            def __getattribute__(self, attr):
                if attr == 'id':
                    return fake_group_id
                else:
                    return 'attribute'
        fake_security_group = group()
        fake_instance_association = {
            'parent_group_name': fake_group_name,
            'parent_group_pid': fake_group_pid,
            'instance_uuid': fake_uuid,
            }

        def path_is_us_false(path):
            self.assertEqual(path, fake_routing_path)
            return False

        def security_group_get_by_name_success(context, pid, name):
            call_info['get'] += 1
            self.assertEqual(pid, fake_group_pid)
            self.assertEqual(name, fake_group_name)
            return fake_security_group

        def instance_add_security_group(context, uuid, group_id, update_cells=False):
            call_info['create'] += 1
            self.assertEqual(uuid, fake_uuid)
            self.assertEqual(group_id, fake_group_id)
            self.assertEqual(update_cells, False)
            return

        self.stubs.Set(self.cells_manager,
                '_path_is_us', path_is_us_false)
        self.stubs.Set(self.cells_manager.db,
                'security_group_get_by_name', security_group_get_by_name_success)
        self.stubs.Set(self.cells_manager.db,
                'instance_add_security_group', instance_add_security_group)

        self.cells_manager.instance_association_create(fake_context,
                fake_instance_association, fake_routing_path)

        self.assertEqual(call_info['create'], 1)
        self.assertEqual(call_info['get'], 1)

    def test_instance_association_destroy(self):

        call_info = {
            'destroy': 0,
            'get': 0,
            'get_instance': 0,
            'get_filters': 0,
        }

        fake_context = mock.Mock()
        fake_context.project_id = "fake_project"
        fake_context.read_deleted = 'no'
        fake_context.to_dict = lambda: {'is_admin': 'True'}
        fake_routing_path = 'fake_routing_path'
        fake_group_pid = 'fake_pid'
        fake_group_name = 'fake_name'
        fake_uuid = 'uuid'
        fake_group_id = 1
        # TODO (shauno) use a proper fake for this
        class group(object):
            def __getattribute__(self, attr):
                if attr == 'id':
                    return fake_group_id
                else:
                    return 'attribute'
        fake_security_group = group()
        fake_instance_association = {
            'parent_group_name': fake_group_name,
            'parent_group_pid': fake_group_pid,
            'instance_uuid': fake_uuid,
            }

        def path_is_us_false(path):
            self.assertEqual(path, fake_routing_path)
            return False

        def security_group_get_by_name_success(context, pid, name):
            call_info['get'] += 1
            self.assertEqual(pid, fake_group_pid)
            self.assertEqual(name, fake_group_name)
            return fake_security_group

        def instance_remove_security_group(context, uuid, group_id, update_cells=False):
            call_info['destroy'] += 1
            self.assertEqual(uuid, fake_uuid)
            self.assertEqual(group_id, fake_group_id)
            self.assertEqual(update_cells, False)
            return

        def instance_get_by_uuid(context, uuid):
            call_info['get_instance'] += 1
            self.assertEqual(uuid, fake_uuid)
            return

        def security_group_instance_association_get_all_by_filters(
            context, instance_association, sort_key, sort_dir, limit=None, marker=None):
            call_info['get_filters'] += 1
            return True

        self.stubs.Set(self.cells_manager,
                '_path_is_us', path_is_us_false)
        self.stubs.Set(self.cells_manager.db,
                'security_group_get_by_name', security_group_get_by_name_success)
        self.stubs.Set(self.cells_manager.db,
                'instance_remove_security_group', instance_remove_security_group)
        self.stubs.Set(self.cells_manager.db,
                'instance_get_by_uuid', instance_get_by_uuid)
        self.stubs.Set(self.cells_manager.db,
                       'security_group_instance_association_get_all_by_filters',
                       security_group_instance_association_get_all_by_filters)

        self.cells_manager.instance_association_destroy(fake_context,
                fake_instance_association, fake_routing_path)

        self.assertEqual(call_info['destroy'], 1)
        self.assertEqual(call_info['get'], 1)
        self.assertEqual(call_info['get_instance'], 1)
        self.assertEqual(call_info['get_filters'], 1)

    def test_heal_rules(self):

        fake_context = context.RequestContext('fake', 'fake')
        stalled_time = time.time()

        call_info = {'heal_entries': 0}


        def fake_time():
            return stalled_time

        def get_parent_cells():
            return None

        def is_time_to_heal_true(curr_time):
            self.assertEqual(curr_time, stalled_time)
            return True

        def set_last_heal_time(curr_time):
            self.assertEqual(curr_time, stalled_time)

        def heal_entries(context):
            self.assertEqual(context, fake_context)
            call_info['heal_entries']+=1

        def instance_get_by_uuid(context, uuid):
            return instances[int(uuid[-1]) - 1]

        self.stubs.Set(self.cells_manager, '_get_parent_cells',
                get_parent_cells)
        self.stubs.Set(self.cells_manager.rules_consistency_handler, 'is_time_to_heal',
                is_time_to_heal_true)
        self.stubs.Set(self.cells_manager.rules_consistency_handler, 'set_last_heal_time',
                set_last_heal_time)
        self.stubs.Set(self.cells_manager.rules_consistency_handler, 'heal_entries',
                heal_entries)
        self.stubs.Set(time, 'time', fake_time)

        self.cells_manager._heal_security_group_rules(fake_context)
        self.assertEqual(call_info['heal_entries'], 1)

    def test_heal_rules_skip(self):

        fake_context = context.RequestContext('fake', 'fake')
        stalled_time = time.time()

        call_info = {'heal_entries': 0}


        def fake_time():
            return stalled_time

        def get_parent_cells():
            return None

        def is_time_to_heal_false(curr_time):
            self.assertEqual(curr_time, stalled_time)
            return False

        def set_last_heal_time(curr_time):
            self.assertEqual(curr_time, stalled_time)

        def heal_entries(context):
            self.assertEqual(context, fake_context)
            call_info['heal_entries']+=1

        def instance_get_by_uuid(context, uuid):
            return instances[int(uuid[-1]) - 1]

        self.stubs.Set(self.cells_manager, '_get_parent_cells',
                get_parent_cells)
        self.stubs.Set(self.cells_manager.rules_consistency_handler, 'is_time_to_heal',
                is_time_to_heal_false)
        self.stubs.Set(self.cells_manager.rules_consistency_handler, 'set_last_heal_time',
                set_last_heal_time)
        self.stubs.Set(self.cells_manager.rules_consistency_handler, 'heal_entries',
                heal_entries)
        self.stubs.Set(time, 'time', fake_time)

        self.cells_manager._heal_security_group_rules(fake_context)
        self.assertEqual(call_info['heal_entries'], 0)

    def test_heal_rules_child(self):

        fake_context = context.RequestContext('fake', 'fake')
        stalled_time = time.time()

        call_info = {'heal_entries': 0}

        def fake_time():
            return stalled_time

        def get_parent_cells_has_parents():
            return ['parent1']

        def is_time_to_heal_true(curr_time):
            self.assertEqual(curr_time, stalled_time)
            return True

        def set_last_heal_time(curr_time):
            self.assertEqual(curr_time, stalled_time)

        def heal_entries(context):
            self.assertEqual(context, fake_context)
            call_info['heal_entries']+=1

        def instance_get_by_uuid(context, uuid):
            return instances[int(uuid[-1]) - 1]

        self.stubs.Set(self.cells_manager, '_get_parent_cells',
                get_parent_cells_has_parents)
        self.stubs.Set(self.cells_manager.rules_consistency_handler, 'is_time_to_heal',
                is_time_to_heal_true)
        self.stubs.Set(self.cells_manager.rules_consistency_handler, 'set_last_heal_time',
                set_last_heal_time)
        self.stubs.Set(self.cells_manager.rules_consistency_handler, 'heal_entries',
                heal_entries)
        self.stubs.Set(time, 'time', fake_time)

        self.cells_manager._heal_security_group_rules(fake_context)
        self.assertEqual(call_info['heal_entries'], 0)

class CellsConsistencyManagerClassTestCase(test.TestCase):
    """Test case for CellsConsistencyManager class"""

    def setUp(self):
        super(CellsConsistencyManagerClassTestCase, self).setUp()
        self.interval = 1000
        self.update_threshold = 2000
        self.update_number = 5
        self.path = 'fake_path'
        self.rpc_api = 'fake_rpc'
        self.get_child_cells = 'fake_child_cells'

        self.consistency_handler = \
            consistency.ConsistencyHandler(
                    self.interval,
                    self.update_threshold,
                    self.update_number,
                    self.path,
                    self.rpc_api,
                    self.get_child_cells)

    def test_time(self):
        last_heal_time = time.time()
        after_heal_time = last_heal_time + self.interval + 1
        before_heal_time = last_heal_time + self.interval - 1

        self.consistency_handler.set_last_heal_time(last_heal_time)
        self.assertEqual(self.consistency_handler.is_time_to_heal(after_heal_time), True)
        self.assertEqual(self.consistency_handler.is_time_to_heal(before_heal_time), False)

        self.consistency_handler.set_last_heal_time(last_heal_time + 5)
        self.assertEqual(self.consistency_handler.is_time_to_heal(after_heal_time), False)

    def test_reset(self):

        fake_context = context.RequestContext('fake', 'fake')

        fake_entries = [
                    {'deleted':False, 'name':'first'},
                    {'deleted':False, 'name':'second'},
                    {'deleted':False, 'name':'third'},
                    {'deleted':False, 'name':'fourth'},
                    {'deleted':False, 'name':'fifth'},
                    {'deleted':False, 'name':'sixth'}
                ]

        call_info = {
                'filter_called': 0,
                'send_create_called':0,
                'send_delete_called':0,
                'entries':[],
                }

        def reset_call_info():
            call_info['filter_called'] =  0
            call_info['send_create_called'] = 0
            call_info['send_delete_called'] = 0
            call_info['entries']= []

        def check_call_info(*args, **kwargs):
            for key, value in kwargs.items():
                self.assertEqual(call_info[key], value)


        def filter_fake_3(context, filters, deleted, direction):
            self.assertEqual(context, fake_context)
            call_info['filter_called']+=1
            return fake_entries[0:3]

        def filter_fake_6(context, filters, deleted, direction):
            self.assertEqual(context, fake_context)
            call_info['filter_called']+=1
            return fake_entries

        def send_create(context, entry):
            self.assertEqual(context, fake_context)
            call_info['send_create_called']+=1
            call_info['entries'].append(entry)

        def send_delete(context, entry):
            self.assertEqual(context, fake_context)
            call_info['send_delete_called']+=1

        # Test that the consistency handler only sends 3 entries
        # when the db returns 3 entries, but the max number of
        # entries for the update is 5

        self.stubs.Set(self.consistency_handler, 'get_entries_filtered',
                filter_fake_3)
        self.stubs.Set(self.consistency_handler, '_send_create',
                send_create)
        self.stubs.Set(self.consistency_handler, '_send_destroy',
                send_delete)

        current_time = time.time()
        last_heal_time = current_time - self.interval
        self.consistency_handler.set_last_heal_time(last_heal_time)
        self.consistency_handler.heal_entries(fake_context)

        check_call_info(filter_called=1, send_create_called=3, send_delete_called=0)
        self.assertEqual(len(call_info['entries']), 3)
        for entry in fake_entries[0:3]:
            self.assertEqual(entry in call_info['entries'], True)

        # Check that no more entries are sent after the first iteration
        # unless reset is called
        self.consistency_handler.heal_entries(fake_context)

        check_call_info(filter_called=1, send_create_called=3, send_delete_called=0)
        self.assertEqual(len(call_info['entries']), 3)
        for entry in fake_entries[0:3]:
            self.assertEqual(entry in  call_info['entries'], True)

        # Confirm that the 3 entries are resent if reset is called
        self.consistency_handler.reset()
        reset_call_info()
        self.consistency_handler.heal_entries(fake_context)
        check_call_info(filter_called=1, send_create_called=3, send_delete_called=0)
        self.assertEqual(len(call_info['entries']), 3)
        for entry in fake_entries[0:3]:
            self.assertEqual(entry in  call_info['entries'], True)

        # Check that no more than the maximum number (5) of entries are sent
        self.stubs.Set(self.consistency_handler, 'get_entries_filtered',
                filter_fake_6)
        reset_call_info()

        self.consistency_handler.reset()
        self.consistency_handler.heal_entries(fake_context)
        check_call_info(filter_called=1, send_create_called=5, send_delete_called=0)
        self.assertEqual(len(call_info['entries']), 5)
        create_count = 0
        for entry in fake_entries:
            if entry in call_info['entries']:
                create_count+=1
        self.assertEqual(len(call_info['entries']), 5)

        call_entries = copy.deepcopy(call_info['entries'])

        # check that the last remaining entry, and a subsequent
        # 4 newly generated entries are sent
        reset_call_info()
        self.consistency_handler.reset()
        self.consistency_handler.heal_entries(fake_context)
        check_call_info(filter_called=1, send_create_called=5, send_delete_called=0)
        self.assertEqual(len(call_info['entries']), 5)

    def test_heal_entries(self):
        fake_context = context.RequestContext('fake', 'fake')

        fake_entries = [
                    {'deleted':False, 'name':'first'},
                    {'deleted':False, 'name':'second'},
                    {'deleted':False, 'name':'third'},
                    {'deleted':False, 'name':'fourth'},
                    {'deleted':False, 'name':'fifth'},
                    {'deleted':False, 'name':'sixth'},
                ]
        fake_entries_delete = [
                    {'deleted':True, 'name':'seventh'},
                    {'deleted':True, 'name':'eigth'},
                ]
        call_info = {
                'filter_called': 0,
                'send_create_called':0,
                'send_delete_called':0,
                'entries':[],
                'deleted':[],
                }

        def reset_call_info():
            call_info['filter_called'] =  0
            call_info['send_create_called'] = 0
            call_info['send_delete_called'] = 0
            call_info['entries']= []
            call_info['deleted']= []

        def check_call_info(*args, **kwargs):
            # Special case for created/deleted entry lists
            in_created = kwargs.pop('in_created', None)
            in_deleted = kwargs.pop('in_deleted', None)

            if in_created:
                created_set = set([e['name'] for e in in_created])
                call_created_set = set([e['name'] for e in call_info['entries']])
                self.assertEqual(created_set, call_created_set)
                self.assertEqual(len(call_info['entries']), len(call_created_set))

            if in_deleted:
                deleted_set = set([e['name'] for e in in_deleted])
                call_deleted_set = set([e['name'] for e in call_info['deleted']])
                self.assertEqual(deleted_set, call_deleted_set)
                self.assertEqual(len(call_info['deleted']), len(call_deleted_set))

            # All other key value pairs for regular comparison
            for key, value in kwargs.items():
                self.assertEqual(call_info[key], value)


        def filter_fake_3(context, filters, deleted, direction):
            # Return a list of entries shorter than the max num entries
            self.assertEqual(context, fake_context)
            call_info['filter_called']+=1
            return fake_entries[0:3]

        def filter_fake_6(context, filters, deleted, direction):
            # Return a list of entries longer than the max num entries
            self.assertEqual(context, fake_context)
            call_info['filter_called']+=1
            return fake_entries

        def filter_fake_delete(context, filters, deleted, direction):
            # Return a list of entries that are to be created and deleted
            self.assertEqual(context, fake_context)
            call_info['filter_called']+=1
            return fake_entries[0:2] + fake_entries_delete[0:2]

        def send_create(context, entry):
            self.assertEqual(context, fake_context)
            call_info['send_create_called']+=1
            call_info['entries'].append(entry)

        def send_delete(context, entry):
            self.assertEqual(context, fake_context)
            call_info['send_delete_called']+=1
            call_info['deleted'].append(entry)

        # Test that the consistency handler only sends 3 entries
        # when the db returns 3 entries, but the max number of
        # entries for the update is 5

        self.stubs.Set(self.consistency_handler, 'get_entries_filtered',
                filter_fake_3)
        self.stubs.Set(self.consistency_handler, '_send_create',
                send_create)
        self.stubs.Set(self.consistency_handler, '_send_destroy',
                send_delete)

        current_time = time.time()
        last_heal_time = current_time - self.interval
        self.consistency_handler.set_last_heal_time(last_heal_time)
        self.consistency_handler.heal_entries(fake_context)

        check_call_info(filter_called=1, send_create_called=3, send_delete_called=0)
        self.assertEqual(len(call_info['entries']), 3)
        for entry in fake_entries[0:3]:
            self.assertEqual(entry in call_info['entries'], True)

        # Check that no more than the maximum number (5) of entries are sent
        self.stubs.Set(self.consistency_handler, 'get_entries_filtered',
                filter_fake_6)
        reset_call_info()

        self.consistency_handler.reset()
        self.consistency_handler.heal_entries(fake_context)
        check_call_info(filter_called=1, send_create_called=5, send_delete_called=0)
        self.assertEqual(len(call_info['entries']), 5)
        create_count = 0
        for entry in fake_entries:
            if entry in call_info['entries']:
                create_count+=1
        self.assertEqual(len(call_info['entries']), 5)

        # Check that remaining entries (>5) are sent on the second
        # call to heal instances
        reset_call_info()
        self.consistency_handler.heal_entries(fake_context)
        check_call_info(filter_called=0, send_create_called=1, send_delete_called=0)
        self.assertEqual(len(call_info['entries']), 1)

        # Delete for delete, create for create
        self.stubs.Set(self.consistency_handler, 'get_entries_filtered',
                filter_fake_delete)
        reset_call_info()

        self.consistency_handler.reset()
        self.consistency_handler.heal_entries(fake_context)
        check_call_info(
                filter_called=1,
                send_create_called=2,
                send_delete_called=2,
                in_created=fake_entries[0:2],
                in_deleted=fake_entries_delete
                )
