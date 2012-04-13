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
Tests For Cells API
"""

from nova.cells import api as cells_api
from nova.cells import utils as cells_utils
from nova import flags
from nova.openstack.common import rpc
from nova import test


FLAGS = flags.FLAGS


class CellsAPITestCase(test.TestCase):
    """Test case for cells.api interfaces."""

    def setUp(self):
        super(CellsAPITestCase, self).setUp()

    def test_cell_call(self):
        fake_context = 'fake_context'
        fake_cell_name = 'fake_cell_name'
        fake_method = 'fake_method'
        fake_method_kwargs = {'kwarg1': 10, 'kwarg2': 20}
        fake_response = 'fake_response'
        fake_wrapped_message = 'fake_wrapped_message'

        def fake_form_routing_message(name, direction, method,
                method_kwargs, need_response=False):
            self.assertEqual(name, fake_cell_name)
            self.assertEqual(method, fake_method)
            self.assertEqual(method_kwargs, fake_method_kwargs)
            self.assertEqual(direction, 'down')
            self.assertTrue(need_response)
            return fake_wrapped_message

        def fake_rpc_call(context, topic, message):
            self.assertEqual(context, fake_context)
            self.assertEqual(topic, FLAGS.cells_topic)
            self.assertEqual(message, fake_wrapped_message)
            return fake_response

        self.stubs.Set(cells_utils, 'form_routing_message',
                fake_form_routing_message)
        self.stubs.Set(rpc, 'call', fake_rpc_call)

        result = cells_api.cell_call(fake_context,
                fake_cell_name, fake_method,
                **fake_method_kwargs)
        self.assertEqual(result, fake_response)

    def test_cell_cast(self):
        fake_context = 'fake_context'
        fake_cell_name = 'fake_cell_name'
        fake_method = 'fake_method'
        fake_method_kwargs = {'kwarg1': 10, 'kwarg2': 20}
        fake_wrapped_message = 'fake_wrapped_message'

        def fake_form_routing_message(name, direction, method,
                method_kwargs, need_response=False):
            self.assertEqual(name, fake_cell_name)
            self.assertEqual(method, fake_method)
            self.assertEqual(method_kwargs, fake_method_kwargs)
            self.assertEqual(direction, 'down')
            self.assertFalse(need_response)
            return fake_wrapped_message

        call_info = {'cast_called': 0}

        def fake_rpc_cast(context, topic, message):
            self.assertEqual(context, fake_context)
            self.assertEqual(topic, FLAGS.cells_topic)
            self.assertEqual(message, fake_wrapped_message)
            call_info['cast_called'] += 1

        self.stubs.Set(cells_utils, 'form_routing_message',
                fake_form_routing_message)
        self.stubs.Set(rpc, 'cast', fake_rpc_cast)

        cells_api.cell_cast(fake_context, fake_cell_name, fake_method,
                **fake_method_kwargs)
        self.assertEqual(call_info['cast_called'], 1)

    def test_cell_broadcast(self):
        fake_context = 'fake_context'
        fake_method = 'fake_method'
        fake_direction = 'fake_direction'
        fake_method_kwargs = {'kwarg1': 10, 'kwarg2': 20}
        fake_wrapped_message = 'fake_wrapped_message'

        def fake_form_broadcast_message(direction, method, method_kwargs):
            self.assertEqual(direction, fake_direction)
            self.assertEqual(method, fake_method)
            self.assertEqual(method_kwargs, fake_method_kwargs)
            return fake_wrapped_message

        call_info = {'cast_called': 0}

        def fake_rpc_cast(context, topic, message):
            self.assertEqual(context, fake_context)
            self.assertEqual(topic, FLAGS.cells_topic)
            self.assertEqual(message, fake_wrapped_message)
            call_info['cast_called'] += 1

        self.stubs.Set(cells_utils, 'form_broadcast_message',
                fake_form_broadcast_message)
        self.stubs.Set(rpc, 'cast', fake_rpc_cast)

        cells_api.cell_broadcast(fake_context, fake_direction, fake_method,
                **fake_method_kwargs)
        self.assertEqual(call_info['cast_called'], 1)

    def test_cast_service_api_method(self):
        fake_context = 'fake_context'
        fake_cell_name = 'fake_cell_name'
        fake_method = 'fake_method'
        fake_service = 'fake_service'
        fake_method_args = (1, 2)
        fake_method_kwargs = {'kwarg1': 10, 'kwarg2': 20}

        expected_method_info = {'method': fake_method,
                                'method_args': fake_method_args,
                                'method_kwargs': fake_method_kwargs}

        call_info = {'cast_called': 0}

        def fake_cell_cast(context, cell_name, method, service_name,
                method_info, is_broadcast):
            self.assertEqual(context, fake_context)
            self.assertEqual(cell_name, fake_cell_name)
            self.assertEqual(method, 'run_service_api_method')
            self.assertEqual(service_name, fake_service)
            self.assertEqual(method_info, expected_method_info)
            self.assertFalse(is_broadcast)
            call_info['cast_called'] += 1

        self.stubs.Set(cells_api, 'cell_cast', fake_cell_cast)

        cells_api.cast_service_api_method(fake_context,
                fake_cell_name, fake_service, fake_method,
                *fake_method_args, **fake_method_kwargs)
        self.assertEqual(call_info['cast_called'], 1)

    def test_call_service_api_method(self):
        fake_context = 'fake_context'
        fake_cell_name = 'fake_cell_name'
        fake_method = 'fake_method'
        fake_service = 'fake_service'
        fake_method_args = (1, 2)
        fake_method_kwargs = {'kwarg1': 10, 'kwarg2': 20}
        fake_response = 'fake_response'

        expected_method_info = {'method': fake_method,
                                'method_args': fake_method_args,
                                'method_kwargs': fake_method_kwargs}

        def fake_cell_call(context, cell_name, method, service_name,
                method_info, is_broadcast):
            self.assertEqual(context, fake_context)
            self.assertEqual(cell_name, fake_cell_name)
            self.assertEqual(method, 'run_service_api_method')
            self.assertEqual(service_name, fake_service)
            self.assertEqual(method_info, expected_method_info)
            self.assertFalse(is_broadcast)
            return fake_response

        self.stubs.Set(cells_api, 'cell_call', fake_cell_call)

        result = cells_api.call_service_api_method(fake_context,
                fake_cell_name, fake_service, fake_method,
                *fake_method_args, **fake_method_kwargs)
        self.assertEqual(result, fake_response)

    def test_broadcast_service_api_method(self):
        fake_context = 'fake_context'
        fake_method = 'fake_method'
        fake_service = 'fake_service'
        fake_method_args = (1, 2)
        fake_method_kwargs = {'kwarg1': 10, 'kwarg2': 20}

        expected_method_info = {'method': fake_method,
                                'method_args': fake_method_args,
                                'method_kwargs': fake_method_kwargs}

        call_info = {'bcast_called': 0}

        def fake_cell_broadcast(context, direction, method, service_name,
                method_info, is_broadcast):
            self.assertEqual(context, fake_context)
            self.assertEqual(direction, 'down')
            self.assertEqual(method, 'run_service_api_method')
            self.assertEqual(service_name, fake_service)
            self.assertEqual(method_info, expected_method_info)
            self.assertTrue(is_broadcast)
            call_info['bcast_called'] += 1

        self.stubs.Set(cells_api, 'cell_broadcast', fake_cell_broadcast)

        cells_api.broadcast_service_api_method(fake_context, fake_service,
                fake_method, *fake_method_args, **fake_method_kwargs)
        self.assertEqual(call_info['bcast_called'], 1)

    def test_schedule_run_instance(self):
        fake_context = 'fake_context'
        fake_kwargs = {'kwarg1': 10, 'kwarg2': 20}

        expected_message = {'method': 'schedule_run_instance',
                            'args': fake_kwargs}

        call_info = {'cast_called': 0}

        def fake_rpc_cast(context, topic, message):
            self.assertEqual(context, fake_context)
            self.assertEqual(topic, FLAGS.cells_topic)
            self.assertEqual(message, expected_message)
            call_info['cast_called'] += 1

        self.stubs.Set(rpc, 'cast', fake_rpc_cast)

        cells_api.schedule_run_instance(fake_context,
                **fake_kwargs)
        self.assertEqual(call_info['cast_called'], 1)

    def test_instance_update(self):
        self.flags(enable_cells=True)
        fake_context = 'fake_context'
        fake_instance = 'fake_instance'
        fake_formed_message = 'fake_formed_message'

        call_info = {'cast_called': 0}

        def fake_form_instance_update_broadcast_message(instance):
            self.assertEqual(instance, fake_instance)
            return fake_formed_message

        def fake_rpc_cast(context, topic, message):
            self.assertEqual(context, fake_context)
            self.assertEqual(topic, FLAGS.cells_topic)
            self.assertEqual(message, fake_formed_message)
            call_info['cast_called'] += 1

        self.stubs.Set(cells_utils,
                'form_instance_update_broadcast_message',
                fake_form_instance_update_broadcast_message)
        self.stubs.Set(rpc, 'cast', fake_rpc_cast)

        cells_api.instance_update(fake_context, fake_instance)
        self.assertEqual(call_info['cast_called'], 1)

    def test_instance_destroy(self):
        self.flags(enable_cells=True)
        fake_context = 'fake_context'
        fake_instance = 'fake_instance'
        fake_formed_message = 'fake_formed_message'

        call_info = {'cast_called': 0}

        def fake_form_instance_destroy_broadcast_message(instance):
            self.assertEqual(instance, fake_instance)
            return fake_formed_message

        def fake_rpc_cast(context, topic, message):
            self.assertEqual(context, fake_context)
            self.assertEqual(topic, FLAGS.cells_topic)
            self.assertEqual(message, fake_formed_message)
            call_info['cast_called'] += 1

        self.stubs.Set(cells_utils,
                'form_instance_destroy_broadcast_message',
                fake_form_instance_destroy_broadcast_message)
        self.stubs.Set(rpc, 'cast', fake_rpc_cast)

        cells_api.instance_destroy(fake_context, fake_instance)
        self.assertEqual(call_info['cast_called'], 1)

    def test_sync_instances(self):
        self.flags(enable_cells=True)
        fake_context = 'fake_context'
        fake_formed_message = 'fake_formed_message'
        fake_updated_since = 'fake_updated_since'
        fake_project_id = 'fake_project_id'
        fake_deleted = 'fake_deleted'

        call_info = {'cast_called': 0}

        def fake_form_broadcast_message(direction, method, method_kwargs):
            self.assertEqual(direction, 'down')
            self.assertEqual(method, 'sync_instances')
            self.assertEqual(method_kwargs,
                    {'updated_since': fake_updated_since,
                     'project_id': fake_project_id,
                     'deleted': fake_deleted})
            return fake_formed_message

        def fake_rpc_cast(context, topic, message):
            self.assertEqual(context, fake_context)
            self.assertEqual(topic, FLAGS.cells_topic)
            self.assertEqual(message, fake_formed_message)
            call_info['cast_called'] += 1

        self.stubs.Set(cells_utils,
                'form_broadcast_message',
                fake_form_broadcast_message)
        self.stubs.Set(rpc, 'cast', fake_rpc_cast)

        cells_api.sync_instances(fake_context,
                project_id=fake_project_id,
                updated_since=fake_updated_since,
                deleted=fake_deleted)
        self.assertEqual(call_info['cast_called'], 1)
