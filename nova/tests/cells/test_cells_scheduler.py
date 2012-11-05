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
Tests For CellsScheduler
"""

from nova.cells import rpcapi as cells_rpcapi
from nova.cells import scheduler as cells_scheduler
from nova import context
from nova import exception
from nova import flags
from nova.openstack.common import rpc
from nova import test
from nova.tests.cells import fakes

flags.DECLARE('cells', 'nova.cells.opts')
FLAGS = flags.FLAGS


class CellsSchedulerTestCase(test.TestCase):
    """Test case for CellsScheduler class"""

    def setUp(self):
        super(CellsSchedulerTestCase, self).setUp()
        self.flags(name='me', group='cells')
        self.flags(host='host0')
        fakes.init()

        rpcapi_obj = cells_rpcapi.CellsAPI(
                cells_driver_cls=fakes.FakeCellsDriver)
        self.cells_manager = fakes.FakeCellsManager(
                _test_case=self,
                _my_name=FLAGS.cells.name,
                cells_scheduler_cls=cells_scheduler.CellsScheduler)
        self.scheduler = self.cells_manager.scheduler

    def test_setup(self):
        self.assertEqual(self.scheduler.manager, self.cells_manager)

    def test_schedule_run_instance_happy_day(self):
        # Fudge our child cells so we only have 'cell2' as a child
        for key in self.cells_manager.child_cells.keys():
            if key != 'cell2':
                del self.cells_manager.child_cells[key]

        self._call_schedule_run_instance(
            'grandchild@host2!cell2@host1!me@host0')

    def test_schedule_run_instance_hint_bottom_cell(self):
        expected_routing_path = 'grandchild@host2!cell2@host1!me@host0'
        call_info = self._call_schedule_run_instance(expected_routing_path,
                                         scheduler_hints={
                                             'cell': 'cell2!grandchild'})
        self.assertEqual(call_info['create_called'], 1)
        self.assertEqual(call_info['cast_called'], 1)
        self.assertEqual(call_info['update_called'], 1)
    
    def test_schedule_run_instance_hint_middle_cell(self):
        expected_routing_path = 'grandchild@host2!cell2@host1!me@host0'
        call_info = self._call_schedule_run_instance(expected_routing_path,
                                         scheduler_hints={
                                             'cell': 'cell2'})
        self.assertEqual(call_info['create_called'], 1)
        self.assertEqual(call_info['cast_called'], 1)
        self.assertEqual(call_info['update_called'], 1)

    def test_schedule_run_instance_hint_missing_cell(self):
        self.assertRaises(exception.CellNotFound,
                          self._call_schedule_run_instance,
                          expected_routing_path=None,
                          scheduler_hints={'cell': 'cell2!missing'})

    def _call_schedule_run_instance(self, expected_routing_path, scheduler_hints=None, error=False):
        # Nuke our parents so we can see the instance_update
        self.cells_manager.parent_cells = {}

        # Tests that requests make it to child cell, instance is created,
        # and an update is returned back upstream
        fake_context = context.RequestContext('fake', 'fake')
        fake_topic = 'compute'
        fake_instance_props = {'vm_state': 'fake_vm_state',
                               'security_groups': 'meow'}
        fake_instance_type = {'memory_mb': 1024}
        fake_request_spec = {'instance_properties': fake_instance_props,
                             'instance_uuids': ['aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa'],
                             'image': 'fake_image',
                             'instance_type': fake_instance_type,
                             'security_group': 'fake_security_group',
                             'block_device_mapping': 'fake_bd_mapping'}
        fake_filter_properties = {'fake_filter_properties': 'meow'}
        if scheduler_hints:
            fake_filter_properties['scheduler_hints'] = scheduler_hints

        # The grandchild cell is where this should get scheduled
        gc_mgr = fakes.FAKE_CELL_MANAGERS['grandchild']

        call_info = {'create_called': 0, 'cast_called': 0,
                     'update_called': 0}

        def fake_create_db_entry(context, instance_type, image,
                base_options, security_group, bd_mapping):
            self.assertEqual(context, fake_context)
            self.assertEqual(image, 'fake_image')
            self.assertEqual(instance_type, fake_instance_type)
            self.assertEqual(security_group, 'fake_security_group')
            self.assertEqual(bd_mapping, 'fake_bd_mapping')
            call_info['create_called'] += 1
            return fake_instance_props

        def fake_cast(self, context, fwd_msg):
            request_spec = fwd_msg['args']['request_spec']
            filter_props = fwd_msg['args']['filter_properties']
            topic = fwd_msg['args']['topic']
            if not fake_request_spec == request_spec:
                raise Exception("Wrong request spec")
            if not fake_filter_properties == filter_props:
                raise Exception("Wrong filter spec")
            if not fake_topic == topic:
                raise Exception("Wrong topic")
            call_info['cast_called'] += 1

        # Called in top level.. should be pushed up from GC cell
        def fake_instance_update(context, instance_info, routing_path):
            props = fake_instance_props.copy()
            # This should get filtered out
            props.pop('security_groups')
            self.assertEqual(routing_path, expected_routing_path)
            self.assertEqual(context, fake_context)
            self.assertEqual(instance_info, props)
            call_info['update_called'] += 1

        self.stubs.Set(gc_mgr.scheduler.compute_api,
                'create_db_entry_for_new_instance',
                fake_create_db_entry)
        self.stubs.Set(cells_scheduler.CellsScheduler, '_cast_to_scheduler',
                fake_cast)
        self.stubs.Set(self.cells_manager, 'instance_update',
                fake_instance_update)

        self.cells_manager.schedule_run_instance(fake_context,
                topic=fake_topic,
                request_spec=fake_request_spec,
                admin_password='foo',
                injected_files=[],
                requested_networks=None,
                is_first_time=True,
                filter_properties=fake_filter_properties)
        return call_info
