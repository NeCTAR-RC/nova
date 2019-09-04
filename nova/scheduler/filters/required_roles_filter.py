# Copyright (c) 2019 Australian Research Data Commons
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


from nova.scheduler import filters
from nova.scheduler.filters import utils


LOG = logging.getLogger(__name__)


class RequiredRolesFilter(filters.BaseHostFilter):
    """Requires users to have a certain roles"""

    def host_passes(self, host_state, spec_obj):
        """If required_roles is set in an aggregate, return True if user has
        such a role, False otherwise.

        If required_roles is not set, return True.

        """

        # get user's role from context
        user_roles = spec_obj._context.roles

        metadata = utils.aggregate_metadata_get_by_host(host_state)
        required_roles = metadata.get('required_roles', None)

        if required_roles is None:
            # Passes if required_roles is not set in an aggregate
            return True

        if required_roles:
            metadata_roles = [x.strip() for x in required_roles.split(',')]
            if set(user_roles).intersection(metadata_roles):
                return True

        return False
