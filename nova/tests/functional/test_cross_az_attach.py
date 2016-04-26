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

from nova import test
from nova.tests import fixtures as nova_fixtures
from nova.tests.functional import fixtures as func_fixtures
from nova.tests.functional import integrated_helpers
from nova.tests.unit.image import fake as fake_image
from nova.tests.unit import policy_fixture


class CrossAZAttachTestCase(test.TestCase,
                            integrated_helpers.InstanceHelperMixin):
    """Contains various scenarios for the [cinder]/cross_az_attach option
    and how it affects interactions between nova and cinder in the API and
    compute service.
    """
    az = 'us-central-1'

    def setUp(self):
        super(CrossAZAttachTestCase, self).setUp()
        # Use the standard fixtures.
        self.useFixture(policy_fixture.RealPolicyFixture())
        self.useFixture(nova_fixtures.CinderFixture(self, az=self.az))
        self.useFixture(nova_fixtures.NeutronFixture(self))
        self.useFixture(func_fixtures.PlacementFixture())
        fake_image.stub_out_image_service(self)
        self.addCleanup(fake_image.FakeImageService_reset)
        # Start nova controller services.
        self.api = self.useFixture(nova_fixtures.OSAPIFixture(
            api_version='v2.1')).admin_api
        self.start_service('conductor')
        self.start_service('scheduler')
        # Start one compute service.
        self.start_service('compute', host='host1')

    def test_cross_az_attach_false_boot_from_volume_default_zone_match(self):
        """Tests the scenario where [cinder]/cross_az_attach=False and the
        server is created with a pre-existing volume and the
        [DEFAULT]/default_schedule_zone matches the volume's AZ.
        """
        self.flags(cross_az_attach=False, group='cinder')
        self.flags(default_schedule_zone=self.az)
        # For this test we have to put the compute host in an aggregate with
        # the AZ we want to match.
        agg_id = self.api.post_aggregate({
            'aggregate': {
                'name': self.az,
                'availability_zone': self.az
            }
        })['id']
        self.api.add_host_to_aggregate(agg_id, 'host1')

        server = self._build_minimal_create_server_request(
            self.api,
            'test_cross_az_attach_false_boot_from_volume_default_zone_match')
        del server['imageRef']  # Do not need imageRef for boot from volume.
        server['block_device_mapping_v2'] = [{
            'source_type': 'volume',
            'destination_type': 'volume',
            'boot_index': 0,
            'uuid': nova_fixtures.CinderFixture.IMAGE_BACKED_VOL
        }]
        server = self.api.post_server({'server': server})
        server = self._wait_for_state_change(self.api, server, 'ACTIVE')
        self.assertEqual(self.az, server['OS-EXT-AZ:availability_zone'])
