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
from nova.cells.filters import standard_filters
from nova.cells.filters.pick_cell_filter import PickCellFilter
from nova.cells.optional_filters.restrict_filter import RestrictCellFilter
from nova import test

class TestFilterRegistration(test.TestCase):
    """Makes sure filters placed in the filters directories are all registered """
    def setUp(self):
        super(TestFilterRegistration, self).setUp()
        self.default_filters = ['PickCellFilter']
        self.optional_filters ={
                'RestrictCellFilter': 'nova.cells.optional_filters.restrict_filter.RestrictCellFilter',
                }

    def test_standard_filters_registered(self):

        filters = standard_filters()
        self.assert_(len(filters) >= 1)
        names = [cls.__name__ for cls in filters]
        self.assert_('PickCellFilter' in names)
        self.assert_('RestrictCellFilter' not in names)

    def test_get_filter_classes_default(self):
        filter_list = ['nova.cells.filters.standard_filters']
        returned_filters = filters.get_filter_classes(filter_list)

        returned_filter_names = [f.__name__ for f in returned_filters]
        for filter_path in self.default_filters:
            self.assert_(filter_path in returned_filter_names)

        self.assert_(len(returned_filters) == len(self.default_filters))

    def test_get_filter_classes_optional_and_default(self):
        filter_list = self.optional_filters.values() + ['nova.cells.filters.standard_filters']
        returned_filters = filters.get_filter_classes(filter_list)

        returned_filter_names = [f.__name__ for f in returned_filters]
        for filter_path in self.optional_filters.keys():
            self.assert_(filter_path in returned_filter_names)

        for filter_path in self.default_filters:
            self.assert_(filter_path in returned_filter_names)

        self.assert_(len(returned_filters) == len(self.optional_filters) + len(self.default_filters))

    def test_get_filter_classes_optional(self):
        filter_list = self.optional_filters.values()
        returned_filters = filters.get_filter_classes(filter_list)

        returned_filter_names = [f.__name__ for f in returned_filters]
        for filter_path in self.optional_filters.keys():
            self.assert_(filter_path in returned_filter_names)

        self.assert_(len(returned_filters) == len(self.optional_filters))

class TestPickFilter(test.TestCase):

    def test_filter_on_flag(self):
        cf = PickCellFilter()
        cells = {'first_cell':{}, 'second_cell':{}}
        len_cells = len(cells)
        filter_properties = {'scheduler_hints': {'cell': 'this-is-a-test'}}

        resp = cf.filter_cells(cells, filter_properties)

        self.assert_('action' in resp)
        self.assert_(resp['action'] == 'direct_route')
        self.assert_('target' in resp)
        self.assert_(resp['target'] == 'this!is!a!test')
        self.assert_(len(cells) == len_cells)
        self.assert_('scheduler_hints' in filter_properties)

        # check the cell flag has been removed so it isnt propagated
        # on to the cell we'll be messaging
        self.assert_('cell' not in filter_properties['scheduler_hints'])

    def test_filter_on_no_flag(self):
        cf = PickCellFilter()
        cells = {'first_cell':{}, 'second_cell':{}}
        len_cells = len(cells)
        filter_properties = {'scheduler_hints': {'some_other_flag': 'this-is-a-test'}}

        resp = cf.filter_cells(cells, filter_properties)

        # confirm that no action will be taken as we didnt get our flag
        self.assert_('action' not in resp)
        self.assert_('target' not in resp)
        self.assert_(len(cells) == len_cells)

class TestRestrictCellFilter(test.TestCase):

    def test_filter_on_role(self):

        class FakeCellInfo(object):
            def __init__(self, capabilites):
                self.capabilities = capabilites

            def get_cell_info(self):
                return {'capabilities': self.capabilities}

        class FakeContext(object):
            def __init__(self, context_variables):
                self.context_variables = context_variables

            def __getattr__(self, attr):
                return self.context_variables.get(attr, None)

        fake_role_1 = 'fake_role_1'
        fake_role_2 = 'fake_role_2'
        unrestricted_role = 'unrestricted'
        restricted_cell_1 = FakeCellInfo({'required_roles':[fake_role_1]})
        restricted_cell_2 = FakeCellInfo({'required_roles':[fake_role_2]})
        unrestricted_cell = FakeCellInfo({'required_roles':[unrestricted_role]})

        cells = [restricted_cell_1,restricted_cell_2, unrestricted_cell]

        cf = RestrictCellFilter()

        filter_properties_1 = {'context': FakeContext({'roles': [fake_role_1]})}
        filter_properties_2 = {'context': FakeContext({'roles': [fake_role_2]})}
        filter_properties_no_roles = {'context': FakeContext({'roles': []})}

        resp = cf.filter_cells(cells, filter_properties_1)
        self.assert_('drop' in resp)
        self.assert_('action' not in resp)
        self.assert_(restricted_cell_2 in resp['drop'])
        self.assert_(unrestricted_cell not in resp['drop'])
        self.assert_(restricted_cell_1 not in resp['drop'])

        resp = cf.filter_cells(cells, filter_properties_2)
        self.assert_('drop' in resp)
        self.assert_('action' not in resp)
        self.assert_(restricted_cell_1 in resp['drop'])
        self.assert_(unrestricted_cell not in resp['drop'])
        self.assert_(restricted_cell_2 not in resp['drop'])

        resp = cf.filter_cells(cells, filter_properties_no_roles)
        self.assert_('drop' in resp)
        self.assert_(restricted_cell_1 in resp['drop'])
        self.assert_(unrestricted_cell not in resp['drop'])
        self.assert_(restricted_cell_2 in resp['drop'])
        self.assert_('action' not in resp)
