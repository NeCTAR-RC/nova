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

import nova.conf
from nova import objects
from nova.scheduler.filters import restricted_zone_filter
from nova import test
from nova.tests.unit.scheduler import fakes


CONF = nova.conf.CONF


@mock.patch('nova.availability_zones.get_restricted_zones')
@mock.patch('nova.scheduler.filters.utils.aggregate_metadata_get_by_host')
class TestRestrictedZoneFilter(test.NoDBTestCase):

    def setUp(self):
        super(TestRestrictedZoneFilter, self).setUp()
        self.filt_cls = restricted_zone_filter.RestrictedZoneFilter()
        CONF.set_override('restrict_zones', True)

    @staticmethod
    def _make_zone_request(zone):
        return objects.RequestSpec(
            context=mock.sentinel.ctx,
            availability_zone=zone)

    def test_restricted_zone_filter_same(self, agg_mock, restricted_mock):
        restricted_mock.return_value = ['nova']
        agg_mock.return_value = {'availability_zone': set(['nova'])}
        request = self._make_zone_request(None)
        host = fakes.FakeHostState('host1', 'node1', {})
        self.assertTrue(self.filt_cls.host_passes(host, request))

    def test_restricted_zone_filter_same_comma(self, agg_mock,
            restricted_mock):
        restricted_mock.return_value = ['nova', 'nova3']
        agg_mock.return_value = {'availability_zone': set(['nova', 'nova2'])}
        request = self._make_zone_request(None)
        host = fakes.FakeHostState('host1', 'node1', {})
        self.assertTrue(self.filt_cls.host_passes(host, request))

    def test_restricted_zone_filter_different(self, agg_mock, restricted_mock):
        restricted_mock.return_value = ['nova2']
        agg_mock.return_value = {'availability_zone': set(['nova'])}
        request = self._make_zone_request(None)
        host = fakes.FakeHostState('host1', 'node1', {})
        self.assertFalse(self.filt_cls.host_passes(host, request))

    def test_restricted_zone_filter_requested_zone(self, agg_mock,
            restricted_mock):
        restricted_mock.return_value = ['nova']
        agg_mock.return_value = {'availability_zone': set(['nova'])}
        request = self._make_zone_request('nova')
        host = fakes.FakeHostState('host1', 'node1', {})
        self.assertTrue(self.filt_cls.host_passes(host, request))
        restricted_mock.assert_not_called()

    def test_restricted_zone_filter_no_restricted(self, agg_mock,
            restricted_mock):
        restricted_mock.return_value = []
        agg_mock.return_value = {'availability_zone': set(['nova'])}
        request = self._make_zone_request(None)
        host = fakes.FakeHostState('host1', 'node1', {})
        self.assertTrue(self.filt_cls.host_passes(host, request))
