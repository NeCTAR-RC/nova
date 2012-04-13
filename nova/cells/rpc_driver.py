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
Cells RPC Driver
"""

from nova.cells import driver
from nova import flags
from nova.openstack.common import log as logging
from nova.openstack.common import rpc

LOG = logging.getLogger('nova.cells.rpc_driver')
FLAGS = flags.FLAGS


class CellsRPCDriver(driver.BaseCellsDriver):
    """Handles cell communication via RPC."""

    def __init__(self, manager):
        super(CellsRPCDriver, self).__init__(manager)

    def _get_server_params_for_cell(self, next_hop):
        param_map = {'username': 'username',
                     'password': 'password',
                     'rpc_host': 'hostname',
                     'rpc_port': 'port',
                     'rpc_virtual_host': 'virtual_host'}
        server_params = {}
        for source, target in param_map.items():
            if next_hop.db_info[source]:
                server_params[target] = next_hop.db_info[source]
        return server_params

    def send_message_to_cell(self, context, cell_info, dest_host, message,
            fanout=False, topic=None):
        server_params = self._get_server_params_for_cell(cell_info)
        if topic is None:
            topic = FLAGS.cells_topic
        if fanout:
            rpc.fanout_cast_to_server(context, server_params, topic,
                    message)
            return
        if dest_host:
            topic += '.%s' % dest_host
        rpc.cast_to_server(context, server_params, topic, message)
