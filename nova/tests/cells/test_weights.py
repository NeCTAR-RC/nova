# Copyright (c) 2012 Openstack, LLC
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
Unit Tests for testing the weight algorithms.

Cells with higher weights should be given priority for new builds.
"""

from nova.cells import weights
from nova.cells.weights import standard_weighters
from nova import test


class CheckWeighterContents(test.TestCase):
    """Makes sure the proper weighters are in the directory"""

    def test_theyAreThere(self):
        weighters = standard_weighters()
        self.assert_(len(weighters) >= 2)
        weightPluginNames = [cls.__name__ for cls in weighters]
        self.assert_('WeightOffsetWeighter' in weightPluginNames)
        self.assert_('CellSpreadByRamWeighter' in weightPluginNames)


class TestIndivdualWeighterPlugins(test.TestCase):
    """Tests that each plugin does what it should"""

    def setUp(self):
        super(TestIndivdualWeighterPlugins, self).setUp()
        self.weighters = dict([(cls.__name__, cls)
                for cls in standard_weighters()])

    def test_weightOffset(self):
        """The weight_offset weighter should add the weight_offset field
        from the DB
        """
        offsetWeighter = self.weighters['WeightOffsetWeighter']()

        class FakeCell(object):
            def __init__(self, offset=0):
                self.db_info = {'weight_offset': offset}

        offsets = [0, -9999999, 100.4]
        cells = map(FakeCell, offsets)
        results = [offsetWeighter.cell_weight(cell, {}) for cell in cells]
        self.assert_(offsets == results)

    def test_byRamWeighter(self):
        """Test that cells with more ram available return a higher weight."""
        # We simulate the creation of a new 512 mb slice.
        # Each cell returns how many points they give to that size of slice,
        # if the cell returns a higher number, that means they want the slice
        # to be built in them.
        #
        # The weighter's result should have a *higher* number for the winner.
        #
        # So the cell that wants the the new slice the most, in the end will
        # have the lowest weight.

        ramWeighter = self.weighters['CellSpreadByRamWeighter']

        class FakeCell(object):
            def __init__(self, free_units):
                self.capacities = {
                    'ram_free': {'total_mb': 512,
                                 'units_by_mb': {'512': free_units}},
                    'disk_free': {'total_mb': 999999,
                                  'units_by_mb': {'1024': 2}}
                   }

        # The number of points each cell gives to the flavor 512
        free_units = [100, 200, 201, 99, 199]
        cells = map(FakeCell, free_units)
        weighing_properties = {'request_spec':
                {'instance_type': {'memory_mb': '512'}}}
        weighter_classes = [ramWeighter]
        weighted_cells = weights.get_weighted_cells(weighter_classes, cells,
                weighing_properties)
        self.assertEqual(len(weighted_cells), 5)
        self.assertEqual(weighted_cells[0].cell, cells[2])
        self.assertEqual(weighted_cells[1].cell, cells[1])
        self.assertEqual(weighted_cells[2].cell, cells[4])
        self.assertEqual(weighted_cells[3].cell, cells[0])
        self.assertEqual(weighted_cells[4].cell, cells[3])
