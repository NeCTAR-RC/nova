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

from nova.cells import filters


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
