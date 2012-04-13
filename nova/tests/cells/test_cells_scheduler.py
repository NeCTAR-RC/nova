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

from nova.cells import scheduler as cells_scheduler
from nova import flags
from nova.openstack.common import rpc
from nova import test
from nova.tests.cells import fakes


FLAGS = flags.FLAGS


class CellsSchedulerTestCase(test.TestCase):
    """Test case for CellsScheduler class"""

    def setUp(self):
        super(CellsSchedulerTestCase, self).setUp()
        self.flags(cell_name='me', host='host0')
        fakes.init()

        self.cells_manager = fakes.FakeCellsManager(
                _test_case=self,
                _my_name=FLAGS.cell_name,
                cells_driver_cls=fakes.FakeCellsDriver,
                cells_scheduler_cls=cells_scheduler.CellsScheduler)
        self.scheduler = self.cells_manager.scheduler
        # Fudge our child cells so we only have 'cell2' as a child
        for key in self.cells_manager.child_cells.keys():
            if key != 'cell2':
                del self.cells_manager.child_cells[key]
        # Also nuke our parents so we can see the instance_update
        self.cells_manager.parent_cells = {}

    def test_setup(self):
        self.assertEqual(self.scheduler.manager, self.cells_manager)

    def test_schedule_run_instance_happy_day(self):
        # Tests that requests make it to child cell, instance is created,
        # and an update is returned back upstream
        fake_context = 'fake_context'
        fake_topic = 'compute'
        fake_instance_props = {'vm_state': 'fake_vm_state',
                               'security_groups': 'meow'}
        fake_instance_type = {'memory_mb': 1024}
        fake_request_spec = {'instance_properties': fake_instance_props,
                             'instance_uuids': ['fake_uuid'],
                             'image': 'fake_image',
                             'instance_type': fake_instance_type,
                             'security_group': 'fake_security_group',
                             'block_device_mapping': 'fake_bd_mapping'}
        fake_filter_properties = {'fake_filter_properties': 'meow'}

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

        def fake_rpc_cast(context, topic, message):
            args = {'topic': fake_topic,
                    'request_spec': fake_request_spec,
                    'filter_properties': fake_filter_properties}
            expected_message = {'method': 'run_instance',
                                'args': args}
            self.assertEqual(context, fake_context)
            self.assertEqual(message, expected_message)
            call_info['cast_called'] += 1

        # Called in top level.. should be pushed up from GC cell
        def fake_instance_update(context, instance_info, routing_path):
            props = fake_instance_props.copy()
            # This should get filtered out
            props.pop('security_groups')
            self.assertEqual(routing_path,
                    'grandchild@host2!cell2@host1!me@host0')
            self.assertEqual(context, fake_context)
            self.assertEqual(instance_info, props)
            call_info['update_called'] += 1

        self.stubs.Set(gc_mgr.scheduler.compute_api,
                'create_db_entry_for_new_instance',
                fake_create_db_entry)
        self.stubs.Set(rpc, 'cast', fake_rpc_cast)
        self.stubs.Set(self.cells_manager, 'instance_update',
                fake_instance_update)

        self.cells_manager.schedule_run_instance(fake_context,
                topic=fake_topic,
                request_spec=fake_request_spec,
                filter_properties=fake_filter_properties)
        self.assertEqual(call_info['create_called'], 1)
        self.assertEqual(call_info['cast_called'], 1)
        self.assertEqual(call_info['update_called'], 1)
