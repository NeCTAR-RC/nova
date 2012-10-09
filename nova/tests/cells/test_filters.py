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
Unit Tests for testing cell scheduler filters.
"""

from nova.cells import filters
from nova.cells.filters import standard_filters, filter_cells
from nova.cells.filters.pick_cell_filter import PickCellFilter
from nova import test

class TestFilterRegistration(test.TestCase):
    """Makes sure filters placed in the filters director are all registered """

    def test_fitlers_registered(self):
        filters = standard_filters()
        self.assert_(len(filters) >= 1)
        names = [cls.__name for cls in filters]
        self.assert_('PickCellFilter' in names)

class TestPickFilter(test.TestCase):

    def setUp(self):
        super(TestPickFilter, self).setUp()
        self.filters = dict([(cls.__name__, cls)
                for cls in standard_weighters()])

        def test_filter_on_flag(self):
            cf = PickCellFilter()
            cells = {'first_cell':{}, 'second_cell':{}}
            len_cells = len(cells)
            filter_properties = {'scheduler_hints': {'use_cell': 'this-is-a-test'}}
            
            resp = cf.filter_cells(cells, filter_properties)
            
            self.assert_('action' in resp)
            self.assert_(resp['action'] == 'direct_route')
            self.assert_('target' in resp)
            self.assert_(resp['target'] == 'this!is!a!test')
            self.assert_(len(cells) = len_cells)
            self_assert('scheduler_hints' in filter_properties)

            # check the use_cell flag has been removed so it isnt propagated
            # on to the cell we'll be messaging
            self_assert('use_cell' not in filter_properties['scheduler_hints'])
        
        def test_filter_on_no_flag(self):
            cf = PickCellFilter()
            cells = {'first_cell':{}, 'second_cell':{}}
            len_cells = len(cells)
            filter_properties = {'scheduler_hints': {'some_other_flag': 'this-is-a-test'}}
            
            resp = cf.filter_cells(cells, filter_properties)
           
            # confirm that no action will be taken as we didnt get our flag
            self.assert_('action' not in resp)
            self.assert_('target' not in resp)
            self.assert_(len(cells) = len_cells)
