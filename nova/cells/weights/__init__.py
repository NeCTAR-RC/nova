# Copyright (c) 2011-2012 OpenStack, LLC.
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
Cell Scheduler weights
"""

import os
import types

from nova import exception
from nova.openstack.common import importutils


class BaseCellWeighter(object):
    """Base class for cell weighters."""
    def fn_weight(self):
        """How weighted this weighter should be.  Normally this would
        be overriden in a subclass based on a config value.
        """
        return 1.0

    def cell_weight(self, cell, weight_properties):
        """Override in a subclass to weigh a cell."""
        return 0.0


def _is_weighter_class(cls):
    """Return whether a class is a valid Cell Weighter class."""
    return type(cls) is types.TypeType and issubclass(cls, BaseCellWeighter)


def _get_weighter_classes_from_module(module_name):
    """Get all weighter classes from a module."""
    classes = []
    module = importutils.import_module(module_name)
    for obj_name in dir(module):
        itm = getattr(module, obj_name)
        if _is_weighter_class(itm):
            classes.append(itm)
    return classes


def standard_weighters():
    """Return a list of weighter classes found in this directory."""
    classes = []
    weighters_dir = __path__[0]
    for dirpath, dirnames, filenames in os.walk(weighters_dir):
        relpath = os.path.relpath(dirpath, weighters_dir)
        if relpath == '.':
            relpkg = ''
        else:
            relpkg = '.%s' % '.'.join(relpath.split(os.sep))
        for fname in filenames:
            root, ext = os.path.splitext(fname)
            if ext != '.py' or root == '__init__':
                continue
            module_name = "%s%s.%s" % (__package__, relpkg, root)
            mod_classes = _get_weighter_classes_from_module(module_name)
            classes.extend(mod_classes)
    return classes


def get_weighter_classes(weighter_class_names):
    """Get weighter classes from class names."""
    classes = []
    for cls_name in weighter_class_names:
        obj = importutils.import_class(cls_name)
        if _is_weighter_class(obj):
            classes.append(obj)
        elif type(obj) is types.FunctionType:
            # Get list of classes from a function
            classes.extend(obj())
        else:
            raise exception.ClassNotFound(class_name=cls_name,
                    exception='Not a valid cell scheduler weighter')
    if not classes:
        return [BaseCellWeighter]
    return classes


class WeightedCell(object):
    """Cell with weight information."""
    def __init__(self, cell, weight):
        self.cell = cell
        self.weight = weight

    def __repr__(self):
        return "<WeightedCell '%s': %s>" % (self.cell.name, self.weight)


def get_weighted_cells(weighter_classes, cells, weighing_properties):
    """Return a sorted (highest score first) list of WeightedCells."""

    weighters = []

    for weighter_cls in weighter_classes:
        weighter = weighter_cls()
        weighters.append(weighter)

    weighted_cells = []
    for cell in cells:
        weight = 0.0
        for weighter in weighters:
            weight += (weighter.fn_weight() *
                    weighter.cell_weight(cell, weighing_properties))
        weighted_cells.append(WeightedCell(cell, weight))
    return sorted(weighted_cells, key=lambda x: x.weight, reverse=True)
