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

from nova import flags
from nova.openstack.common import cfg
from nova.cells.filters import BaseCellFilter
import logging

LOG = logging.getLogger(__name__)


direct_only_cells = cfg.ListOpt(
    'scheduler_direct_only_cells',
    default=[],
    help='Cells that can only be scheduled to directly. '
         'I.e., with the "cell" scheduler hint.')

FLAGS = flags.FLAGS
FLAGS.register_opt(direct_only_cells, group='cells')


class RestrictCellFilter(BaseCellFilter):
    def filter_cells(self, cells, filter_properties):
        roles = filter_properties['context'].roles
        drop = []
        for cell in cells:
            cell_capabilities = cell.capabilities
            cell_required_roles = cell_capabilities.get('required_roles', [])
            if not cell_required_roles or 'unrestricted' in cell_required_roles:
                continue
            matching_roles = set(cell_required_roles).intersection(set(roles))
            if not len(matching_roles):
                drop.append(cell)
        if not drop:
            return None
        return {'drop' : drop}


class DirectOnlyCellFilter(BaseCellFilter):
    def filter_cells(self, cells, filter_properties):
        # Just always drop the direct only cells. The pick cell filter
        # should be before this filter in the filter list.
        direct_cell_names = FLAGS.cells.scheduler_direct_only_cells
        cell_names = [cell for cell in cells
                        if cell.name in direct_cell_names]
        return {'drop': cell_names}
