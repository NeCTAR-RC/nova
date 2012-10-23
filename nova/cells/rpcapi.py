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
Handles all requests relating to cells.
"""

from nova.cells import utils as cells_utils
from nova import flags
from nova.openstack.common import cfg
from nova.openstack.common import importutils
from nova.openstack.common import log as logging
from nova.openstack.common import rpc
import nova.openstack.common.rpc.proxy

LOG = logging.getLogger(__name__)

driver_opt = cfg.StrOpt('driver',
                        default='nova.cells.rpc_driver.CellsRPCDriver',
                        help='Cells driver to use')

flags.DECLARE('cells', 'nova.cells.opts')

FLAGS = flags.FLAGS
FLAGS.register_opt(driver_opt, group='cells')


class CellsAPI(nova.openstack.common.rpc.proxy.RpcProxy):
    '''Cells client-side RPC API

    API version history:

        1.0 - Initial version.
    '''

    # NOTE(belliott) Should probably get bumped to 2.0 to be consistent
    # with other nova services.  Leaving at 1.0 for the moment to avoid
    # version mismatch errors due to nova-api potentially getting
    # updated before nova-cells.  After the versioned RPC API gets some
    # testing, this should be addressed.
    BASE_RPC_API_VERSION = '1.0'

    def __init__(self, cells_driver_cls=None):
        super(CellsAPI, self).__init__(topic=FLAGS.cells.topic,
                default_version=self.BASE_RPC_API_VERSION)

        if not cells_driver_cls:
            cells_driver_cls = importutils.import_class(FLAGS.cells.driver)
        self.driver = cells_driver_cls(self)

    def cell_call(self, context, cell_name, method, **kwargs):
        """Route a call to a specific cell."""
        routing_message = cells_utils.form_routing_message(cell_name,
                'down', method, kwargs, need_response=True)
        return self.call(context, routing_message)

    def cell_cast(self, context, cell_name, method, **kwargs):
        """Route a cast to a specific cell."""
        routing_message = cells_utils.form_routing_message(cell_name,
                'down', method, kwargs)
        self.cast(context, routing_message)

    def cell_broadcast(self, context, direction, method, **kwargs):
        """Route a cast to a specific cell."""
        bcast_message = cells_utils.form_broadcast_message(direction, method,
                kwargs)
        self.cast(context, bcast_message)

    def cell_broadcast_call(self, context, direction, method, **kwargs):
        bcast_message = cells_utils.form_broadcast_call_message(direction,
                method, kwargs)
        return self.call(context, bcast_message)

    def broadcast_service_api_method(self, context, service_name, method,
            *args, **kwargs):
        """Encapsulate a call to a service API within a broadcast message"""

        method_info = {'method': method,
                       'method_args': args,
                       'method_kwargs': kwargs}
        self.cell_broadcast(context, 'down', 'run_service_api_method',
                service_name=service_name, method_info=method_info,
                is_broadcast=True)

    def cast_service_api_method(self, context, cell_name, service_name, method,
            *args, **kwargs):
        """Encapsulate a call to a service API within a routing call"""

        method_info = {'method': method,
                       'method_args': args,
                       'method_kwargs': kwargs}
        self.cell_cast(context, cell_name, 'run_service_api_method',
                service_name=service_name, method_info=method_info,
                is_broadcast=False)

    def call_service_api_method(self, context, cell_name, service_name, method,
            *args, **kwargs):
        """Encapsulate a call to a service API within a routing call"""

        method_info = {'method': method,
                       'method_args': args,
                       'method_kwargs': kwargs}
        return self.cell_call(context, cell_name, 'run_service_api_method',
                service_name=service_name, method_info=method_info,
                is_broadcast=False)

    def schedule_run_instance(self, context, **kwargs):
        """Schedule a new instance for creation."""
        message = {'method': 'schedule_run_instance',
                   'args': kwargs}
        self.cast(context, message)

    def call_dbapi_method(self, context, method, args, kwargs=None,
            sub_topic=None, direction='up'):
        """Broadcast message up, saying to call a DB API method."""
        if kwargs is None:
            kwargs = {}
        db_method_info = {'method': method,
                          'method_args': args,
                          'method_kwargs': kwargs}
        bcast_message = cells_utils.form_broadcast_message(direction,
                'call_dbapi_method_%s' % direction,
                {'db_method_info': db_method_info})
        topic = FLAGS.cells.topic
        if sub_topic:
            topic += '.' + sub_topic
        self.cast(context, bcast_message, topic)

    def instance_update(self, context, instance):
        """Broadcast upwards that an instance was updated."""
        if not FLAGS.cells.enable:
            return
        bcast_message = cells_utils.form_instance_update_broadcast_message(
                instance)
        self.cast(context, bcast_message)

    def instance_destroy(self, context, instance):
        """Broadcast upwards that an instance was destroyed."""
        if not FLAGS.cells.enable:
            return
        bcast_message = cells_utils.form_instance_destroy_broadcast_message(
                instance)
        self.cast(context, bcast_message)

    def instance_fault_create(self, context, instance_fault):
        """Broadcast upwards that an instance fault was created."""
        if not FLAGS.cells.enable:
            return
        instance_fault = dict(instance_fault.iteritems())
        items_to_remove = ['id']
        for key in items_to_remove:
            instance_fault.pop(key, None)
        self.call_dbapi_method(context, 'instance_fault_create',
                (instance_fault, ))

    def bw_usage_update(self, context, *args, **kwargs):
        """Broadcast upwards that bw_usage was updated."""
        if not FLAGS.cells.enable:
            return
        self.call_dbapi_method(context, 'bw_usage_update',
                args, kwargs=kwargs, sub_topic='bw_updates')

    def instance_metadata_update(self, context, *args, **kwargs):
        """Broadcast upwards that bw_usage was updated."""
        if not FLAGS.cells.enable:
            return
        self.call_dbapi_method(context, 'instance_metadata_update', args,
                kwargs=kwargs)

    def get_all_cell_info(self, context):
        """Get the list of cells and their information from the manager."""
        msg = {'method': 'get_cell_info',
               'args': {}}
        return self.call(context, msg)

    def get_subcell_names(self, context):
        """Get the name of each subcell (not just immediate children)."""
        msg = {'method': 'get_subcell_names',
               'args': {}}
        return self.call(context, msg)

    def sync_instances(self, context, project_id=None, updated_since=None,
            deleted=False):
        """Broadcast message down to tell cells to sync instance data."""
        if not FLAGS.cells.enable:
            return
        bcast_message = cells_utils.form_broadcast_message('down',
                'sync_instances', {'project_id': project_id,
                                   'updated_since': updated_since,
                                   'deleted': deleted})
        self.cast(context, bcast_message)

    def request_capacities(self, context, src_routing_path, child_cells):
        """Request capacities from child cells."""
        bcast_msg = cells_utils.form_broadcast_message('down',
                'announce_capacities', {}, routing_path=src_routing_path,
                hopcount=1)
        self.send_message_to_cells(context, child_cells, bcast_msg)

    def request_capabilities(self, context, src_routing_path, child_cells):
        """Request capabilities from child cells."""
        bcast_msg = cells_utils.form_broadcast_message('down',
                'announce_capabilities', {}, routing_path=src_routing_path,
                hopcount=1)
        self.send_message_to_cells(context, child_cells, bcast_msg)

    def update_capacities(self, context, cell_name, capacities, parent_cells):
        """Send our capacity info to parent cells."""
        msg = self.make_msg('update_capacities', cell_name=cell_name,
                capacities=capacities)
        self.send_message_to_cells(context, parent_cells, msg, fanout=True)

    def update_capabilities(self, context, cell_name, capabilities,
                            parent_cells):
        """Send our capabilities to parent cells."""
        msg = self.make_msg('update_capabilities', cell_name=cell_name,
                capabilities=capabilities)
        self.send_message_to_cells(context, parent_cells, msg, fanout=True)

    def send_message_to_cell(self, context, cell, message, dest_host=None,
                                 fanout=False, topic=None):
        """Send a message to a cell."""
        self.driver.send_message_to_cell(context, cell, dest_host, message,
                                         self, fanout=fanout, topic=topic)

    def send_message_to_cells(self, context, cells, msg, dest_host=None,
                        fanout=False, ignore_exceptions=True, topic=None):
        """Send a broadcast message to multiple cells."""
        for cell in cells:
            try:
                self.send_message_to_cell(context, cell, msg,
                        dest_host=dest_host, fanout=fanout, topic=topic)
            except Exception, e:
                if not ignore_exceptions:
                    raise
                cell_name = cell.name
                LOG.exception(_("Error sending broadcast to cell "
                                "'%(cell_name)s': %(e)s") % locals())

    def forward_routing_message(self, context, dest_cell, direction,
                      routing_path, response_uuid, method, method_kwargs,
                      next_hop, hop_host, topic):
        """Forward a routing message to the next hop along the route."""
        routing_message = cells_utils.form_routing_message(dest_cell,
                direction, method, method_kwargs, response_uuid=response_uuid,
                routing_path=routing_path)
        self.send_message_to_cell(context, next_hop, routing_message,
                dest_host=hop_host, topic=topic)

    def forward_response(self, context, dest_cell, direction,
                      resp_routing_path, response_uuid, result_info, next_hop,
                      hop_host, reply_topic):
        """Forward a reponse to the next hop along the route."""
        kwargs = {'response_uuid': response_uuid, 'result_info': result_info}
        routing_message = cells_utils.form_routing_message(dest_cell,
                direction, 'send_response', kwargs,
                routing_path=resp_routing_path)
        self.send_message_to_cell(context, next_hop, routing_message,
                dest_host=hop_host, topic=reply_topic)

    def broadcast_dbmethod_down(self, context, dbmethod, *args, **kwargs):
        """Broadcast db method downward"""
        if not FLAGS.cells.enable:
            return
        self.call_dbapi_method(context, dbmethod, args,
                               kwargs=kwargs, direction='down')
