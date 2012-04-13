# Copyright (c) 2012 Openstack, LLC.
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
Base Cells Driver
"""

from nova.db import base
from nova import flags
from nova.openstack.common import log as logging

LOG = logging.getLogger('nova.cells.driver')
FLAGS = flags.FLAGS


class BaseCellsDriver(base.Base):
    """The base class for cells communication."""

    def __init__(self, manager):
        super(BaseCellsDriver, self).__init__()
        self.manager = manager
