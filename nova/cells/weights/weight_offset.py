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
Returns the weight_offset from the DB. Originally intended to be used to set a
default cell by setting its weight offset to 999999999 or some other extremely
high value, depending on the other weighting plugins and sorts of values
they're returning.
"""

from nova.cells import weights


class WeightOffsetWeighter(weights.BaseCellWeighter):
    """
    Weight cell by weight_offset db field.
    Originally designed so you can set a default cell by putting
    its weight_offset to 999999999999999 (highest weight wins)
    """

    def cell_weight(self, cell, weight_properties):
        """Returns whatever was in the DB for weight_offset"""
        return cell.db_info.get('weight_offset', 0)
