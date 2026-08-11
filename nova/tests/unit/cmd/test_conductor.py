# Licensed under the Apache License, Version 2.0 (the "License"); you may
# not use this file except in compliance with the License. You may obtain
# a copy of the License at
#
#    http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS, WITHOUT
# WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied. See the
# License for the specific language governing permissions and limitations
# under the License.

from unittest import mock

from nova.cmd import conductor
from nova import config
from nova import test


# required because otherwise oslo early parse_args dies
@mock.patch.object(config, 'parse_args', new=lambda *args, **kwargs: None)
class TestConductor(test.NoDBTestCase):

    @mock.patch('nova.service.Service.create')
    @mock.patch('nova.service.serve')
    @mock.patch('nova.service.wait')
    @mock.patch('nova.scheduler.client.report.close_pooled_connections')
    def test_placement_connections_closed_before_fork(
        self, mock_close, mock_wait, mock_serve, service_create
    ):
        # serve() forks the workers, so the close must come first
        mock_serve.side_effect = (
            lambda *a, **kw: mock_close.assert_called_once_with())

        conductor.main()

        mock_serve.assert_called_once_with(
            service_create.return_value, workers=mock.ANY)

    @mock.patch('nova.utils.allow_cell_scoped_version_check')
    @mock.patch('nova.service.Service.create')
    @mock.patch('nova.service.serve')
    @mock.patch('nova.service.wait')
    def test_allows_cell_scoped_version_check(
        self, mock_wait, mock_serve, service_create, mock_allow
    ):
        # The start-up version check runs inside Service.create(), so the
        # conductor has to opt in before that.
        service_create.side_effect = (
            lambda **kwargs: mock_allow.assert_called_once_with())

        conductor.main()

        service_create.assert_called_once_with(
            binary='nova-conductor', topic=mock.ANY)
