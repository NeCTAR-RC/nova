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
from nova.scheduler.filters import project_tags_filter
from nova import test
from nova.tests.unit.scheduler import fakes


@mock.patch('nova.nectar_utils.get_project')
@mock.patch('nova.scheduler.filters.utils.aggregate_metadata_get_by_host')
class TestProjectTagsFilter(test.NoDBTestCase):

    def setUp(self):
        super().setUp()
        self.filt_cls = project_tags_filter.ProjectTagsFilter()

    @staticmethod
    def _get_spec():
        return objects.RequestSpec(
            context=mock.sentinel.ctx)

    def test_project_tags_filter_same(self, agg_mock, get_project_mock):
        get_project_mock.return_value = mock.Mock(tags=['preemptible'])
        agg_mock.return_value = {'nectar:project-tags': set(['preemptible'])}
        request = self._get_spec()
        host = fakes.FakeHostState('host1', 'node1', {})
        self.assertTrue(self.filt_cls.host_passes(host, request))

    def test_project_tags_filter_different(self, agg_mock, get_project_mock):
        get_project_mock.return_value = mock.Mock(tags=['preemptible'])
        agg_mock.return_value = {'nectar:project-tags': set(['bogus'])}
        request = self._get_spec()
        host = fakes.FakeHostState('host1', 'node1', {})
        self.assertFalse(self.filt_cls.host_passes(host, request))

    def test_project_tags_filter_no_host_tags(self, agg_mock,
                                              get_project_mock):
        get_project_mock.return_value = mock.Mock(tags=['preemptible'])
        agg_mock.return_value = {'foobar': set(['barfoo'])}
        request = self._get_spec()
        host = fakes.FakeHostState('host1', 'node1', {})
        self.assertFalse(self.filt_cls.host_passes(host, request))

    def test_project_tags_filter_bad_tags(self, agg_mock, get_project_mock):
        get_project_mock.return_value = mock.Mock(tags=['foobar'])
        agg_mock.return_value = {'nectar:project-tags': set(['preemptible'])}
        request = self._get_spec()
        host = fakes.FakeHostState('host1', 'node1', {})
        self.assertTrue(self.filt_cls.host_passes(host, request))

    def test_project_tags_filter_no_tags(self, agg_mock, get_project_mock):
        get_project_mock.return_value = mock.Mock(tags=[])
        agg_mock.return_value = {'nectar:project-tags': set(['preemptible'])}
        request = self._get_spec()
        host = fakes.FakeHostState('host1', 'node1', {})
        self.assertTrue(self.filt_cls.host_passes(host, request))
