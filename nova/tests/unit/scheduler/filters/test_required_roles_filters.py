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

import mock

from nova import objects
from nova.scheduler.filters import required_roles_filter
from nova import test
from nova.tests.unit.scheduler import fakes


class TestRequiredRolesFilter(test.NoDBTestCase):

    def setUp(self):
        super(TestRequiredRolesFilter, self).setUp()
        self.filt_cls = required_roles_filter.RequiredRolesFilter()

    def test_required_roles_filter_passes_no_required_roles(self):
        context = mock.sentinel.ctx
        context.roles = []
        spec_obj = objects.RequestSpec(
            context=context)
        host = fakes.FakeHostState('host1', 'node1', {})
        self.assertTrue(self.filt_cls.host_passes(host, spec_obj))

    @mock.patch('nova.scheduler.filters.utils.aggregate_metadata_get_by_host')
    def test_required_roles_filter_fails_user_no_role(self, agg_mock):
        context = mock.sentinel.ctx
        context.roles = []
        spec_obj = objects.RequestSpec(
            context=context)
        host = fakes.FakeHostState('host1', 'node1', {})
        agg_mock.return_value = {'required_roles': 'role1'}
        self.assertFalse(self.filt_cls.host_passes(host, spec_obj))

    @mock.patch('nova.scheduler.filters.utils.aggregate_metadata_get_by_host')
    def test_required_roles_filter_passes_user_has_role(self, agg_mock):
        context = mock.sentinel.ctx
        context.roles = ['role1', 'role2']
        spec_obj = objects.RequestSpec(
            context=context)
        host = fakes.FakeHostState('host1', 'node1', {})
        agg_mock.return_value = {'required_roles': 'role1'}
        self.assertTrue(self.filt_cls.host_passes(host, spec_obj))

    @mock.patch('nova.scheduler.filters.utils.aggregate_metadata_get_by_host')
    def test_required_roles_filter_passes_multiple_roles(self, agg_mock):
        context = mock.sentinel.ctx
        context.roles = ['role1']
        spec_obj = objects.RequestSpec(
            context=context)
        host = fakes.FakeHostState('host1', 'node1', {})
        agg_mock.return_value = {'required_roles': 'role1,role2'}
        self.assertTrue(self.filt_cls.host_passes(host, spec_obj))
