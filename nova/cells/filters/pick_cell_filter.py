# Copyright (c) 2012 OpenStack, LLC.
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

"""
Pick cell specified by the cell scheduler hint
"""

from nova.cells.filters import BaseCellFilter
import logging

LOG = logging.getLogger(__name__)

class PickCellFilter(BaseCellFilter):
    """Pick cell specified by the cell scheduler hint"""

    def filter_cells(self, cells, filter_properties):
        """ """
        LOG.info(_("Filtering cells for a specific cell name"))
        scheduler_hints = filter_properties.get('scheduler_hints', {}) or {}
        cell_name = scheduler_hints.get('cell', None)
        if not cell_name:
            return {}
        # remove this as once the call reaches the 
        # specified top cell, we want to proceed
        # using normal scheduling
        scheduler_hints.pop('cell')
        
        # FIXME: cell name will come in hyphen separated
        # from the CLI as bangs break it. This should be
        # massaged out at a higher level
        cell_name = cell_name.replace('-', '!')

        LOG.info(_("Filtering for cell '%(cell_name)s'"), locals())
        resp = {
            'action': 'direct_route',
            'target': cell_name,
            }

        return resp
