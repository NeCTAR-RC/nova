# Copyright (c) 2011-2012 OpenStack Foundation
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

from oslo_log import log as logging

from nova import availability_zones
import nova.conf
from nova.scheduler import filters
from nova.scheduler.filters import utils

LOG = logging.getLogger(__name__)

CONF = nova.conf.CONF


class RestrictedZoneFilter(filters.BaseHostFilter):
    """Filters Hosts by restricted availability zones

    Restricted availability zones are determined by a poperty
    set in keystone. If none set it is assumed a pass.
    """

    # Availability zones do not change within a request
    run_filter_once_per_request = True

    def host_passes(self, host_state, spec_obj):
        availability_zone = spec_obj.availability_zone

        if availability_zone:
            # Authorisation of what AZ's can be used is handled at
            # the API level so we only need to check when a request doesn't
            # supply an availability-zone in the boot request.
            return True

        restricted_zones = availability_zones.get_restricted_zones(
            spec_obj._context)

        if not restricted_zones:
            return True

        metadata = utils.aggregate_metadata_get_by_host(
                host_state, key='availability_zone')

        if 'availability_zone' in metadata:
            host_az = metadata['availability_zone']
        else:
            host_az = set([CONF.default_availability_zone])

        if set(restricted_zones).intersection(host_az):
            return True

        LOG.debug("Restricted zones '%(restricted_zones)s'. "
                  "%(host_state)s has AZs: %(host_az)s",
                  {'host_state': host_state,
                   'restricted_zones': restricted_zones,
                   'host_az': host_az})

        return False
