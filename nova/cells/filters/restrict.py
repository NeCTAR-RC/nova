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

from oslo_config import cfg
from oslo_log import log as logging

from nova.cells import filters


LOG = logging.getLogger(__name__)

direct_only_cells = cfg.ListOpt(
    'scheduler_direct_only_cells',
    default=[],
    help='Cells that can only be scheduled to directly. '
         'I.e., with the "cell" scheduler hint.')

CONF = cfg.CONF
CONF.register_opt(direct_only_cells, group='cells')


class RestrictCellFilter(filters.BaseCellFilter):

    def filter_all(self, cells, filter_properties):
        roles = filter_properties['context'].roles
        allowed_cells = []
        for cell in cells:
            cell_capabilities = cell.capabilities
            cell_required_roles = cell_capabilities.get('required_roles', [])

            if (not cell_required_roles or
                    'unrestricted' in cell_required_roles):
                allowed_cells.append(cell)
                continue
            matching_roles = set(cell_required_roles).intersection(set(roles))
            if matching_roles:
                allowed_cells.append(cell)
        return allowed_cells


class DirectOnlyCellFilter(filters.BaseCellFilter):

    def cell_passes(self, cell, filter_properties):

        direct_cell_names = CONF.cells.scheduler_direct_only_cells

        spec = filter_properties.get('request_spec', {})
        props = spec.get('instance_properties', {})
        availability_zone = props.get('availability_zone')
        if not availability_zone:
            # No AZ flag set, try deprecated scheduler hint
            scheduler_hints = filter_properties.get(
                'scheduler_hints', {}) or {}
            availability_zone = scheduler_hints.get('cell', None)

        # If AZ is not directly specified and cell is direct
        # only then don't allow
        if not availability_zone and cell.name in direct_cell_names:
            LOG.debug('Cell %s only accessible directly' % cell)
            return False

        return True
