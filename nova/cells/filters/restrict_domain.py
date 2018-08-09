# Copyright (c) 2012-2013 University of Melbourne
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

from nova.cells import filters
from nova.i18n import _LW


LOG = logging.getLogger(__name__)


class DomainRestrictCellFilter(filters.BaseCellFilter):

    def filter_all(self, cells, filter_properties):
        domain = filter_properties['context'].project_domain
        if not domain:
            LOG.warning(_LW("No domain found in context using default"))
            domain = 'default'
        LOG.debug("Project Domain is %s", domain)
        allowed_cells = []
        for cell in cells:
            cell_capabilities = cell.capabilities
            allowed_domains = cell_capabilities.get('allowed_domains',
                                                    ['default'])
            LOG.debug("Allowed Domains %s", allowed_domains)
            if domain in allowed_domains:
                allowed_cells.append(cell)
        return allowed_cells
