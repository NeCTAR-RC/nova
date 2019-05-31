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

from oslo_log import log as logging

from nova import availability_zones
from nova.cells import filters

LOG = logging.getLogger(__name__)


class AvailabilityZoneFilter(filters.BaseCellFilter):
    """Filters cells by availability zone.

    Works with cell capabilities using the key
    'availability_zones'
    Note: cell can have multiple availability zones
    """

    def cell_passes(self, cell, filter_properties):
        LOG.debug('Filtering on availability zones for cell %s', cell)

        available_zones = cell.capabilities.get('availability_zones', [])
        LOG.debug('Available zones: %s', available_zones)
        spec = filter_properties.get('request_spec', {})
        props = spec.get('instance_properties', {})
        availability_zone = props.get('availability_zone')

        if availability_zone:
            return availability_zone in available_zones
        else:
            restricted_zones = availability_zones.get_restricted_zones(
                filter_properties['context'])
            if restricted_zones and not set(restricted_zones).intersection(
                    set(available_zones)):
                return False
        return True
