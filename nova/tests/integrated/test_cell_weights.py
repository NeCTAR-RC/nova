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
Tests the cell weigting plugins
"""

from nova.cells import scheduler as cells_scheduler
from nova import flags
from nova.openstack.common import rpc
from nova import test
from nova.tests.cells import fakes

FLAGS = flags.FLAGS


class TestWeightOffsetTestCase(test.TestCase):
    def setUp(self):
        super(TestWeightOffsetTestCase, self).setUp()
        self.flags(cell_name='me', host='host1')
        fakes.init()
        # Tell cell5 who it's parent is, so it can tell it's capacity to its
        # parent so that it's parent can make informed choices with the
        # weighting plugins
        fakes.FAKE_CELLS['cell5'] = [{'id':1, 'name':'me', 'is_parent':True}]
        # So it seems the grandchild is chosen by default anyway,
        # as none of the cells have any capacity info
        # Lets give cell5 some capacity so that it should really have precence
        self.cell5_manager = fakes.FakeCellsManager(
                _test_case=self,
                _my_name='cell5',
                cells_driver_cls=fakes.FakeCellsDriver,
                cells_scheduler_cls=cells_scheduler.CellsScheduler,
                capacities={'ram_free': {'total_mb': 2048,
                                           'units_by_mb': {'1024': 2}},
                              'disk_free': {'total_mb': 999999,
                                            'units_by_mb': {'1024': 2}}
                             }
        )
        # Cell5 manager must be created before it's parent, so that we can give
        # it a capacity
        self.top_cell_manager = fakes.FakeCellsManager(
                _test_case=self,
                _my_name=FLAGS.cell_name,
                cells_driver_cls=fakes.FakeCellsDriver,
                cells_scheduler_cls=cells_scheduler.CellsScheduler)
        self.scheduler = self.top_cell_manager.scheduler
        # Also nuke our parents so we can see the instance_update
        self.top_cell_manager.parent_cells = {}

    def test_weight_offset(self):
        """
        Tests that the scheduler reads the weight_offset db field.
        Done with the plugin nova/cells/weights/weight_offset.py """
        self.assertEqual(self.scheduler.manager, self.top_cell_manager)
        fake_context = 'fake_context'
        fake_topic = 'compute'
        fake_instance_props = {'vm_state': 'fake_vm_state',
                               'security_groups': 'meow'}
        fake_instance_type = {'memory_mb': 1024}
        fake_request_spec = {'instance_properties': fake_instance_props,
                             'instance_uuids': ['fake-uuid'],
                             'image': 'fake_image',
                             'instance_type': fake_instance_type,
                             'security_group': 'fake_security_group',
                             'block_device_mapping': 'fake_bd_mapping'}
        fake_filter_properties = {'fake_filter_properties': 'meow'}

        # Tell the top level manager about cell5's capacities
        self.cell5_manager._update_our_parents(fake_context)

        call_info = {'create_called': 0, 'cast_called': 0,
                     'update_called': 0}

        # Detect when the rpc message is sent
        def fake_rpc_cast(context, topic, message):
            args = {'request_spec': fake_request_spec,
                    'filter_properties': fake_filter_properties,
                    'admin_password': 'foo',
                    'injected_files': [],
                    'requested_networks': None,
                    'is_first_time': True,
                    }
            expected_message = {'method': 'run_instance',
                                'args': args,
                                'version': '2.0'}
            self.assertEqual(context, fake_context)
            self.assertEqual(message, expected_message)
            call_info['cast_called'] += 1

        self.stubs.Set(rpc, 'cast', fake_rpc_cast)

        def fake_instance_update(context, instance_info, routing_path):
            """Called in top level.. should be pushed up from GC cell"""
            props = fake_instance_props.copy()
            # This should get filtered out
            props.pop('security_groups')
            self.assertEqual(routing_path,
                    'cell5@host1!me@host1')
            self.assertEqual(context, fake_context)
            self.assertEqual(instance_info, props)
            call_info['update_called'] += 1

        def fake_create_db_entry(context, instance_type, image,
                base_options, security_group, bd_mapping):
            self.assertEqual(context, fake_context)
            self.assertEqual(image, 'fake_image')
            self.assertEqual(instance_type, fake_instance_type)
            self.assertEqual(security_group, 'fake_security_group')
            self.assertEqual(bd_mapping, 'fake_bd_mapping')
            call_info['create_called'] += 1
            return fake_instance_props

        # Choose the winner cell
        # Using float because that's what's in the DB
        self.cell5_manager.my_cell_info.db_info['weight_offset'] = \
            -99999999999999.0

        self.stubs.Set(self.cell5_manager.scheduler.compute_api,
                'create_db_entry_for_new_instance',
                fake_create_db_entry)
        self.stubs.Set(self.cell5_manager.scheduler.compute_api,
                'create_db_entry_for_new_instance',
                fake_create_db_entry)
        self.stubs.Set(self.top_cell_manager, 'instance_update',
                fake_instance_update)

        self.top_cell_manager.schedule_run_instance(fake_context,
                topic=fake_topic,
                request_spec=fake_request_spec,
                admin_password='foo',
                injected_files=[],
                requested_networks=None,
                is_first_time=True,
                filter_properties=fake_filter_properties)

        self.assertEqual(call_info['create_called'], 1)
        self.assertEqual(call_info['cast_called'], 1)
        self.assertEqual(call_info['update_called'], 1)
