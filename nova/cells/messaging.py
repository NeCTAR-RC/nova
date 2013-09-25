# Copyright (c) 2012 Rackspace Hosting
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
Cell messaging module.

This module defines the different message types that are passed between
cells and the methods that they can call when the target cell has been
reached.

The interface into this module is the MessageRunner class.
"""
import sys

from eventlet import queue
from oslo.config import cfg

from nova.cells import state as cells_state
from nova.cells import utils as cells_utils
from nova import compute
from nova.compute import rpcapi as compute_rpcapi
from nova.consoleauth import rpcapi as consoleauth_rpcapi
from nova import context
from nova import db
from nova.db import base
from nova import exception
from nova.openstack.common import excutils
from nova.openstack.common import importutils
from nova.openstack.common import jsonutils
from nova.openstack.common import log as logging
from nova.openstack.common import rpc
from nova.openstack.common.rpc import common as rpc_common
from nova.openstack.common import timeutils
from nova.openstack.common import uuidutils
from nova import utils
from nova import availability_zones

cell_messaging_opts = [
    cfg.IntOpt('max_hop_count',
            default=10,
            help='Maximum number of hops for cells routing.'),
    cfg.StrOpt('scheduler',
            default='nova.cells.scheduler.CellsScheduler',
            help='Cells scheduler to use')]

CONF = cfg.CONF
CONF.import_opt('name', 'nova.cells.opts', group='cells')
CONF.import_opt('call_timeout', 'nova.cells.opts', group='cells')
CONF.register_opts(cell_messaging_opts, group='cells')

LOG = logging.getLogger(__name__)

# Separator used between cell names for the 'full cell name' and routing
# path.
_PATH_CELL_SEP = cells_utils._PATH_CELL_SEP


def _reverse_path(path):
    """Reverse a path.  Used for sending responses upstream."""
    path_parts = path.split(_PATH_CELL_SEP)
    path_parts.reverse()
    return _PATH_CELL_SEP.join(path_parts)


def _response_cell_name_from_path(routing_path, neighbor_only=False):
    """Reverse the routing_path.  If we only want to send to our parent,
    set neighbor_only to True.
    """
    path = _reverse_path(routing_path)
    if not neighbor_only or len(path) == 1:
        return path
    return _PATH_CELL_SEP.join(path.split(_PATH_CELL_SEP)[:2])


#
# Message classes.
#


class _BaseMessage(object):
    """Base message class.  It defines data that is passed with every
    single message through every cell.

    Messages are JSON-ified before sending and turned back into a
    class instance when being received.

    Every message has a unique ID.  This is used to route responses
    back to callers.  In the future, this might be used to detect
    receiving the same message more than once.

    routing_path is updated on every hop through a cell.  The current
    cell name is appended to it (cells are separated by
    _PATH_CELL_SEP ('!')).  This is used to tell if we've reached the
    target cell and also to determine the source of a message for
    responses by reversing it.

    hop_count is incremented and compared against max_hop_count.  The
    only current usefulness of this is to break out of a routing loop
    if someone has a broken config.

    fanout means to send to all nova-cells services running in a cell.
    This is useful for capacity and capability broadcasting as well
    as making sure responses get back to the nova-cells service that
    is waiting.
    """

    # Override message_type in a subclass
    message_type = None

    base_attrs_to_json = ['message_type',
                          'ctxt',
                          'method_name',
                          'method_kwargs',
                          'direction',
                          'need_response',
                          'fanout',
                          'uuid',
                          'routing_path',
                          'hop_count',
                          'max_hop_count']

    def __init__(self, msg_runner, ctxt, method_name, method_kwargs,
            direction, need_response=False, fanout=False, uuid=None,
            routing_path=None, hop_count=0, max_hop_count=None,
            **kwargs):
        self.ctxt = ctxt
        self.resp_queue = None
        self.msg_runner = msg_runner
        self.state_manager = msg_runner.state_manager
        # Copy these.
        self.base_attrs_to_json = self.base_attrs_to_json[:]
        # Normally this would just be CONF.cells.name, but going through
        # the msg_runner allows us to stub it more easily.
        self.our_path_part = self.msg_runner.our_name
        self.uuid = uuid
        if self.uuid is None:
            self.uuid = uuidutils.generate_uuid()
        self.method_name = method_name
        self.method_kwargs = method_kwargs
        self.direction = direction
        self.need_response = need_response
        self.fanout = fanout
        self.routing_path = routing_path
        self.hop_count = hop_count
        if max_hop_count is None:
            max_hop_count = CONF.cells.max_hop_count
        self.max_hop_count = max_hop_count
        self.is_broadcast = False
        self._append_hop()
        # Each sub-class should set this when the message is inited
        self.next_hops = []
        self.resp_queue = None

    def __repr__(self):
        _dict = self._to_dict()
        _dict.pop('method_kwargs')
        return "<%s: %s>" % (self.__class__.__name__, _dict)

    def _append_hop(self):
        """Add our hop to the routing_path."""
        routing_path = (self.routing_path and
                self.routing_path + _PATH_CELL_SEP or '')
        self.routing_path = routing_path + self.our_path_part
        self.hop_count += 1

    def _at_max_hop_count(self, do_raise=True):
        """Check if we're at the max hop count.  If we are and do_raise is
        True, raise CellMaxHopCountReached.  If we are at the max and
        do_raise is False... return True, else False.
        """
        if self.hop_count >= self.max_hop_count:
            if do_raise:
                raise exception.CellMaxHopCountReached(
                        hop_count=self.hop_count)
            return True
        return False

    def _process_locally(self):
        """Its been determined that we should process this message in this
        cell.  Go through the MessageRunner to call the appropriate
        method for this message.  Catch the response and/or exception and
        encode it within a Response instance.  Return it so the caller
        can potentially return it to another cell... or return it to
        a caller waiting in this cell.
        """
        try:
            resp_value = self.msg_runner._process_message_locally(self)
            failure = False
        except Exception as exc:
            resp_value = sys.exc_info()
            failure = True
            LOG.exception(_("Error processing message locally: %(exc)s"),
                          locals())
        return Response(self.routing_path, resp_value, failure)

    def _setup_response_queue(self):
        """Shortcut to creating a response queue in the MessageRunner."""
        self.resp_queue = self.msg_runner._setup_response_queue(self)

    def _cleanup_response_queue(self):
        """Shortcut to deleting a response queue in the MessageRunner."""
        if self.resp_queue:
            self.msg_runner._cleanup_response_queue(self)
            self.resp_queue = None

    def _wait_for_json_responses(self, num_responses=1):
        """Wait for response(s) to be put into the eventlet queue.  Since
        each queue entry actually contains a list of JSON-ified responses,
        combine them all into a single list to return.

        Destroy the eventlet queue when done.
        """
        if not self.resp_queue:
            # Source is not actually expecting a response
            return
        responses = []
        wait_time = CONF.cells.call_timeout
        try:
            for x in xrange(num_responses):
                json_responses = self.resp_queue.get(timeout=wait_time)
                responses.extend(json_responses)
        except queue.Empty:
            raise exception.CellTimeout()
        finally:
            self._cleanup_response_queue()
        return responses

    def _send_json_responses(self, json_responses, neighbor_only=False,
            fanout=False):
        """Send list of responses to this message.  Responses passed here
        are JSON-ified.  Targeted messages have a single response while
        Broadcast messages may have multiple responses.

        If this cell was the source of the message, these responses will
        be returned from self.process().

        Otherwise, we will route the response to the source of the
        request.  If 'neighbor_only' is True, the response will be sent
        to the neighbor cell, not the original requester.  Broadcast
        messages get aggregated at each hop, so neighbor_only will be
        True for those messages.
        """
        if not self.need_response:
            return
        if self.source_is_us():
            responses = []
            for json_response in json_responses:
                responses.append(Response.from_json(json_response))
            return responses
        direction = self.direction == 'up' and 'down' or 'up'
        response_kwargs = {'orig_message': self.to_json(),
                           'responses': json_responses}
        target_cell = _response_cell_name_from_path(self.routing_path,
                neighbor_only=neighbor_only)
        response = self.msg_runner._create_response_message(self.ctxt,
                direction, target_cell, self.uuid, response_kwargs,
                fanout=fanout)
        response.process()

    def _send_response(self, response, neighbor_only=False):
        """Send a response to this message.  If the source of the
        request was ourselves, just return the response.  It'll be
        passed back to the caller of self.process().  See DocString for
        _send_json_responses() as it handles most of the real work for
        this method.

        'response' is an instance of Response class.
        """
        if not self.need_response:
            return
        if self.source_is_us():
            return response
        self._send_json_responses([response.to_json()],
                                  neighbor_only=neighbor_only)

    def _send_response_from_exception(self, exc_info):
        """Take an exception as returned from sys.exc_info(), encode
        it in a Response, and send it.
        """
        response = Response(self.routing_path, exc_info, True)
        return self._send_response(response)

    def _to_dict(self):
        """Convert a message to a dictionary.  Only used internally."""
        _dict = {}
        for key in self.base_attrs_to_json:
            _dict[key] = getattr(self, key)
        return _dict

    def to_json(self):
        """Convert a message into JSON for sending to a sibling cell."""
        _dict = self._to_dict()
        # Convert context to dict.
        _dict['ctxt'] = _dict['ctxt'].to_dict()
        return jsonutils.dumps(_dict)

    def source_is_us(self):
        """Did this cell create this message?"""
        return self.routing_path == self.our_path_part

    def process(self):
        """Process a message.  Deal with it locally and/or forward it to a
        sibling cell.

        Override in a subclass.
        """
        raise NotImplementedError()


class _TargetedMessage(_BaseMessage):
    """A targeted message is a message that is destined for a specific
    single cell.

    'target_cell' can be a full cell name like 'api!child-cell' or it can
    be an instance of the CellState class if the target is a neighbor cell.
    """
    message_type = 'targeted'

    def __init__(self, msg_runner, ctxt, method_name, method_kwargs,
            direction, target_cell, **kwargs):
        super(_TargetedMessage, self).__init__(msg_runner, ctxt,
                method_name, method_kwargs, direction, **kwargs)
        if isinstance(target_cell, cells_state.CellState):
            # Neighbor cell or ourselves.  Convert it to a 'full path'.
            if target_cell.is_me:
                target_cell = self.our_path_part
            else:
                target_cell = '%s%s%s' % (self.our_path_part,
                                          _PATH_CELL_SEP,
                                          target_cell.name)
        self.target_cell = target_cell
        self.base_attrs_to_json.append('target_cell')

    def _get_next_hop(self):
        """Return the cell name for the next hop.  If the next hop is
        the current cell, return None.
        """
        if self.target_cell == self.routing_path:
            return self.state_manager.my_cell_state
        target_cell = self.target_cell
        routing_path = self.routing_path
        current_hops = routing_path.count(_PATH_CELL_SEP)
        next_hop_num = current_hops + 1
        dest_hops = target_cell.count(_PATH_CELL_SEP)
        if dest_hops < current_hops:
            reason = _("destination is %(target_cell)s but routing_path "
                    "is %(routing_path)s") % locals()
            raise exception.CellRoutingInconsistency(reason=reason)
        dest_name_parts = target_cell.split(_PATH_CELL_SEP)
        if (_PATH_CELL_SEP.join(dest_name_parts[:next_hop_num]) !=
                routing_path):
            reason = _("destination is %(target_cell)s but routing_path "
                    "is %(routing_path)s") % locals()
            raise exception.CellRoutingInconsistency(reason=reason)
        next_hop_name = dest_name_parts[next_hop_num]
        if self.direction == 'up':
            next_hop = self.state_manager.get_parent_cell(next_hop_name)
        else:
            next_hop = self.state_manager.get_child_cell(next_hop_name)
        if not next_hop:
            cell_type = 'parent' if self.direction == 'up' else 'child'
            reason = _("Unknown %(cell_type)s when routing to "
                       "%(target_cell)s") % locals()
            raise exception.CellRoutingInconsistency(reason=reason)
        return next_hop

    def process(self):
        """Process a targeted message.  This is called for all cells
        that touch this message.  If the local cell is the one that
        created this message, we reply directly with a Response instance.
        If the local cell is not the target, an eventlet queue is created
        and we wait for the response to show up via another thread
        receiving the Response back.

        Responses to targeted messages are routed directly back to the
        source.  No eventlet queues are created in intermediate hops.

        All exceptions for processing the message across the whole
        routing path are caught and encoded within the Response and
        returned to the caller.
        """
        try:
            next_hop = self._get_next_hop()
        except Exception as exc:
            exc_info = sys.exc_info()
            LOG.exception(_("Error locating next hop for message: %(exc)s"),
                          locals())
            return self._send_response_from_exception(exc_info)

        if next_hop.is_me:
            # Final destination.
            response = self._process_locally()
            return self._send_response(response)

        # Need to forward via neighbor cell.
        if self.need_response and self.source_is_us():
            # A response is needed and the source of the message is
            # this cell.  Create the eventlet queue.
            self._setup_response_queue()
            wait_for_response = True
        else:
            wait_for_response = False

        try:
            # This is inside the try block, so we can encode the
            # exception and return it to the caller.
            if self.hop_count >= self.max_hop_count:
                raise exception.CellMaxHopCountReached(
                        hop_count=self.hop_count)
            next_hop.send_message(self)
        except Exception as exc:
            exc_info = sys.exc_info()
            err_str = _("Failed to send message to cell: %(next_hop)s: "
                        "%(exc)s")
            LOG.exception(err_str, locals())
            self._cleanup_response_queue()
            return self._send_response_from_exception(exc_info)

        if wait_for_response:
            # Targeted messages only have 1 response.
            remote_response = self._wait_for_json_responses()[0]
            return Response.from_json(remote_response)


class _BroadcastMessage(_BaseMessage):
    """A broadcast message.  This means to call a method in every single
    cell going in a certain direction.
    """
    message_type = 'broadcast'

    def __init__(self, msg_runner, ctxt, method_name, method_kwargs,
            direction, run_locally=True, **kwargs):
        super(_BroadcastMessage, self).__init__(msg_runner, ctxt,
                method_name, method_kwargs, direction, **kwargs)
        # The local cell creating this message has the option
        # to be able to process the message locally or not.
        self.run_locally = run_locally
        self.is_broadcast = True

    def _get_next_hops(self):
        """Set the next hops and return the number of hops.  The next
        hops may include ourself.
        """
        if self.hop_count >= self.max_hop_count:
            return []
        if self.direction == 'down':
            return self.state_manager.get_child_cells()
        else:
            return self.state_manager.get_parent_cells()

    def _send_to_cells(self, target_cells):
        """Send a message to multiple cells."""
        for cell in target_cells:
            cell.send_message(self)

    def _send_json_responses(self, json_responses):
        """Responses to broadcast messages always need to go to the
        neighbor cell from which we received this message.  That
        cell aggregates the responses and makes sure to forward them
        to the correct source.
        """
        return super(_BroadcastMessage, self)._send_json_responses(
                json_responses, neighbor_only=True, fanout=True)

    def process(self):
        """Process a broadcast message.  This is called for all cells
        that touch this message.

        The message is sent to all cells in the certain direction and
        the creator of this message has the option of whether or not
        to process it locally as well.

        If responses from all cells are required, each hop creates an
        eventlet queue and waits for responses from its immediate
        neighbor cells.  All responses are then aggregated into a
        single list and are returned to the neighbor cell until the
        source is reached.

        When the source is reached, a list of Response instances are
        returned to the caller.

        All exceptions for processing the message across the whole
        routing path are caught and encoded within the Response and
        returned to the caller.  It is possible to get a mix of
        successful responses and failure responses.  The caller is
        responsible for dealing with this.
        """
        try:
            next_hops = self._get_next_hops()
        except Exception as exc:
            exc_info = sys.exc_info()
            LOG.exception(_("Error locating next hops for message: %(exc)s"),
                          locals())
            return self._send_response_from_exception(exc_info)

        # Short circuit if we don't need to respond
        if not self.need_response:
            if self.run_locally:
                self._process_locally()
            self._send_to_cells(next_hops)
            return

        # We'll need to aggregate all of the responses (from ourself
        # and our sibling cells) into 1 response
        try:
            self._setup_response_queue()
            self._send_to_cells(next_hops)
        except Exception as exc:
            # Error just trying to send to cells.  Send a single response
            # with the failure.
            exc_info = sys.exc_info()
            LOG.exception(_("Error sending message to next hops: %(exc)s"),
                          locals())
            self._cleanup_response_queue()
            return self._send_response_from_exception(exc_info)

        if self.run_locally:
            # Run locally and store the Response.
            local_response = self._process_locally()
        else:
            local_response = None

        try:
            remote_responses = self._wait_for_json_responses(
                    num_responses=len(next_hops))
        except Exception as exc:
            # Error waiting for responses, most likely a timeout.
            # Send a single response back with the failure.
            exc_info = sys.exc_info()
            err_str = _("Error waiting for responses from neighbor cells: "
                        "%(exc)s")
            LOG.exception(err_str, locals())
            return self._send_response_from_exception(exc_info)

        if local_response:
            remote_responses.append(local_response.to_json())
        return self._send_json_responses(remote_responses)


class _ResponseMessage(_TargetedMessage):
    """A response message is really just a special targeted message,
    saying to call 'parse_responses' when we reach the source of a 'call'.

    The 'fanout' attribute on this message may be true if we're responding
    to a broadcast or if we're about to respond to the source of an
    original target message.  Because multiple nova-cells services may
    be running within a cell, we need to make sure the response gets
    back to the correct one, so we have to fanout.
    """
    message_type = 'response'

    def __init__(self, msg_runner, ctxt, method_name, method_kwargs,
            direction, target_cell, response_uuid, **kwargs):
        super(_ResponseMessage, self).__init__(msg_runner, ctxt,
                method_name, method_kwargs, direction, target_cell, **kwargs)
        self.response_uuid = response_uuid
        self.base_attrs_to_json.append('response_uuid')

    def process(self):
        """Process a response.  If the target is the local cell, process
        the response here.  Otherwise, forward it to where it needs to
        go.
        """
        next_hop = self._get_next_hop()
        if next_hop.is_me:
            self._process_locally()
            return
        if self.fanout is False:
            # Really there's 1 more hop on each of these below, but
            # it doesn't matter for this logic.
            target_hops = self.target_cell.count(_PATH_CELL_SEP)
            current_hops = self.routing_path.count(_PATH_CELL_SEP)
            if current_hops + 1 == target_hops:
                # Next hop is the target.. so we must fanout.  See
                # DocString above.
                self.fanout = True
        next_hop.send_message(self)


#
# Methods that may be called when processing messages after reaching
# a target cell.
#


class _BaseMessageMethods(base.Base):
    """Base class for defining methods by message types."""
    def __init__(self, msg_runner):
        super(_BaseMessageMethods, self).__init__()
        self.msg_runner = msg_runner
        self.state_manager = msg_runner.state_manager
        # NOTE(matiu): We need the local api for both compute and aggregates,
        # not the cells version. This is so we can call it to do local
        # processing of messages
        self.compute_api = compute.api.API()
        self.aggregate_api = compute.api.AggregateAPI()
        self.securitygroup_api = compute.api.SecurityGroupAPI()
        self.compute_rpcapi = compute_rpcapi.ComputeAPI()
        self.consoleauth_rpcapi = consoleauth_rpcapi.ConsoleAuthAPI()
        self.securitygroup_rpcapi = compute_rpcapi.SecurityGroupAPI()

    def task_log_get_all(self, message, task_name, period_beginning,
                         period_ending, host, state):
        """Get task logs from the DB.  The message could have
        directly targeted this cell, or it could have been a broadcast
        message.

        If 'host' is not None, filter by host.
        If 'state' is not None, filter by state.
        """
        task_logs = self.db.task_log_get_all(message.ctxt, task_name,
                                             period_beginning,
                                             period_ending,
                                             host=host,
                                             state=state)
        return jsonutils.to_primitive(task_logs)


class _ResponseMessageMethods(_BaseMessageMethods):
    """Methods that are called from a ResponseMessage.  There's only
    1 method (parse_responses) and it is called when the message reaches
    the source of a 'call'.  All we do is stuff the response into the
    eventlet queue to signal the caller that's waiting.
    """
    def parse_responses(self, message, orig_message, responses):
        self.msg_runner._put_response(message.response_uuid,
                responses)


class _TargetedMessageMethods(_BaseMessageMethods):
    """These are the methods that can be called when routing a message
    to a specific cell.
    """
    def __init__(self, *args, **kwargs):
        super(_TargetedMessageMethods, self).__init__(*args, **kwargs)

    def schedule_run_instance(self, message, host_sched_kwargs):
        """Parent cell told us to schedule new instance creation."""
        self.msg_runner.scheduler.run_instance(message, host_sched_kwargs)

    def _instance_get_by_uuid(self, ctxt, instance_uuid):
        try:
            instance = self.db.instance_get_by_uuid(ctxt, instance_uuid)
        except exception.InstanceNotFound:
            with excutils.save_and_reraise_exception():
                # Must be a race condition.  Let's try to resolve it by
                # telling the top level cells that this instance doesn't
                # exist.
                instance = {'uuid': instance_uuid}
                self.msg_runner.instance_destroy_at_top(ctxt, instance)
        return instance

    def run_compute_api_method(self, message, method_info):
        """Run a method in the compute api class."""
        method = method_info['method']
        fn = getattr(self.compute_api, method, None)
        if not fn:
            detail = _("Unknown method '%(method)s' in compute API")
            raise exception.CellServiceAPIMethodNotFound(
                    detail=detail % locals())
        args = list(method_info['method_args'])
        # 1st arg is instance_uuid that we need to turn into the
        # instance object.
        instance_uuid = args[0]
        instance = self._instance_get_by_uuid(message.ctxt, instance_uuid)
        args[0] = instance
        return fn(message.ctxt, *args, **method_info['method_kwargs'])

    def run_securitygroup_api_method(self, message, method_info):
        """Run a method in the securitygroup api class."""
        method = method_info['method']
        fn = getattr(self.securitygroup_api, method, None)
        if not fn:
            detail = _("Unknown method '%(method)s' in securitygroup API")
            raise exception.CellServiceAPIMethodNotFound(
                    detail=detail % locals())
        args = list(method_info['method_args'])
        # 1st arg is instance_uuid that we need to turn into the
        # instance object.
        instance_uuid = args[0]
        instance = self._instance_get_by_uuid(message.ctxt, instance_uuid)
        args[0] = instance
        return fn(message.ctxt, *args, **method_info['method_kwargs'])

    def update_capabilities(self, message, cell_name, capabilities):
        """A child cell told us about their capabilities."""
        LOG.debug(_("Received capabilities from child cell "
                "%(cell_name)s: %(capabilities)s"), locals())
        self.state_manager.update_cell_capabilities(cell_name,
                capabilities)
        # Go ahead and update our parents now that a child updated us
        self.msg_runner.tell_parents_our_capabilities(message.ctxt)

    def update_capacities(self, message, cell_name, capacities):
        """A child cell told us about their capacity."""
        LOG.debug(_("Received capacities from child cell "
                "%(cell_name)s: %(capacities)s"), locals())
        self.state_manager.update_cell_capacities(cell_name,
                capacities)
        # Go ahead and update our parents now that a child updated us
        self.msg_runner.tell_parents_our_capacities(message.ctxt)

    def announce_capabilities(self, message):
        """A parent cell has told us to send our capabilities, so let's
        do so.
        """
        self.msg_runner.tell_parents_our_capabilities(message.ctxt)

    def announce_capacities(self, message):
        """A parent cell has told us to send our capacity, so let's
        do so.
        """
        self.msg_runner.tell_parents_our_capacities(message.ctxt)

    def service_get_by_compute_host(self, message, host_name):
        """Return the service entry for a compute host."""
        service = self.db.service_get_by_compute_host(message.ctxt,
                                                      host_name)
        return jsonutils.to_primitive(service)

    def proxy_rpc_to_manager(self, message, host_name, rpc_message,
                             topic, timeout):
        """Proxy RPC to the given compute topic."""
        # Check that the host exists.
        self.db.service_get_by_compute_host(message.ctxt, host_name)
        if message.need_response:
            return rpc.call(message.ctxt, topic, rpc_message,
                    timeout=timeout)
        rpc.cast(message.ctxt, topic, rpc_message)

    def compute_node_get(self, message, compute_id):
        """Get compute node by ID."""
        compute_node = self.db.compute_node_get(message.ctxt,
                                                compute_id)
        return jsonutils.to_primitive(compute_node)

    def actions_get(self, message, instance_uuid):
        actions = self.db.actions_get(message.ctxt, instance_uuid)
        return jsonutils.to_primitive(actions)

    def action_get_by_request_id(self, message, instance_uuid, request_id):
        action = self.db.action_get_by_request_id(message.ctxt, instance_uuid,
                                                  request_id)
        return jsonutils.to_primitive(action)

    def action_events_get(self, message, action_id):
        action_events = self.db.action_events_get(message.ctxt, action_id)
        return jsonutils.to_primitive(action_events)

    def validate_console_port(self, message, instance_uuid, console_port,
                              console_type):
        """Validate console port with child cell compute node."""
        # 1st arg is instance_uuid that we need to turn into the
        # instance object.
        try:
            instance = self.db.instance_get_by_uuid(message.ctxt,
                                                    instance_uuid)
        except exception.InstanceNotFound:
            with excutils.save_and_reraise_exception():
                # Must be a race condition.  Let's try to resolve it by
                # telling the top level cells that this instance doesn't
                # exist.
                instance = {'uuid': instance_uuid}
                self.msg_runner.instance_destroy_at_top(message.ctxt,
                                                        instance)
        return self.compute_rpcapi.validate_console_port(message.ctxt,
                instance, console_port, console_type)

    def create_aggregate(self, message, aggregate_name, availability_zone):
        response = self.aggregate_api.create_aggregate(
            message.ctxt, aggregate_name, availability_zone)
        return jsonutils.to_primitive(response)

    def get_aggregate(self, message, aggregate_id):
        response = self.aggregate_api.get_aggregate(
            message.ctxt, aggregate_id)
        return jsonutils.to_primitive(response)

    def get_aggregate_list(self, message):
        response = self.aggregate_api.get_aggregate_list(message.ctxt)
        return jsonutils.to_primitive(response)

    def update_aggregate(self, message, aggregate_id, values):
        response = self.aggregate_api.update_aggregate(
            message.ctxt, aggregate_id, values)
        return jsonutils.to_primitive(response)

    def update_aggregate_metadata(self, message, aggregate_id, metadata):
        response = self.aggregate_api.update_aggregate_metadata(
            message.ctxt, aggregate_id, metadata)
        return jsonutils.to_primitive(response)

    def delete_aggregate(self, message, aggregate_id):
        self.aggregate_api.delete_aggregate(message.ctxt, aggregate_id)

    def add_host_to_aggregate(self, message, aggregate_id, host_name):
        response = self.aggregate_api.add_host_to_aggregate(
            message.ctxt, aggregate_id, host_name)
        return jsonutils.to_primitive(response)

    def remove_host_from_aggregate(self, message, aggregate_id, host_name):
        response = self.aggregate_api.remove_host_from_aggregate(
            message.ctxt, aggregate_id, host_name)
        return jsonutils.to_primitive(response)

    def authorize_console(self, message, token, console_type,
                          host, port, internal_access_path,
                          instance_uuid):
        self.consoleauth_rpcapi.authorize_console(message.ctxt,
                            token, console_type, host,
                            port, internal_access_path, instance_uuid)

    def get_host_availability_zone(self, message, host):
        return availability_zones.get_host_availability_zone(message.ctxt, host)

    def instance_type_create(self, message, values):
        return self.db.instance_type_create(message.ctxt, values)

class _BroadcastMessageMethods(_BaseMessageMethods):
    """These are the methods that can be called as a part of a broadcast
    message.
    """
    def _at_the_top(self):
        """Are we the API level?"""
        return not self.state_manager.get_parent_cells()

    def instance_update_at_top(self, message, instance, **kwargs):
        """Update an instance in the DB if we're a top level cell."""
        if not self._at_the_top():
            return
        instance_uuid = instance['uuid']

        # Remove things that we can't update in the top level cells.
        # 'metadata' is only updated in the API cell, so don't overwrite
        # it based on what child cells say.  Make sure to update
        # 'cell_name' based on the routing path.
        items_to_remove = ['id', 'security_groups', 'volumes', 'cell_name',
                           'name', 'metadata']
        for key in items_to_remove:
            instance.pop(key, None)
        instance['cell_name'] = _reverse_path(message.routing_path)

        # Fixup info_cache.  We'll have to update this separately if
        # it exists.
        info_cache = instance.pop('info_cache', None)
        if info_cache is not None:
            info_cache.pop('id', None)
            info_cache.pop('instance', None)

        # Fixup system_metadata (should be a dict for update, not a list)
        if ('system_metadata' in instance and
                isinstance(instance['system_metadata'], list)):
            sys_metadata = dict([(md['key'], md['value'])
                    for md in instance['system_metadata']])
            instance['system_metadata'] = sys_metadata

        LOG.debug(_("Got update for instance %(instance_uuid)s: "
                "%(instance)s") % locals())

        # It's possible due to some weird condition that the instance
        # was already set as deleted... so we'll attempt to update
        # it with permissions that allows us to read deleted.
        with utils.temporary_mutation(message.ctxt, read_deleted="yes"):
            try:
                self.db.instance_update(message.ctxt, instance_uuid,
                        instance, update_cells=False)
            except exception.NotFound:
                # FIXME(comstud): Strange.  Need to handle quotas here,
                # if we actually want this code to remain..
                self.db.instance_create(message.ctxt, instance)
        if info_cache:
            try:
                self.db.instance_info_cache_update(
                        message.ctxt, instance_uuid, info_cache)
            except exception.InstanceInfoCacheNotFound:
                # Can happen if we try to update a deleted instance's
                # network information.
                pass

    def instance_destroy_at_top(self, message, instance, **kwargs):
        """Destroy an instance from the DB if we're a top level cell."""
        if not self._at_the_top():
            return
        instance_uuid = instance['uuid']
        LOG.debug(_("Got update to delete instance %(instance_uuid)s") %
                locals())
        try:
            self.db.instance_destroy(message.ctxt, instance_uuid,
                    update_cells=False)
        except exception.InstanceNotFound:
            pass

    def instance_delete_everywhere(self, message, instance, delete_type,
                                   **kwargs):
        """Call compute API delete() or soft_delete() in every cell.
        This is used when the API cell doesn't know what cell an instance
        belongs to but the instance was requested to be deleted or
        soft-deleted.  So, we'll run it everywhere.
        """
        LOG.debug(_("Got broadcast to %(delete_type)s delete instance"),
            locals(), instance=instance)
        if delete_type == 'soft':
            self.compute_api.soft_delete(message.ctxt, instance)
        else:
            self.compute_api.delete(message.ctxt, instance)

    def instance_fault_create_at_top(self, message, instance_fault, **kwargs):
        """Destroy an instance from the DB if we're a top level cell."""
        if not self._at_the_top():
            return
        items_to_remove = ['id']
        for key in items_to_remove:
            instance_fault.pop(key, None)
        log_str = _("Got message to create instance fault: "
                    "%(instance_fault)s")
        LOG.debug(log_str, locals())
        self.db.instance_fault_create(message.ctxt, instance_fault)

    def bw_usage_update_at_top(self, message, bw_update_info, **kwargs):
        """Update Bandwidth usage in the DB if we're a top level cell."""
        if not self._at_the_top():
            return
        self.db.bw_usage_update(message.ctxt, **bw_update_info)

    def _sync_instance(self, ctxt, instance):
        if instance['deleted']:
            self.msg_runner.instance_destroy_at_top(ctxt, instance)
        else:
            self.msg_runner.instance_update_at_top(ctxt, instance)

    def sync_instances(self, message, project_id, updated_since, deleted,
                       **kwargs):
        projid_str = project_id is None and "<all>" or project_id
        since_str = updated_since is None and "<all>" or updated_since
        LOG.info(_("Forcing a sync of instances, project_id="
                   "%(projid_str)s, updated_since=%(since_str)s"), locals())
        if updated_since is not None:
            updated_since = timeutils.parse_isotime(updated_since)
        instances = cells_utils.get_instances_to_sync(message.ctxt,
                updated_since=updated_since, project_id=project_id,
                deleted=deleted)
        for instance in instances:
            self._sync_instance(message.ctxt, instance)

    def service_get_all(self, message, filters):
        if filters is None:
            filters = {}
        disabled = filters.pop('disabled', None)
        services = self.db.service_get_all(message.ctxt, disabled=disabled)
        services = availability_zones.set_availability_zones(message.ctxt, services)
        ret_services = []
        for service in services:
            service = jsonutils.to_primitive(service)
            for key, val in filters.iteritems():
                if service[key] != val:
                    break
            else:
                ret_services.append(service)
        return ret_services

    def compute_node_get_all(self, message, hypervisor_match):
        """Return compute nodes in this cell."""
        if hypervisor_match is not None:
            nodes = self.db.compute_node_search_by_hypervisor(message.ctxt,
                    hypervisor_match)
        else:
            nodes = self.db.compute_node_get_all(message.ctxt)
        return jsonutils.to_primitive(nodes)

    def compute_node_stats(self, message):
        """Return compute node stats from this cell."""
        return self.db.compute_node_statistics(message.ctxt)

    def consoleauth_delete_tokens(self, message, instance_uuid):
        """Delete consoleauth tokens for an instance in API cells."""
        if not self._at_the_top():
            return
        self.consoleauth_rpcapi.delete_tokens_for_instance(message.ctxt,
                                                           instance_uuid)

    def get_aggregate_list(self, message):
        response = self.aggregate_api.get_aggregate_list(message.ctxt)
        return jsonutils.to_primitive(response)

    def bdm_update_or_create_at_top(self, message, bdm, create):
        """Create or update a block device mapping in API cells.  If
        create is True, only try to create.  If create is None, try to
        update but fall back to create.  If create is False, only attempt
        to update.  This maps to nova-conductor's behavior.
        """
        if not self._at_the_top():
            return
        items_to_remove = ['id']
        for key in items_to_remove:
            bdm.pop(key, None)
        if create is None:
            self.db.block_device_mapping_update_or_create(message.ctxt,
                                                          bdm)
            return
        elif create is True:
            self.db.block_device_mapping_create(message.ctxt, bdm)
            return
        # Unfortunately this update call wants BDM ID... but we don't know
        # what it is in this cell.  Search for it.. try matching either
        # device_name or volume_id.
        dev_name = bdm['device_name']
        vol_id = bdm['volume_id']
        instance_bdms = self.db.block_device_mapping_get_all_by_instance(
                message.ctxt, bdm['instance_uuid'])
        for instance_bdm in instance_bdms:
            if dev_name and instance_bdm['device_name'] == dev_name:
                break
            if vol_id and instance_bdm['volume_id'] == vol_id:
                break
        else:
            LOG.warn(_("No match when trying to update BDM: %(bdm)s"),
                     dict(bdm=bdm))
            return
        self.db.block_device_mapping_update(message.ctxt,
                                            instance_bdm['id'], bdm)

    def bdm_destroy_at_top(self, message, instance_uuid, device_name,
                           volume_id):
        """Destroy a block device mapping in API cells by device name
        or volume_id.  device_name or volume_id can be None, but not both.
        """
        if not self._at_the_top():
            return
        if device_name:
            self.db.block_device_mapping_destroy_by_instance_and_device(
                    message.ctxt, instance_uuid, device_name)
        elif volume_id:
            self.db.block_device_mapping_destroy_by_instance_and_volume(
                    message.ctxt, instance_uuid, volume_id)

    def security_group_create(self, message, group):
        ctxt = message.ctxt

        group_dict = dict(group.iteritems())
        LOG.debug(_("Received message to create group %(group_dict)s") % locals())

        # See if this already exists
        try:
            group = self.db.security_group_get_by_name(
                ctxt,
                group['project_id'],
                group['name']
            )
        except exception.SecurityGroupNotFound:
            #Doesn't exist so we'll create it
            LOG.info(_("Adding missing security group %s" % ((dict(group.iteritems())))))
            self.db.security_group_create(ctxt, group, update_cells=False)

    def security_group_destroy(self, message, group):

        LOG.debug(_("Received message to destroy group %s" % ((dict(group.iteritems())))))
        ctxt = message.ctxt

        # See if this group exists and remove it
        try:
            group = self.db.security_group_get_by_name(
                ctxt,
                group['project_id'],
                group['name']
            )
            # Exists so destroy
            LOG.info(_("Deleting out of sync security group %s" % ((dict(group.iteritems())))))
            self.db.security_group_destroy(ctxt, group['id'], update_cells=False)
        except exception.SecurityGroupNotFound:
            # Doesn't exist so do nothing
            pass

    def _refresh_group_security_rules(self, ctxt, group):
        instances = group['instances']
        for instance in instances:
            self.securitygroup_rpcapi.refresh_instance_security_rules(ctxt,
                instance['host'], instance)

    def security_group_rule_create(self, message, rule):
        ctxt = message.ctxt

        rule_dict = dict(rule.iteritems())
        LOG.debug(_("Received message to create rule %(rule_dict)s"), locals())

        # NOTE(shauno): The parent group id was replaced with the
        # group name and project id when this message was created
        # so we could link this rule back to the correct parent group
        # even if ids get out of sync between parent and child cells.
        # Use them to find the correct parent group and replace them
        # with the id.
        rule = rule.copy()
        security_group_name = rule.pop('parent_group_name', None)
        security_group_pid = rule.pop('parent_group_pid', None)
        linked_group_name = rule.pop('linked_group_name', None)

        if not security_group_name or not security_group_pid:
            LOG.error(_("Could not add rule %(rule)s "
                        "to group '%(security_group_name)s' "
                        "(no parent name/project)"),
                      locals())
            return

        # Security group name and project id were included correctly

        try:
            group = self.db.security_group_get_by_name(
                ctxt,
                security_group_pid,
                security_group_name
            )
        except exception.SecurityGroupNotFound:
            LOG.error(_("Could not add rule %(rule)s "
                        "to group '%(security_group_name)s' "
                        "(group missing from db)"),
                      locals())
            return

        if linked_group_name:
            try:
                linked_group = self.db.security_group_get_by_name(
                    ctxt,
                    security_group_pid,
                    linked_group_name,
                )
                rule['group_id'] = linked_group['id']
            except exception.SecurityGroupNotFound:
                LOG.error(_("Could not add rule %(rule)s "
                            "to group '%(security_group_name)s' "
                            "(linked group missing from db)"),
                          locals())
                return

        rule['parent_group_id'] = group['id']
        # Check to see if rule exists already (only check for active entries)
        existing_rule = rule.copy()
        existing_rule['deleted'] = False
        existing_rule = self.db.security_group_rule_get_all_by_filters(
            ctxt, existing_rule, 'deleted', 'asc')
        if not existing_rule:
            LOG.info(_("Adding missing security group rule %s" % ((dict(rule.iteritems())))))
            self.db.security_group_rule_create(ctxt, rule, update_cells=False)
            self._refresh_group_security_rules(ctxt, group)

    def security_group_rule_destroy(self, message, rule):
        # TODO(kspear): Refactor this to share code from rule_create above.
        LOG.debug(_("Received message to delete rule %s" % (rule)))

        ctxt = message.ctxt

        rule = rule.copy()
        security_group_name = rule.pop('parent_group_name', None)
        security_group_pid = rule.pop('parent_group_pid', None)
        linked_group_name = rule.pop('linked_group_name', None)

        if not security_group_name or not security_group_pid:
            LOG.error(_( "Could not remove rule %(rule)s "
                         "to group '%(security_group_name)s' (group missing from db)"),
                      locals())
            return

        # Security group name and project id were included correctly

        try:
            group = self.db.security_group_get_by_name(
                ctxt,
                security_group_pid,
                security_group_name
            )
        except exception.SecurityGroupNotFound:
            LOG.warn(_("Could not remove rule %(rule)s "
                       "from group '%(security_group_name)s' "
                       "(group missing from db)"),
                     locals())
            #May need to make this better
            return

        if linked_group_name:
            try:
                linked_group = self.db.security_group_get_by_name(
                    ctxt,
                    security_group_pid,
                    linked_group_name,
                )
                rule['group_id'] = linked_group['id']
            except exception.SecurityGroupNotFound:
                LOG.warn(_("Could not remove rule %(rule)s "
                           "to group '%(security_group_name)s' "
                           "(linked group missing from db)"),
                         locals())
                #TODO May need to make this better
                return

        rule['parent_group_id'] = group['id']
        rule['deleted'] = False
        rules = self.db.security_group_rule_get_all_by_filters(ctxt,
                    rule, 'deleted', 'asc')
        if rules:
            rule_to_delete = rules[0]
            LOG.info(_("Deleting out of sync security group rule %s"),
                     dict(rule_to_delete.iteritems()))
            self.db.security_group_rule_destroy(ctxt, rule_to_delete['id'])
            self._refresh_group_security_rules(ctxt, group)

    def _association_exists(self, ctxt, instance_uuid, group_id):
        filters = {'instance_uuid': instance_uuid,
                   'security_group_id': group_id,
                   'deleted': False}
        return self.db.security_group_instance_association_get_all_by_filters(
                   ctxt, filters, 'deleted', 'asc')

    def instance_add_security_group(self, message, instance_uuid, group):
        LOG.debug(_("Received message to add security group %(name)s to "
                    "instance %(instance_uuid)s"),
                  {'instance_uuid': instance_uuid, 'name': group['name']})
        if not self._at_the_top():
            return
        ctxt = message.ctxt
        group_name = group['name']
        try:
            group = self.db.security_group_get_by_name(
                ctxt,
                group['project_id'],
                group['name']
            )
            exists = self._association_exists(ctxt, instance_uuid, group['id'])
        except exception.SecurityGroupNotFound:
            LOG.error(_("Could not associate instance %(instance_uuid)s "
                        "with group '%(group_name)s' "
                        "(group missing from db)"),
                      {'group_name': group_name,
                       'instance_uuid': instance_uuid})
            return
        if not exists:
            self.db.instance_add_security_group(ctxt,
                    instance_uuid, group['id'],
                    update_cells=False)

    def instance_remove_security_group(self, message, instance_uuid, group):
        LOG.debug(_("Received message to remove security group %(name)s to "
                    "instance %(instance_uuid)s"),
                  {'instance_uuid': instance_uuid, 'name': group['name']})
        if not self._at_the_top():
            return
        ctxt = message.ctxt
        group_name = group['name']
        try:
            group = self.db.security_group_get_by_name(
                ctxt,
                group['project_id'],
                group['name']
            )
            exists = self._association_exists(ctxt, instance_uuid, group['id'])
        except exception.SecurityGroupNotFound:
            LOG.error(_("Could not disassociate instance %(instance_uuid)s "
                        "from group '%(group_name)s' "
                        "(group missing from db)"),
                      {'group_name': group_name,
                       'instance_uuid': instance_uuid})
            return
        if exists:
            self.db.instance_remove_security_group(ctxt,
                    instance_uuid, group['id'],
                    update_cells=False)

    def ec2_instance_create(self, message, instance_uuid, ec2_id):
        self.db.ec2_instance_create(message.ctxt, instance_uuid, ec2_id,
                                    update_cells=False)

    def s3_image_create(self, message, image_uuid, s3_id):
        self.db.s3_image_create(message.ctxt, image_uuid, s3_id,
                                update_cells=False)

    def ec2_volume_create(self, message, volume_uuid, ec2_id):
        self.db.ec2_volume_create(message.ctxt, volume_uuid, ec2_id,
                                  update_cells=False)


