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
Tests For Cells Utils interfaces
"""

from nova.cells import utils as cells_utils
from nova import test


class CellsUtilsTestCase(test.TestCase):
    """Test case for cells.common interfaces."""

    def test_reverse_path(self):
        path = 'a!b!c!d'
        expected = 'd!c!b!a'
        rev_path = cells_utils.reverse_path(path)
        self.assertEqual(rev_path, expected)

    def test_path_without_hosts(self):
        # test array with tuples of inputs/expected outputs
        test_paths = [('cell1@host1', 'cell1'),
                      ('cell1@host1!cell2@host2!cell3',
                            'cell1!cell2!cell3'),
                      ('cell1!cell2!cell3',
                            'cell1!cell2!cell3'),
                      ('cell1@host1!cell2!cell3@host3',
                            'cell1!cell2!cell3')]

        for test_input, expected_output in test_paths:
            self.assertEqual(expected_output,
                    cells_utils.path_without_hosts(test_input))

    def test_response_cell_name_from_path(self):
        # test array with tuples of inputs/expected outputs
        test_paths = [('cell1', 'cell1'),
                      ('cell1@host1', 'cell1@host1'),
                      ('cell1!cell2', 'cell2!cell1'),
                      ('cell1@host1!cell2@host2!cell3',
                              'cell3!cell2!cell1@host1'),
                      ('cell1!cell2@host2!cell3@host3',
                              'cell3!cell2!cell1')]

        for test_input, expected_output in test_paths:
            self.assertEqual(expected_output,
                    cells_utils.response_cell_name_from_path(test_input))

    def test_form_routing_message_basic(self):
        fake_method = 'fake_method'
        fake_method_kwargs = {'kwarg1': 10, 'kwarg2': 20}
        rtg_message = cells_utils.form_routing_message('fake_cell',
                'fake_direction', fake_method, fake_method_kwargs)
        expected = {'method': 'route_message',
                    'args': {'dest_cell_name': 'fake_cell',
                             'direction': 'fake_direction',
                             'message': {'method': fake_method,
                                         'args': fake_method_kwargs},
                             'routing_path': None}}
        self.assertEqual(rtg_message, expected)

    def test_form_routing_message_full(self):
        fake_method = 'fake_method'
        fake_method_kwargs = {'kwarg1': 10, 'kwarg2': 20}
        rtg_message = cells_utils.form_routing_message('fake_cell',
                'fake_direction', fake_method, fake_method_kwargs,
                need_response=True, response_uuid='fake_uuid',
                routing_path='fake_path')
        expected = {'method': 'route_message',
                    'args': {'dest_cell_name': 'fake_cell',
                             'direction': 'fake_direction',
                             'message': {'method': fake_method,
                                         'args': fake_method_kwargs},
                             'routing_path': 'fake_path',
                             'need_response': True,
                             'response_uuid': 'fake_uuid'}}
        self.assertEqual(rtg_message, expected)

    def test_form_broadcast_message_basic(self):
        fake_method = 'fake_method'
        fake_method_kwargs = {'kwarg1': 10, 'kwarg2': 20}
        bcast_message = cells_utils.form_broadcast_message(
                'fake_direction', fake_method, fake_method_kwargs)
        expected = {'method': 'broadcast_message',
                    'args': {'direction': 'fake_direction',
                             'message': {'method': fake_method,
                                         'args': fake_method_kwargs},
                             'routing_path': None,
                             'hopcount': 0,
                             'fanout': False}}
        self.assertEqual(bcast_message, expected)

    def test_form_broadcast_message_full(self):
        fake_method = 'fake_method'
        fake_method_kwargs = {'kwarg1': 10, 'kwarg2': 20}
        bcast_message = cells_utils.form_broadcast_message(
                'fake_direction', fake_method, fake_method_kwargs,
                routing_path='fake_path', hopcount=1, fanout=True)
        expected = {'method': 'broadcast_message',
                    'args': {'direction': 'fake_direction',
                             'message': {'method': fake_method,
                                         'args': fake_method_kwargs},
                             'routing_path': 'fake_path',
                             'hopcount': 1,
                             'fanout': True}}
        self.assertEqual(bcast_message, expected)

    def test_form_instance_update_broadcast_message(self):
        fake_instance = {'uuid': 'fake_uuid',
                         'task_state': 'fake_task_state',
                         'vm_state': 'fake_vm_state',
                         'security_groups': 'foo',
                         'info_cache': {'id': 1, 'network_info': 'meow'},
                         'metadata': [{'key': 'key1', 'value': 'val1'},
                                      {'key': 'key2', 'value': 'val2'}]}
        bcast_message = cells_utils.form_instance_update_broadcast_message(
                fake_instance)

        instance_info = fake_instance.copy()
        # This gets filtered
        instance_info.pop('security_groups')
        # This gets converted
        instance_info['metadata'] = {'key1': 'val1', 'key2': 'val2'}
        # This gets 'id' stripped
        instance_info['info_cache'] = {'network_info': 'meow'}
        message = {'method': 'instance_update',
                   'args': {'instance_info': instance_info}}
        message = {'method': 'instance_update',
                   'args': {'instance_info': instance_info}}
        expected = {'method': 'broadcast_message',
                    'args': {'direction': 'up',
                             'message': message,
                             'routing_path': None,
                             'hopcount': 0,
                             'fanout': False}}
        self.assertEqual(bcast_message, expected)

    def test_form_instance_destroy_broadcast_message(self):
        fake_instance = {'uuid': 'fake_uuid',
                         'task_state': 'fake_task_state',
                         'vm_state': 'fake_vm_state',
                         'not_copied': 'foo'}
        bcast_message = cells_utils.form_instance_destroy_broadcast_message(
                fake_instance)

        message = {'method': 'instance_destroy',
                   'args': {'instance_info': {'uuid': 'fake_uuid'}}}
        expected = {'method': 'broadcast_message',
                    'args': {'direction': 'up',
                             'message': message,
                             'routing_path': None,
                             'hopcount': 0,
                             'fanout': False}}
        self.assertEqual(bcast_message, expected)

    def test_update_routing_path_decorator(self):
        found_routing_paths = []

        @cells_utils.update_routing_path
        def test_routing_path(self, routing_path=None):
            found_routing_paths.append(routing_path)

        my_name = 'test_cell_name@test_hostname'

        paths_to_test = [None, None, '', 'cell1@host1']
        expected_paths = [my_name, my_name, my_name,
                "cell1@host1!%s" % my_name]

        class FakeManager(object):
            our_path = my_name

        mgr = FakeManager()

        for i, path in enumerate(paths_to_test):
            if i == 0:
                test_routing_path(mgr)
            else:
                test_routing_path(mgr, routing_path=path)

        for i, path in enumerate(expected_paths):
            self.assertEqual(path, found_routing_paths[i])
