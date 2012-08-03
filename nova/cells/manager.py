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
Cells Service Manager
"""

import copy
import datetime
import random
import sys
import time
import traceback

import eventlet
from eventlet import greenthread
from eventlet import queue

from nova.cells import rpcapi as cells_rpcapi
from nova.cells import utils as cells_utils
from nova import compute
from nova import context
from nova import db
from nova import exception
from nova import flags
from nova import manager
from nova import network
from nova.openstack.common import cfg
from nova.openstack.common import excutils
from nova.openstack.common import importutils
from nova.openstack.common import log as logging
from nova.openstack.common import rpc
from nova.openstack.common.rpc import common as rpc_common
from nova.openstack.common import timeutils
from nova import utils
from nova import volume

flag_opts = [
        cfg.StrOpt('scheduler',
                default='nova.cells.scheduler.CellsScheduler',
                help='Cells scheduler to use'),
        cfg.IntOpt('db_check_interval',
                default=60,
                help='Seconds between getting fresh cell info from db.'),
        cfg.IntOpt('call_timeout',
                default=60,
                help='Seconds to wait for response from a call to a cell.'),
        cfg.IntOpt('max_broadcast_hop_count',
                default=10,
                help='Maximum number of hops for a broadcast message.'),
        cfg.IntOpt("instance_update_interval",
                default=60,
                help="Number of seconds between cell instance updates"),
        cfg.IntOpt("instance_updated_at_threshold",
                default=3600,
                help="Number of seconds after an instance was updated "
                        "or deleted to continue to update cells"),
        cfg.IntOpt("instance_update_num_instances",
                default=1,
                help="Number of instances to update per periodic task run")
]


LOG = logging.getLogger('nova.cells.manager')

flags.DECLARE('cells', 'nova.cells.opts')

FLAGS = flags.FLAGS
FLAGS.register_opts(flag_opts, group='cells')


class CellInfo(object):
    """Holds information ior a particular cell."""
    def __init__(self, cell_name, is_me=False):
        self.name = cell_name
        self.is_me = is_me
        self.last_seen = datetime.datetime.min
        self.capabilities = {}
        self.capacities = {}
        self.db_info = {}

    def update_db_info(self, cell_db_info):
        """Update cell credentials from db"""
        self.db_info = dict(
                [(k, v) for k, v in cell_db_info.iteritems()
                        if k != 'name'])

    def update_capabilities(self, cell_metadata):
        """Update cell capabilities for a cell."""
        self.last_seen = timeutils.utcnow()
        self.capabilities = cell_metadata

    def update_capacities(self, capacities):
        """Update capacity information for a cell."""
        self.last_seen = timeutils.utcnow()
        self.capacities = capacities

    def get_cell_info(self):
        """Return subset of cell information for OS API use.
        """
        db_fields_to_return = ['id', 'is_parent', 'weight_scale',
                'weight_offset', 'username', 'rpc_host', 'rpc_port']
        cell_info = dict(name=self.name, capabilities=self.capabilities)
        if self.db_info:
            for field in db_fields_to_return:
                cell_info[field] = self.db_info[field]
        return cell_info


class CellsBWUpdateManager(manager.Manager):
    """Cells RPC consumer manager for BW updates."""

    # NOTE(belliott) Should probably get bumped to 2.0 to be consistent
    # with other nova services.  Leaving at 1.0 for the moment to avoid
    # version mismatch errors due to nova-api potentially getting
    # updated before nova-cells.  After the versioned RPC API gets some
    # testing, this should be addressed
    RPC_API_VERSION = '1.0'

    def __init__(self, cells_manager):
        self.cells_manager = cells_manager

    def __getattr__(self, key):
        return getattr(self.cells_manager, key)


class CellsReplyManager(manager.Manager):
    """Cells RPC consumer manager for replies."""

    # NOTE(belliott) Should probably get bumped to 2.0 to be consistent
    # with other nova services.  Leaving at 1.0 for the moment to avoid
    # version mismatch errors due to nova-api potentially getting
    # updated before nova-cells.  After the versioned RPC API gets some
    # testing, this should be addressed
    RPC_API_VERSION = '1.0'

    def __init__(self, cells_manager):
        self.cells_manager = cells_manager

    def __getattr__(self, key):
        return getattr(self.cells_manager, key)


class CellsManager(manager.Manager):
    """Handles cell communication."""

    # NOTE(belliott) Should probably get bumped to 2.0 to be consistent
    # with other nova services.  Leaving at 1.0 for the moment to avoid
    # version mismatch errors due to nova-api potentially getting
    # updated before nova-cells.  After the versioned RPC API gets some
    # testing, this should be addressed
    RPC_API_VERSION = '1.0'

    def __init__(self, cells_driver_cls=None, cells_scheduler_cls=None,
            cell_info_cls=None, *args, **kwargs):
        super(CellsManager, self).__init__(*args, **kwargs)
        self.api_map = {'compute': compute.API(),
                        'network': network.API(),
                        'volume': volume.API()}

        if not cells_scheduler_cls:
            cells_scheduler_cls = importutils.import_class(
                    FLAGS.cells.scheduler)
        if not cell_info_cls:
            cell_info_cls = CellInfo

        self.cells_rpcapi = cells_rpcapi.CellsAPI(cells_driver_cls)
        self.bw_updates_topic = FLAGS.cells.topic + '.bw_updates'
        self.replies_topic = FLAGS.cells.topic + '.replies'
        self.scheduler = cells_scheduler_cls(self)
        self.cell_info_cls = cell_info_cls
        self.my_cell_info = cell_info_cls(FLAGS.cells.name, is_me=True)
        my_cell_capabs = {}
        self.rpc_connections = []
        for cap in FLAGS.cells.capabilities:
            name, value = cap.split('=', 1)
            if ';' in value:
                values = set(value.split(';'))
            else:
                values = set([value])
            my_cell_capabs[name] = values
        self.my_cell_info.update_capabilities(my_cell_capabs)

        self.our_path = cells_utils.PATH_CELL_HOST_SEP.join(
                [self.my_cell_info.name, FLAGS.host])
        self.response_queues = {}
        self.parent_cells = {}
        self.child_cells = {}
        self.last_cell_db_check = datetime.datetime.min
        ctxt = context.get_admin_context()
        self._cell_db_sync(ctxt)
        self._update_our_capacity(ctxt)
        self.last_instance_heal_time = 0
        self.instances_to_heal = iter([])

        def _cells_manager_startup():
            # FIXME(comstud): When we start, we want to send out some
            # messages to let neighbor cells know we're alive and that
            # they should update our idea of capacity and capabilities.
            # Unfortunately if we do this too early, we've not created
            # our fanout queue yet.  So if a cell responds quickly,
            # we miss the update.  I think nova Service() should perhaps
            # create the rpc queues inside of __init__() and then start
            # consuming in Service.start().
            eventlet.sleep(2)
            if self.get_child_cells():
                self._ask_children_for_capabilities(ctxt)
                self._ask_children_for_capacities(ctxt)
            else:
                self._tell_parents_our_capabilities(ctxt)
                self._tell_parents_our_capacities(ctxt)

        greenthread.spawn_n(_cells_manager_startup)
        self._start_separate_consumer(CellsBWUpdateManager,
                self.bw_updates_topic)
        self._start_separate_consumer(CellsReplyManager,
                self.replies_topic, host_too=True)

    def _start_separate_consumer(self, manager_cls, topic, fanout=False,
            host_too=False):
        manager = manager_cls(self)
        rpc_dispatcher = manager.create_rpc_dispatcher()
        conn = rpc.create_connection(new=True)
        conn.create_consumer(topic, rpc_dispatcher, fanout=fanout)
        if host_too:
            topic += '.' + FLAGS.host
            conn.create_consumer(topic, rpc_dispatcher, fanout=fanout)
        self.rpc_connections.append(conn)
        conn.consume_in_thread()

    def __getattr__(self, key):
        """Makes all scheduler_ methods pass through to scheduler"""
        if key.startswith("schedule_"):
            try:
                return getattr(self.scheduler, key)
            except AttributeError, e:
                with utils.save_and_reraise_exception():
                    LOG.error(_("Cells scheduler has no method '%s'") % key)
        raise AttributeError(_("Cells manager has no attribute '%s'") %
                key)

    def get_cell_info(self, context):
        """Return cell information for all cells."""
        cell_list = [cell.get_cell_info()
                for cell in self.child_cells.itervalues()]
        cell_list.extend([cell.get_cell_info()
                for cell in self.parent_cells.itervalues()])
        return cell_list

    def _cell_get_all(self, context):
        """Get all cells from the DB.  Used to stub in tests."""
        return self.db.cell_get_all(context)

    def _refresh_cells_from_db(self, context):
        """Make our cell info map match the db."""
        # Add/update existing cells ...
        db_cells = self._cell_get_all(context)
        db_cells_dict = dict([(cell['name'], cell) for cell in db_cells])

        # Update current cells.  Delete ones that disappeared
        for cells_dict in (self.parent_cells, self.child_cells):
            for cell_name, cell_info in cells_dict.items():
                is_parent = cell_info.db_info['is_parent']
                db_dict = db_cells_dict.get(cell_name)
                if db_dict and is_parent == db_dict['is_parent']:
                    cell_info.update_db_info(db_dict)
                else:
                    del cells_dict[cell_name]

        # Add new cells
        for cell_name, db_info in db_cells_dict.items():
            if db_info['is_parent']:
                cells_dict = self.parent_cells
            else:
                cells_dict = self.child_cells
            if cell_name not in cells_dict:
                cells_dict[cell_name] = self.cell_info_cls(cell_name)
                cells_dict[cell_name].update_db_info(db_info)

    @manager.periodic_task
    def _cell_db_sync(self, context):
        """Update status for all cells.  This should be called
        periodically to refresh the cell states.
        """
        diff = timeutils.utcnow() - self.last_cell_db_check
        if diff.seconds >= FLAGS.cells.db_check_interval:
            LOG.debug(_("Updating cell cache from db."))
            self.last_cell_db_check = timeutils.utcnow()
            self._refresh_cells_from_db(context)

    @manager.periodic_task
    def _update_our_capacity(self, context):
        instance_types = self.db.instance_type_get_all(context)
        compute_nodes = self.db.compute_node_get_all(context)
        compute_hosts = {}
        for compute in compute_nodes:
            service = compute['service']
            if not service or service['disabled']:
                continue
            host = service['host']
            compute_hosts[host] = {
                    'free_ram_mb': compute['memory_mb'],
                    'free_disk_mb': compute['local_gb'] * 1024}
        if not compute_hosts:
            self.my_cell_info.update_capacities({})
            return
        instances = db.instance_get_all(context)
        for instance in instances:
            host = instance['host']
            if not host:
                continue
            compute_host = compute_hosts.get(host)
            if not compute_host:
                continue
            compute_host['free_ram_mb'] -= instance['memory_mb']
            disk_gb = instance['root_gb'] + instance['ephemeral_gb']
            compute_host['free_disk_mb'] -= disk_gb * 1024
        ram_mb_free_units = {}
        disk_mb_free_units = {}
        total_ram_mb_free = 0
        total_disk_mb_free = 0
        for compute_values in compute_hosts.values():
            total_ram_mb_free += compute_values['free_ram_mb']
            total_disk_mb_free += compute_values['free_disk_mb']
            for instance_type in instance_types:
                memory_mb = instance_type['memory_mb']
                disk_mb = (instance_type['root_gb'] +
                        instance_type['ephemeral_gb']) * 1024
                ram_mb_free_units.setdefault(str(memory_mb), 0)
                disk_mb_free_units.setdefault(str(disk_mb), 0)
                ram_free_units = max(0,
                        int(compute_values['free_ram_mb'] /
                                (memory_mb or 1)))
                disk_free_units = max(0,
                        int(compute_values['free_disk_mb'] /
                                (disk_mb or 1)))
                ram_mb_free_units[str(memory_mb)] += ram_free_units
                disk_mb_free_units[str(disk_mb)] += disk_free_units
        capacities = {'ram_free': {'total_mb': total_ram_mb_free,
                                   'units_by_mb': ram_mb_free_units},
                      'disk_free': {'total_mb': total_disk_mb_free,
                                    'units_by_mb': disk_mb_free_units}}
        self.my_cell_info.update_capacities(capacities)

    @manager.periodic_task
    def _update_our_parents(self, context):
        """Update our parent cells with our capabilities and capacity
        if we're at the bottom of the tree.
        """
        self._tell_parents_our_capabilities(context)
        self._tell_parents_our_capacities(context)

    def _get_our_capabilities(self, include_children=True):
        capabs = copy.deepcopy(self.my_cell_info.capabilities)
        if include_children:
            for cell in self.get_child_cells():
                for capab_name, values in cell.capabilities.items():
                    if capab_name not in capabs:
                        capabs[capab_name] = set([])
                    capabs[capab_name] |= values
        return capabs

    def _tell_parents_our_capabilities(self, context):
        """Send our capabilities to parent cells."""
        capabs = self._get_our_capabilities()
        LOG.debug(_("Updating parents with our capabilities: %(capabs)s"),
                locals())
        # We have to turn the sets into lists so they can potentially
        # be json encoded when the raw message is sent.
        for key, values in capabs.items():
            capabs[key] = list(values)
        self.cells_rpcapi.update_capabilities(context, self.my_cell_info.name,
                capabs, self.get_parent_cells())

    def _add_to_dict(self, target, src):
        for key, value in src.items():
            if isinstance(value, dict):
                target.setdefault(key, {})
                self._add_to_dict(target[key], value)
                continue
            target.setdefault(key, 0)
            target[key] += value

    def _get_our_capacities(self, include_children=True):
        capacities = copy.deepcopy(self.my_cell_info.capacities)
        if include_children:
            for cell in self.get_child_cells():
                self._add_to_dict(capacities, cell.capacities)
        return capacities

    def _tell_parents_our_capacities(self, context):
        """Send our capacities to parent cells."""
        capacities = self._get_our_capacities()
        LOG.debug(_("Updating parents with our capacities: %(capacities)s"),
                locals())
        self.cells_rpcapi.update_capacities(context, self.my_cell_info.name,
                capacities, self.get_parent_cells())

    def update_capabilities(self, context, cell_name, capabilities):
        """A child cell told us about their capabilities."""
        child_cell = self.child_cells.get(cell_name)
        if not child_cell:
            LOG.error(_("Received capability update from unknown "
                "child cell '%(cell_name)s'"), locals())
            return
        LOG.debug(_("Received capabilities from child cell "
                "%(cell_name)s: %(capabilities)s"), locals())
        # Turn capabilities values into sets from lists
        capabs = {}
        for capab_name, values in capabilities.items():
            capabs[capab_name] = set(values)
        child_cell.update_capabilities(capabs)
        # Go ahead and update our parents now that a child updated us
        self._tell_parents_our_capabilities(context)

    def update_capacities(self, context, cell_name, capacities):
        """A child cell told us about their capacity."""
        child_cell = self.child_cells.get(cell_name)
        if not child_cell:
            LOG.error(_("Received capacity update from unknown "
                "child cell '%(cell_name)s'"), locals())
            return
        LOG.debug(_("Received capacities from child cell "
                "%(cell_name)s: %(capacities)s"), locals())
        child_cell.update_capacities(capacities)
        self._tell_parents_our_capacities(context)

    def _ask_children_for_capabilities(self, context):
        """Tell child cells to send us capabilities.  We do this on
        startup of cells service.
        """
        child_cells = self.get_child_cells()
        self.cells_rpcapi.request_capabilities(context, self.our_path,
                                               child_cells)

    def _ask_children_for_capacities(self, context):
        """Tell child cells to send us capacities.  We do this on
        startup of cells service.
        """
        child_cells = self.get_child_cells()
        self.cells_rpcapi.request_capacities(context, self.our_path,
                                             child_cells)

    def get_child_cells(self):
        """Return list of child cell_infos."""
        return self.child_cells.values()

    def get_parent_cells(self):
        """Return list of parent cell_infos."""
        return self.parent_cells.values()

    def _process_message_for_me(self, context, message, **kwargs):
        """Process a message for our cell."""
        routing_path = kwargs.get('routing_path')
        method = message['method']
        args = message.get('args', {})
        fn = getattr(self, method)
        return fn(context, routing_path=routing_path, **args)

    def send_routing_message(self, context, dest_cell_name, message):
        """Send a routing message to a cell by name."""
        routing_path = self.our_path
        (next_hop, hop_host) = self._find_next_hop(dest_cell_name,
                routing_path, 'down')
        if next_hop.is_me:
            return self._process_message_for_me(context, message['message'])

        self.cells_rpcapi.send_message_to_cell(context, next_hop, None,
                                               message)

    def _path_is_us(self, routing_path):
        return (cells_utils.path_without_hosts(routing_path) ==
                self.my_cell_info.name)

    def _find_next_hop(self, dest_cell_name, routing_path, direction):
        """Return the (cell, host) for the next routing hop.  The next hop
        might be ourselves if this is where the message is supposed to go.
        """
        (next_hop_name, host) = cells_utils.cell_name_for_next_hop(
                dest_cell_name, routing_path)
        if not next_hop_name:
            return (self.my_cell_info, None)
        if direction == 'up':
            next_hop = self.parent_cells.get(next_hop_name)
        else:
            next_hop = self.child_cells.get(next_hop_name)
        if not next_hop:
            cell_type = 'parent' if direction == 'up' else 'child'
            reason = _("Unknown %(cell_type)s when routing to "
                    "%(dest_cell_name)s") % locals()
            raise exception.CellRoutingInconsistency(reason=reason)
        return (next_hop, host)

    def _route_response(self, context, response_uuid, routing_path,
            direction, result, failure=False):
        """Send a response back to the top of the current routing_path."""

        dest_cell = cells_utils.response_cell_name_from_path(routing_path)
        # Routing path for the response starts with us
        resp_routing_path = self.our_path
        (next_hop, hop_host) = self._find_next_hop(dest_cell,
                resp_routing_path, direction)
        result_info = {'result': result, 'failure': failure}
        if next_hop.is_me:
            # Response was for me!  Just call the method directly.
            self.send_response(context, response_uuid, result_info)
            return

        self.cells_rpcapi.forward_response(context, dest_cell, direction,
                resp_routing_path, response_uuid, result_info, next_hop,
                hop_host, self.replies_topic)

    def send_response(self, context, response_uuid, result_info, **kwargs):
        """This method is called when a another cell has responded to a
        request initiated from this cell.  (The response was encapsulated
        inside a routing message, so this method is only called in the cell
        where the request was initiated.
        """
        # Find the response queue the caller is waiting on
        response_queue = self.response_queues.get(response_uuid)
        # Just drop the response if we don't have a place to put it
        # anymore.  Likely we were restarted..
        if response_queue:
            if result_info.get('failure', False):
                result = rpc_common.RemoteError(*result_info['result'])
            else:
                result = result_info['result']
            response_queue.put(result)

    def _process_routing_message(self, context, from_our_cell,
            dest_cell_name, routing_path, direction, message,
            resp_queue, resp_uuid, resp_direction, **kwargs):
        """Process a routing message and stuff reply into response
        queue if needed.
        Callers should catch exceptions and send an error response if
        a response was desired.
        """
        (next_hop, hop_host) = self._find_next_hop(dest_cell_name,
                routing_path, direction)
        if next_hop.is_me:
            result = self._process_message_for_me(context, message,
                    routing_path=routing_path, **kwargs)
            # If a response is desired and the request did not
            # originate from our cell, send a response to the
            # calling cell.
            if resp_queue:
                resp_queue.put(result)
            if not resp_uuid:
                return
            self._route_response(context, resp_uuid, routing_path,
                resp_direction, result, failure=False)
        else:
            # Forward the message to the next hop
            if resp_uuid:
                topic = self.replies_topic
            else:
                topic = None

            self.cells_rpcapi.forward_routing_message(context, dest_cell_name,
                    direction, routing_path, resp_uuid, message['method'],
                    message['args'], next_hop, hop_host, topic)

    @cells_utils.update_routing_path
    def route_message(self, context, dest_cell_name, routing_path,
            direction, message, response_uuid=None, need_response=None,
            **kwargs):
        """Route a message to the destination cell."""
        from_our_cell = self._path_is_us(routing_path)
        resp_direction = 'up' if direction == 'down' else 'down'
        resp_queue = None

        if need_response and from_our_cell:
            # Set up a queue for the response so we can wait for a cell
            # to respond.  Cells will respond by routing a message
            # back to our cells.host queue, calling back into
            # 'self.send_response'.  There, the response is put into
            # this queue.
            resp_queue = queue.Queue()
            response_uuid = str(utils.gen_uuid())
            self.response_queues[response_uuid] = resp_queue

        try:
            self._process_routing_message(context, from_our_cell,
                    dest_cell_name, routing_path, direction,
                    message, resp_queue, response_uuid, resp_direction,
                    **kwargs)
        except Exception, e:
            if resp_queue:
                # Local response needed.  Just delete the queue and
                # re-raise
                del self.response_queues[response_uuid]
                raise
            LOG.exception(_("Received exception during cell routing: "
                    "%(e)s") % locals())
            if response_uuid:
                # Calling cell wanted a response, so send them an error.
                exc = sys.exc_info()
                result = (exc[0].__name__, str(exc[1]),
                        traceback.format_exception(*exc))
                LOG.debug(_("Sending %(result)s back to %(routing_path)s") %
                        locals())
                self._route_response(context, response_uuid,
                        routing_path, resp_direction, result,
                        failure=True)
            return

        if resp_queue:
            wait_time = FLAGS.cells.call_timeout
            try:
                result = resp_queue.get(timeout=wait_time)
            except queue.Empty:
                del self.response_queues[response_uuid]
                raise exception.CellTimeout()

            del self.response_queues[response_uuid]
            if isinstance(result, BaseException):
                raise result
            return result

    @cells_utils.update_routing_path
    def broadcast_call(self, context, routing_path, direction, message,
                       response_uuid=None, **kwargs):
        """Route messages to the destination cells."""
        if not response_uuid:
            response_uuid = str(utils.gen_uuid())

        self.response_queues[response_uuid] = queue.Queue()
        response_queue = self.response_queues[response_uuid]
        response_direction = 'down' if direction == 'up' else 'up'

        if direction == 'down':
            dest_cells = self.get_child_cells()
        else:
            dest_cells = self.get_parent_cells()

        if dest_cells:
            broadcast_msg = cells_utils.form_broadcast_call_message(direction,
                    message['method'], message['args'], response_uuid,
                    routing_path)
            self.cells_rpcapi.send_message_to_cells(context, dest_cells,
                                                    broadcast_msg)

        our_response = self._process_message_for_me(context, message,
                routing_path=routing_path, **kwargs)

        our_cell_name = self.my_cell_info.name
        responses = [(our_response, our_cell_name)]
        wait_time = FLAGS.cell_call_timeout

        for cell in dest_cells:
            try:
                responses.extend(response_queue.get(timeout=wait_time))
            except queue.Empty:
                del self.response_queues[response_uuid]
                raise exception.CellTimeout()

        del self.response_queues[response_uuid]

        if not self._path_is_us(routing_path):
            self._route_response(context, response_uuid, routing_path,
                    response_direction, responses, failure=False)

        return responses

    @cells_utils.update_routing_path
    def broadcast_message(self, context, direction, message, hopcount,
            fanout, routing_path, **kwargs):
        """Broadcast a message to all parent or child cells and also
        process it locally.
        """

        max_hops = FLAGS.cells.max_broadcast_hop_count
        if hopcount > max_hops:
            detail = _("Broadcast message '%(message)s' reached max hop "
                    "count: %(hopcount)s > %(max_hops)s") % locals()
            LOG.error(detail)
            return

        hopcount += 1
        bcast_msg = cells_utils.form_broadcast_message(direction,
                message['method'], message['args'],
                routing_path=routing_path, hopcount=hopcount, fanout=fanout)
        topic = None
        if message['method'] == 'call_dbapi_method':
            db_method_info = message.get('args', {}).get('db_method_info', {})
            if 'bw_usage' in db_method_info.get('method', ''):
                topic = self.bw_updates_topic
        # Forward request on to other cells
        if direction == 'up':
            cells = self.get_parent_cells()
        else:
            cells = self.get_child_cells()
        self.cells_rpcapi.send_message_to_cells(context, cells, bcast_msg,
                topic=topic)
        # Now let's process it.
        self._process_message_for_me(context, message,
                routing_path=routing_path, **kwargs)

    def run_service_api_method(self, context, service_name, method_info,
            is_broadcast=False, routing_path=None, **kwargs):
        """Caller wants us to call a method in a service API

        :param routing_path: the path to the cell that sent this message, or
                             None if it came straight from OSAPI service
        """
        api = self.api_map.get(service_name)
        if not api:
            detail = _("Unknown service API: %s") % service_name
            raise exception.CellServiceAPIMethodNotFound(detail=detail)
        method = method_info['method']
        fn = getattr(api, method, None)
        if not fn:
            detail = _("Unknown method '%(method)s' in %(service_name)s "
                    "API") % locals()
            raise exception.CellServiceAPIMethodNotFound(detail=detail)
        args = list(method_info['method_args'])

        def _broadcast_instance_destroy(instance_uuid):
            instance = {'uuid': instance_uuid}
            bcast_msg = cells_utils.form_instance_destroy_broadcast_message(
                    instance)
            self.broadcast_message(context, **bcast_msg['args'])

        # FIXME(comstud): Make more generic later.  Finish 'volume' code
        if service_name == 'compute':
            # 1st arg is instance_uuid that we need to turn into the
            # instance object.
            instance_uuid = args[0]
            try:
                instance = db.instance_get_by_uuid(context, instance_uuid)
            except exception.InstanceNotFound:
                # Kick an instance_destroy update to the top if it's not
                # a broadcast message, since we must have gotten out of
                # sync..
                if is_broadcast:
                    LOG.info(_("Ignoring unknown instance in broadcast "
                        "message"), instance_uuid=instance_uuid)
                    return
                with excutils.save_and_reraise_exception():
                    _broadcast_instance_destroy(instance_uuid)
            args[0] = instance
        return fn(context, *args, **method_info['method_kwargs'])

    def _get_instances_to_sync(self, context, updated_since=None,
            project_id=None, deleted=True, shuffle=False,
            uuids_only=False):
        """Return a generator that will return a list of active and
        deleted instances to sync with parent cells.  The list may
        optionally be shuffled for periodic updates so that multiple
        cells services aren't self-healing the same instances in nearly
        lockstep.
        """
        filters = {}
        if updated_since is not None:
            filters['changes-since'] = updated_since
        if project_id is not None:
            filters['project_id'] = project_id
        if not deleted:
            filters['deleted'] = False
        # Active instances first.
        instances = self.db.instance_get_all_by_filters(
                context, filters, 'deleted', 'asc')
        if shuffle:
            random.shuffle(instances)
        for instance in instances:
            if uuids_only:
                yield instance['uuid']
            else:
                yield instance

    @manager.periodic_task
    def _heal_instances(self, context):
        """Periodic task to send updates for a number of instances to
        parent cells.
        """

        interval = FLAGS.cells.instance_update_interval
        if not interval:
            return
        curr_time = time.time()
        if self.last_instance_heal_time + interval > curr_time:
            return
        self.last_instance_heal_time = curr_time

        info = {'updated_list': False}

        def _next_instance():
            try:
                instance = self.instances_to_heal.next()
            except StopIteration:
                if info['updated_list']:
                    return
                threshold = FLAGS.cells.instance_updated_at_threshold
                updated_since = None
                if threshold > 0:
                    updated_since = timeutils.utcnow() - datetime.timedelta(
                            seconds=threshold)
                self.instances_to_heal = self._get_instances_to_sync(
                        context, updated_since=updated_since, shuffle=True,
                        uuids_only=True)
                info['updated_list'] = True
                try:
                    instance = self.instances_to_heal.next()
                except StopIteration:
                    return
            return instance

        rd_context = context.elevated(read_deleted='yes')

        for i in xrange(FLAGS.cells.instance_update_num_instances):
            while True:
                # Yield to other greenthreads
                time.sleep(0)
                instance_uuid = _next_instance()
                if not instance_uuid:
                    return
                try:
                    instance = self.db.instance_get_by_uuid(rd_context,
                            instance_uuid)
                except exception.InstanceNotFound:
                    continue
                self._sync_instance(context, instance)
                break

    def _sync_instance(self, context, instance):
        """Broadcast an instance_update or instance_destroy message up to
        parent cells.
        """
        if instance['deleted']:
            msg = cells_utils.form_instance_destroy_broadcast_message(
                instance, routing_path=self.our_path, hopcount=1)
        else:
            msg = cells_utils.form_instance_update_broadcast_message(
                instance, routing_path=self.our_path, hopcount=1)
        self.cells_rpcapi.send_message_to_cells(context,
                self.get_parent_cells(), msg)

    def sync_instances(self, context, routing_path, project_id=None,
            updated_since=None, deleted=False, **kwargs):
        """Force a sync of all instances, potentially by project_id,
        and potentially since a certain date/time."""
        projid_str = project_id is None and "<all>" or project_id
        since_str = updated_since is None and "<all>" or updated_since
        LOG.info(_("Forcing a sync of instances, project_id="
            "%(projid_str)s, updated_since=%(since_str)s"), locals())
        if updated_since is not None:
            updated_since = timeutils.parse_isotime(updated_since)
        instances = self._get_instances_to_sync(context,
                updated_since=updated_since, project_id=project_id,
                deleted=deleted)
        for instance in instances:
            self._sync_instance(context, instance)

    def instance_update(self, context, instance_info, routing_path,
            **kwargs):
        """Update an instance in the DB if we're a top level cell."""
        if self.get_parent_cells() or self._path_is_us(routing_path):
            # Only update the DB if we're at the very top and the
            # call didn't originate from ourselves
            return
        instance_uuid = instance_info['uuid']
        if routing_path:
            instance_info['cell_name'] = cells_utils.reverse_path(
                    cells_utils.path_without_hosts(routing_path))
        else:
            LOG.error(_("No routing_path for instance_update of "
                    "%(instance_uuid)s") % locals())
        LOG.debug(_("Got update for instance %(instance_uuid)s: "
                "%(instance_info)s") % locals())
        info_cache = instance_info.pop('info_cache', None)
        # It's possible due to some weird condition that the instance
        # was already set as deleted... so we'll attempt to update
        # it with permissions that allows us to read deleted.
        with utils.temporary_mutation(context, read_deleted="yes"):
            try:
                self.db.instance_update(context, instance_uuid,
                        instance_info, update_cells=False)
            except exception.NotFound:
                # FIXME(comstud): Strange.  Need to handle quotas here,
                # if we actually want this code to remain..
                self.db.instance_create(context, instance_info)
        if info_cache:
            self.db.instance_info_cache_update(context, instance_uuid,
                    info_cache)

    def instance_destroy(self, context, instance_info, routing_path=None,
            **kwargs):
        """Destroy an instance from the DB if we're a top level cell."""
        if self.get_parent_cells() or self._path_is_us(routing_path):
            # Only update the DB if we're at the very top and the
            # call didn't originate from ourselves
            return
        instance_uuid = instance_info['uuid']
        LOG.debug(_("Got update to delete instance %(instance_uuid)s") %
                locals())
        try:
            self.db.instance_destroy(context, instance_uuid,
                    update_cells=False)
        except exception.InstanceNotFound:
            pass

    def call_dbapi_method(self, context, db_method_info, routing_path,
            **kwargs):
        """Call a DB API method if we're a top level cell."""
        if self.get_parent_cells() or self._path_is_us(routing_path):
            # Only update the DB if we're at the very top and the
            # call didn't originate from ourselves
            return
        method = db_method_info['method']
        args = db_method_info['method_args']
        kwargs = db_method_info['method_kwargs']
        LOG.debug(_("Got message to call DB API method %(method)s "
                "with args %(args)s and kwargs %(kwargs)s"),
                locals())
        db_method = getattr(self.db, method)
        try:
            db_method(context, *args, update_cells=False, **kwargs)
        except exception.NotFound:
            pass

    def announce_capabilities(self, context, routing_path, **kwargs):
        """A parent cell has told us to send our capabilities."""
        self._tell_parents_our_capabilities(context)

    def announce_capacities(self, context, routing_path, **kwargs):
        """A parent cell has told us to send our capacity."""
        self._tell_parents_our_capacities(context)

    def get_service(self, context, routing_path, **kwargs):
        service_id = kwargs.get("service_id")
        try:
            return db.service_get(context, service_id)
        except exception.ServiceNotFound:
            return None

    def list_services(self, context, routing_path, include_disabled=True):
        """
        :param include_disabled: if True (the default), includes disabled
                                 services in the Result
        """
        if include_disabled:
            return db.service_get_all(context)
        else:
            return db.service_get_all(context, disabled=False)

    def task_logs(self, context, routing_path, **kwargs):
        task_name = kwargs.get('task_name')
        begin = kwargs.get('begin')
        end = kwargs.get('end')
        return db.task_log_get_all(context, task_name, begin, end)

    def list_compute_nodes(self, context, routing_path,
                           hypervisor_match=None):
        if hypervisor_match:
            return db.compute_node_search_by_hypervisor(context,
                                                        hypervisor_match)
        else:
            return db.compute_node_get_all(context)

    def compute_node_get(self, context, routing_path, **kwargs):
        compute_id = kwargs.get("compute_id")
        try:
            return db.compute_node_get(context, compute_id)
        except exception.ComputeHostNotFound:
            return None

    def compute_node_stats(self, context, routing_path):
        return [db.compute_node_statistics(context)]