_CELL_MESSAGE_TYPE_TO_MESSAGE_CLS = {'targeted': _TargetedMessage,
                                     'broadcast': _BroadcastMessage,
                                     'response': _ResponseMessage}
_CELL_MESSAGE_TYPE_TO_METHODS_CLS = {'targeted': _TargetedMessageMethods,
                                     'broadcast': _BroadcastMessageMethods,
                                     'response': _ResponseMessageMethods}


#
# Below are the public interfaces into this module.
#


class MessageRunner(object):
    """This class is the main interface into creating messages and
    processing them.

    Public methods in this class are typically called by the CellsManager
    to create a new message and process it with the exception of
    'message_from_json' which should be used by CellsDrivers to convert
    a JSONified message it has received back into the appropriate Message
    class.

    Private methods are used internally when we need to keep some
    'global' state.  For instance, eventlet queues used for responses are
    held in this class.  Also, when a Message is process()ed above and
    it's determined we should take action locally,
    _process_message_locally() will be called.

    When needing to add a new method to call in a Cell2Cell message,
    define the new method below and also add it to the appropriate
    MessageMethods class where the real work will be done.
    """

    def __init__(self, state_manager):
        self.state_manager = state_manager
        cells_scheduler_cls = importutils.import_class(
                CONF.cells.scheduler)
        self.scheduler = cells_scheduler_cls(self)
        self.response_queues = {}
        self.methods_by_type = {}
        self.our_name = CONF.cells.name
        for msg_type, cls in _CELL_MESSAGE_TYPE_TO_METHODS_CLS.iteritems():
            self.methods_by_type[msg_type] = cls(self)

    def _process_message_locally(self, message):
        """Message processing will call this when its determined that
        the message should be processed within this cell.  Find the
        method to call based on the message type, and call it.  The
        caller is responsible for catching exceptions and returning
        results to cells, if needed.
        """
        methods = self.methods_by_type[message.message_type]
        fn = getattr(methods, message.method_name)
        return fn(message, **message.method_kwargs)

    def _put_response(self, response_uuid, response):
        """Put a response into a response queue.  This is called when
        a _ResponseMessage is processed in the cell that initiated a
        'call' to another cell.
        """
        resp_queue = self.response_queues.get(response_uuid)
        if not resp_queue:
            # Response queue is gone.  We must have restarted or we
            # received a response after our timeout period.
            return
        resp_queue.put(response)

    def _setup_response_queue(self, message):
        """Set up an eventlet queue to use to wait for replies.

        Replies come back from the target cell as a _ResponseMessage
        being sent back to the source.
        """
        resp_queue = queue.Queue()
        self.response_queues[message.uuid] = resp_queue
        return resp_queue

    def _cleanup_response_queue(self, message):
        """Stop tracking the response queue either because we're
        done receiving responses, or we've timed out.
        """
        try:
            del self.response_queues[message.uuid]
        except KeyError:
            # Ignore if queue is gone already somehow.
            pass

    def _create_response_message(self, ctxt, direction, target_cell,
            response_uuid, response_kwargs, **kwargs):
        """Create a ResponseMessage.  This is used internally within
        the messaging module.
        """
        return _ResponseMessage(self, ctxt, 'parse_responses',
                                response_kwargs, direction, target_cell,
                                response_uuid, **kwargs)

    def message_from_json(self, json_message):
        """Turns a message in JSON format into an appropriate Message
        instance.  This is called when cells receive a message from
        another cell.
        """
        message_dict = jsonutils.loads(json_message)
        message_type = message_dict.pop('message_type')
        # Need to convert context back.
        ctxt = message_dict['ctxt']
        message_dict['ctxt'] = context.RequestContext.from_dict(ctxt)
        message_cls = _CELL_MESSAGE_TYPE_TO_MESSAGE_CLS[message_type]
        return message_cls(self, **message_dict)

    def ask_children_for_capabilities(self, ctxt):
        """Tell child cells to send us capabilities.  This is typically
        called on startup of the nova-cells service.
        """
        child_cells = self.state_manager.get_child_cells()
        for child_cell in child_cells:
            message = _TargetedMessage(self, ctxt,
                                        'announce_capabilities',
                                        dict(), 'down', child_cell)
            message.process()

    def ask_children_for_capacities(self, ctxt):
        """Tell child cells to send us capacities.  This is typically
        called on startup of the nova-cells service.
        """
        child_cells = self.state_manager.get_child_cells()
        for child_cell in child_cells:
            message = _TargetedMessage(self, ctxt, 'announce_capacities',
                                        dict(), 'down', child_cell)
            message.process()

    def tell_parents_our_capabilities(self, ctxt):
        """Send our capabilities to parent cells."""
        parent_cells = self.state_manager.get_parent_cells()
        if not parent_cells:
            return
        my_cell_info = self.state_manager.get_my_state()
        capabs = self.state_manager.get_our_capabilities()
        LOG.debug(_("Updating parents with our capabilities: %(capabs)s"),
                locals())
        # We have to turn the sets into lists so they can potentially
        # be json encoded when the raw message is sent.
        for key, values in capabs.items():
            capabs[key] = list(values)
        method_kwargs = {'cell_name': my_cell_info.name,
                         'capabilities': capabs}
        for cell in parent_cells:
            message = _TargetedMessage(self, ctxt, 'update_capabilities',
                    method_kwargs, 'up', cell, fanout=True)
            message.process()

    def tell_parents_our_capacities(self, ctxt):
        """Send our capacities to parent cells."""
        parent_cells = self.state_manager.get_parent_cells()
        if not parent_cells:
            return
        my_cell_info = self.state_manager.get_my_state()
        capacities = self.state_manager.get_our_capacities()
        LOG.debug(_("Updating parents with our capacities: %(capacities)s"),
                locals())
        method_kwargs = {'cell_name': my_cell_info.name,
                         'capacities': capacities}
        for cell in parent_cells:
            message = _TargetedMessage(self, ctxt, 'update_capacities',
                    method_kwargs, 'up', cell, fanout=True)
            message.process()

    def schedule_run_instance(self, ctxt, target_cell, host_sched_kwargs):
        """Called by the scheduler to tell a child cell to schedule
        a new instance for build.
        """
        method_kwargs = dict(host_sched_kwargs=host_sched_kwargs)
        message = _TargetedMessage(self, ctxt, 'schedule_run_instance',
                                    method_kwargs, 'down',
                                    target_cell)
        message.process()

    def run_compute_api_method(self, ctxt, cell_name, method_info, call):
        """Call a compute API method in a specific cell."""
        message = _TargetedMessage(self, ctxt, 'run_compute_api_method',
                                   dict(method_info=method_info), 'down',
                                   cell_name, need_response=call)
        return message.process()

    def run_securitygroup_api_method(self, ctxt, cell_name, method_info, call):
        """Call a compute API method in a specific cell."""
        message = _TargetedMessage(self, ctxt, 'run_securitygroup_api_method',
                                   dict(method_info=method_info), 'down',
                                   cell_name, need_response=call)
        return message.process()

    def instance_update_at_top(self, ctxt, instance):
        """Update an instance at the top level cell."""
        message = _BroadcastMessage(self, ctxt, 'instance_update_at_top',
                                    dict(instance=instance), 'up',
                                    run_locally=False)
        message.process()

    def instance_destroy_at_top(self, ctxt, instance):
        """Destroy an instance at the top level cell."""
        message = _BroadcastMessage(self, ctxt, 'instance_destroy_at_top',
                                    dict(instance=instance), 'up',
                                    run_locally=False)
        message.process()

    def instance_delete_everywhere(self, ctxt, instance, delete_type):
        """This is used by API cell when it didn't know what cell
        an instance was in, but the instance was requested to be
        deleted or soft_deleted.  So, we'll broadcast this everywhere.
        """
        method_kwargs = dict(instance=instance, delete_type=delete_type)
        message = _BroadcastMessage(self, ctxt,
                                    'instance_delete_everywhere',
                                    method_kwargs, 'down',
                                    run_locally=False)
        message.process()

    def instance_fault_create_at_top(self, ctxt, instance_fault):
        """Create an instance fault at the top level cell."""
        message = _BroadcastMessage(self, ctxt,
                                    'instance_fault_create_at_top',
                                    dict(instance_fault=instance_fault),
                                    'up', run_locally=False)
        message.process()

    def bw_usage_update_at_top(self, ctxt, bw_update_info):
        """Update bandwidth usage at top level cell."""
        message = _BroadcastMessage(self, ctxt, 'bw_usage_update_at_top',
                                    dict(bw_update_info=bw_update_info),
                                    'up', run_locally=False)
        message.process()

    def sync_instances(self, ctxt, project_id, updated_since, deleted):
        """Force a sync of all instances, potentially by project_id,
        and potentially since a certain date/time.
        """
        method_kwargs = dict(project_id=project_id,
                             updated_since=updated_since,
                             deleted=deleted)
        message = _BroadcastMessage(self, ctxt, 'sync_instances',
                                    method_kwargs, 'down',
                                    run_locally=False)
        message.process()

    def service_get_all(self, ctxt, filters=None):
        method_kwargs = dict(filters=filters)
        message = _BroadcastMessage(self, ctxt, 'service_get_all',
                                    method_kwargs, 'down',
                                    run_locally=True, need_response=True)
        return message.process()

    def service_get_by_compute_host(self, ctxt, cell_name, host_name):
        method_kwargs = dict(host_name=host_name)
        message = _TargetedMessage(self, ctxt,
                                  'service_get_by_compute_host',
                                  method_kwargs, 'down', cell_name,
                                  need_response=True)
        return message.process()

    def proxy_rpc_to_manager(self, ctxt, cell_name, host_name, topic,
                             rpc_message, call, timeout):
        method_kwargs = {'host_name': host_name,
                         'topic': topic,
                         'rpc_message': rpc_message,
                         'timeout': timeout}
        message = _TargetedMessage(self, ctxt,
                                   'proxy_rpc_to_manager',
                                   method_kwargs, 'down', cell_name,
                                   need_response=call)
        return message.process()

    def task_log_get_all(self, ctxt, cell_name, task_name,
                         period_beginning, period_ending,
                         host=None, state=None):
        """Get task logs from the DB from all cells or a particular
        cell.

        If 'cell_name' is None or '', get responses from all cells.
        If 'host' is not None, filter by host.
        If 'state' is not None, filter by state.

        Return a list of Response objects.
        """
        method_kwargs = dict(task_name=task_name,
                             period_beginning=period_beginning,
                             period_ending=period_ending,
                             host=host, state=state)
        if cell_name:
            message = _TargetedMessage(self, ctxt, 'task_log_get_all',
                                    method_kwargs, 'down',
                                    cell_name, need_response=True)
            # Caller should get a list of Responses.
            return [message.process()]
        message = _BroadcastMessage(self, ctxt, 'task_log_get_all',
                                    method_kwargs, 'down',
                                    run_locally=True, need_response=True)
        return message.process()

    def compute_node_get_all(self, ctxt, hypervisor_match=None):
        """Return list of compute nodes in all child cells."""
        method_kwargs = dict(hypervisor_match=hypervisor_match)
        message = _BroadcastMessage(self, ctxt, 'compute_node_get_all',
                                    method_kwargs, 'down',
                                    run_locally=True, need_response=True)
        return message.process()

    def compute_node_stats(self, ctxt):
        """Return compute node stats from all child cells."""
        method_kwargs = dict()
        message = _BroadcastMessage(self, ctxt, 'compute_node_stats',
                                    method_kwargs, 'down',
                                    run_locally=True, need_response=True)
        return message.process()

    def compute_node_get(self, ctxt, cell_name, compute_id):
        """Return compute node entry from a specific cell by ID."""
        method_kwargs = dict(compute_id=compute_id)
        message = _TargetedMessage(self, ctxt, 'compute_node_get',
                                    method_kwargs, 'down',
                                    cell_name, need_response=True)
        return message.process()

    def actions_get(self, ctxt, cell_name, instance_uuid):
        method_kwargs = dict(instance_uuid=instance_uuid)
        message = _TargetedMessage(self, ctxt, 'actions_get',
                                method_kwargs, 'down',
                                cell_name, need_response=True)
        return message.process()

    def action_get_by_request_id(self, ctxt, cell_name, instance_uuid,
                                 request_id):
        method_kwargs = dict(instance_uuid=instance_uuid,
                             request_id=request_id)
        message = _TargetedMessage(self, ctxt, 'action_get_by_request_id',
                                method_kwargs, 'down',
                                cell_name, need_response=True)
        return message.process()

    def action_events_get(self, ctxt, cell_name, action_id):
        method_kwargs = dict(action_id=action_id)
        message = _TargetedMessage(self, ctxt, 'action_events_get',
                                method_kwargs, 'down',
                                cell_name, need_response=True)
        return message.process()

    def consoleauth_delete_tokens(self, ctxt, instance_uuid):
        """Delete consoleauth tokens for an instance in API cells."""
        message = _BroadcastMessage(self, ctxt, 'consoleauth_delete_tokens',
                                    dict(instance_uuid=instance_uuid),
                                    'up', run_locally=False)
        message.process()

    def validate_console_port(self, ctxt, cell_name, instance_uuid,
                              console_port, console_type):
        """Validate console port with child cell compute node."""
        method_kwargs = {'instance_uuid': instance_uuid,
                         'console_port': console_port,
                         'console_type': console_type}
        message = _TargetedMessage(self, ctxt, 'validate_console_port',
                                   method_kwargs, 'down',
                                   cell_name, need_response=True)
        return message.process()

    def create_aggregate(self, ctxt, cell_name, aggregate_name,
                         availability_zone):
        method_kwargs = dict(aggregate_name=aggregate_name,
                             availability_zone=availability_zone)
        message = _TargetedMessage(self, ctxt, 'create_aggregate',
                                   method_kwargs, 'down',
                                   cell_name, need_response=True)
        return message.process()
 
    def get_aggregate(self, ctxt, cell_name, aggregate_id):
        method_kwargs = dict(aggregate_id=aggregate_id)
        message = _TargetedMessage(self, ctxt, 'get_aggregate',
                method_kwargs, 'down',
                cell_name, need_response=True)
        return message.process()

    def get_aggregate_list(self, ctxt, cell_name=None):
        if cell_name is None:
            message = _BroadcastMessage(self, ctxt, 'get_aggregate_list',
                    {}, 'down', need_response=True)
        else:
            message = _TargetedMessage(self, ctxt, 'get_aggregate_list',
                    {}, 'down',
                    cell_name, need_response=True)
        return message.process()

    def update_aggregate(self, ctxt, cell_name, aggregate_id, values):
        method_kwargs = dict(aggregate_id=aggregate_id, values=values)
        message = _TargetedMessage(self, ctxt, 'update_aggregate',
                method_kwargs, 'down',
                cell_name, need_response=True)
        return message.process()

    def update_aggregate_metadata(self, ctxt, cell_name,
                                  aggregate_id, metadata):
        method_kwargs = dict(aggregate_id=aggregate_id, metadata=metadata)
        message = _TargetedMessage(self, ctxt, 'update_aggregate_metadata',
                method_kwargs, 'down',
                cell_name, need_response=True)
        return message.process()

    def delete_aggregate(self, ctxt, cell_name, aggregate_id):
        method_kwargs = dict(aggregate_id=aggregate_id)
        message = _TargetedMessage(self, ctxt, 'delete_aggregate',
                method_kwargs, 'down',
                cell_name, need_response=False)
        message.process()

    def add_host_to_aggregate(self, ctxt, cell_name, aggregate_id, host_name):
        method_kwargs = dict(aggregate_id=aggregate_id,
                             host_name=host_name)
        message = _TargetedMessage(self, ctxt, 'add_host_to_aggregate',
                method_kwargs, 'down',
                cell_name, need_response=True)
        return message.process()

    def remove_host_from_aggregate(self, ctxt, cell_name,
                                   aggregate_id, host_name):
        method_kwargs = dict(aggregate_id=aggregate_id,
                             host_name=host_name)
        message = _TargetedMessage(self, ctxt, 'remove_host_from_aggregate',
                method_kwargs, 'down',
                cell_name, need_response=True)
        return message.process()

    def authorize_console(self, ctxt, cell_name,
                          token, console_type, host, port,
                          internal_access_path, instance_uuid):
        method_kwargs = {'token': token,
                         'console_type': console_type,
                         'host': host,
                         'port': port,
                         'internal_access_path': internal_access_path,
                         'instance_uuid': instance_uuid}
        message = _TargetedMessage(self, ctxt, 'authorize_console',
                                   method_kwargs, 'down',
                                   cell_name, need_response=True)
        return message.process()

    def bdm_update_or_create_at_top(self, ctxt, bdm, create=None):
        """Update/Create a BDM at top level cell."""
        message = _BroadcastMessage(self, ctxt,
                                    'bdm_update_or_create_at_top',
                                    dict(bdm=bdm, create=create),
                                    'up', run_locally=False)
        message.process()

    def bdm_destroy_at_top(self, ctxt, instance_uuid, device_name=None,
                           volume_id=None):
        """Destroy a BDM at top level cell."""
        method_kwargs = dict(instance_uuid=instance_uuid,
                             device_name=device_name,
                             volume_id=volume_id)
        message = _BroadcastMessage(self, ctxt, 'bdm_destroy_at_top',
                                    method_kwargs,
                                    'up', run_locally=False)
        message.process()

    def _security_group_create_or_destroy(self, ctxt, group, create=True):
        """Create a special message for adding security groups which
        sends by name and project_id which is unique,
        id can get out of sync between child/parent cells"""
        security_group_dict = dict(group.iteritems())
        remove = ['id', 'deleted', 'created_at', 'updated_at', 'deleted_at']
        for item in remove:
            if item in security_group_dict:
                security_group_dict.pop(item)
        if create:
            method = 'security_group_create'
        else:
            method = 'security_group_destroy'
        message = _BroadcastMessage(self, ctxt, method,
                                    dict(group=security_group_dict),
                                    'down', run_locally=False)
        message.process()

    def security_group_create(self, ctxt, group):
        self._security_group_create_or_destroy(ctxt, group, create=True)

    def security_group_destroy(self, ctxt, group):
        self._security_group_create_or_destroy(ctxt, group, create=False)

    def _security_group_rule_create_or_destroy(self, ctxt, group, rule,
                                               create=True):
        """Create a special message for adding security group rules which
        sends unique information about a parent group rather than the id,
        which can get out of sync between child/parent cells"""
        security_group_rule_dict = dict(rule.iteritems())
        linked_id = security_group_rule_dict.pop('group_id', None)
        remove = ['id', 'deleted', 'created_at', 'updated_at',
                  'deleted_at', 'group_id', 'parent_group_id']
        for item in remove:
            if item in security_group_rule_dict:
                security_group_rule_dict.pop(item)

        security_group_rule_dict['parent_group_name'] = group['name']
        security_group_rule_dict['parent_group_pid'] = group['project_id']

        if linked_id:
            rd_ctxt = ctxt.elevated(read_deleted='yes')
            linked_group = db.security_group_get(rd_ctxt, linked_id)
            security_group_rule_dict['linked_group_name'] = linked_group.name
        if create:
            method = 'security_group_rule_create'
        else:
            method = 'security_group_rule_destroy'

        message = _BroadcastMessage(self, ctxt, method,
                                    dict(rule=security_group_rule_dict),
                                    'down', run_locally=False)
        message.process()

    def security_group_rule_create(self, ctxt, group, rule):
        self._security_group_rule_create_or_destroy(ctxt, group, rule,
                                                    create=True)

    def security_group_rule_destroy(self, ctxt, group, rule):
        self._security_group_rule_create_or_destroy(ctxt, group, rule,
                                                    create=False)

    def instance_add_security_group(self, ctxt, instance_uuid, group):
        method_kwargs = {'instance_uuid': instance_uuid, 'group': group}
        message = _BroadcastMessage(self, ctxt,
                                    'instance_add_security_group',
                                    method_kwargs, 'up', run_locally=False)
        message.process()

    def instance_remove_security_group(self, ctxt, instance_uuid, group):
        method_kwargs = {'instance_uuid': instance_uuid, 'group': group}
        message = _BroadcastMessage(self, ctxt,
                                    'instance_remove_security_group',
                                    method_kwargs, 'up', run_locally=False)
        message.process()

    def ec2_instance_create(self, ctxt, instance_uuid, ec2_id):
        method_kwargs = {'instance_uuid': instance_uuid, 'ec2_id': ec2_id}
        message = _BroadcastMessage(self, ctxt, 'ec2_instance_create',
                                    method_kwargs,
                                    'down', run_locally=False)
        message.process()

    def s3_image_create(self, ctxt, image_uuid, s3_id):
        method_kwargs = {'image_uuid': image_uuid, 's3_id': s3_id}
        message = _BroadcastMessage(self, ctxt, 's3_image_create',
                                    method_kwargs,
                                    'down', run_locally=False)
        message.process()

    def ec2_volume_create(self, ctxt, volume_uuid, ec2_id):
        method_kwargs = {'volume_uuid': volume_uuid, 'ec2_id': ec2_id}
        message = _BroadcastMessage(self, ctxt, 'ec2_volume_create',
                                    method_kwargs,
                                    'down', run_locally=False)
        message.process()

    def get_host_availability_zone(self, ctxt, cell_name, host):
        message = _TargetedMessage(self, ctxt, 'get_host_availability_zone',
                                   dict(host=host),
                                   'down', cell_name, need_response=True)
        return message.process()

    def instance_type_create(self, ctxt, cell_name, values):
        message = _TargetedMessage(self, ctxt, 'instance_type_create',
                                   dict(values=values),
                                   'down', cell_name, need_response=True)
        return message.process()

    @staticmethod
    def get_message_types():
        return _CELL_MESSAGE_TYPE_TO_MESSAGE_CLS.keys()


class Response(object):
    """Holds a response from a cell.  If there was a failure, 'failure'
    will be True and 'response' will contain an encoded Exception.
    """
    def __init__(self, cell_name, value, failure):
        self.failure = failure
        self.cell_name = cell_name
        self.value = value

    def to_json(self):
        resp_value = self.value
        if self.failure:
            resp_value = rpc_common.serialize_remote_exception(resp_value,
                    log_failure=False)
        _dict = {'cell_name': self.cell_name,
                 'value': resp_value,
                 'failure': self.failure}
        return jsonutils.dumps(_dict)

    @classmethod
    def from_json(cls, json_message):
        _dict = jsonutils.loads(json_message)
        if _dict['failure']:
            resp_value = rpc_common.deserialize_remote_exception(
                    CONF, _dict['value'])
            _dict['value'] = resp_value
        return cls(**_dict)

    def value_or_raise(self):
        if self.failure:
            if isinstance(self.value, (tuple, list)):
                raise self.value[0], self.value[1], self.value[2]
            else:
                raise self.value
        return self.value
