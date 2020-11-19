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

import nova.conf
from nova import nectar_utils
from nova.scheduler import filters
from nova.scheduler.filters import utils

LOG = logging.getLogger(__name__)

CONF = nova.conf.CONF


class ProjectTagsFilter(filters.BaseHostFilter):
    """Filters Hosts by project tags

    Restricted availability zones are determined by a poperty
    set in keystone. If none set it is assumed a pass.
    """

    # Project tags do not change within a request
    run_filter_once_per_request = True
    host_key = 'nectar:project-tags'

    # Allowed tags to filter on
    allowed_tags = ['preemptible']

    def host_passes(self, host_state, spec_obj):

        project = nectar_utils.get_project(spec_obj._context)
        project_tags = set(project.tags)
        allowed_tags = set(self.allowed_tags)

        if project_tags.intersection(allowed_tags):
            LOG.warn("Project has allowed_key")
            metadata = utils.aggregate_metadata_get_by_host(
                host_state, key=self.host_key)

            if self.host_key in metadata:
                LOG.warn("host has key")
                host_tags = metadata[self.host_key]

                if not project_tags.intersection(host_tags):
                    LOG.warn("No match")
                    return False
            else:
                LOG.warn("No host key")
                return False

        return True
