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
Utility methods for Cells.
"""

from nova import exception
from nova import flags
from nova.openstack.common import log as logging

FLAGS = flags.FLAGS
LOG = logging.getLogger('nova.cells.common')

PATH_CELL_SEP = '!'
PATH_CELL_HOST_SEP = '@'


def form_routing_message(dest_cell_name, direction, method,
        method_kwargs, need_response=False, response_uuid=None,
        routing_path=None):
    """Create a routing message."""

    args = {'dest_cell_name': dest_cell_name,
            'routing_path': routing_path,
            'direction': direction,
            'message': {'method': method, 'args': method_kwargs}}
    if need_response:
        args['need_response'] = True
    if response_uuid:
        args['response_uuid'] = response_uuid
    return {'method': 'route_message', 'args': args}


def form_broadcast_call_message(direction, method,
        method_kwargs, response_uuid=None, routing_path=None):
    """Create a broadcast message which requires a response."""
    args = {'routing_path': routing_path,
            'direction': direction,
            'message': {'method': method, 'args': method_kwargs}}
    if response_uuid:
        args['response_uuid'] = response_uuid
    return {'method': 'broadcast_call', 'args': args}


def form_broadcast_message(direction, method, method_kwargs,
        routing_path=None, hopcount=0, fanout=False):
    """Create a broadcast message."""
    args = {'direction': direction,
            'message': {'method': method, 'args': method_kwargs},
            'routing_path': routing_path,
            'hopcount': hopcount,
            'fanout': fanout}
    return {'method': 'broadcast_message', 'args': args}


def form_instance_update_broadcast_message(instance, routing_path=None,
        hopcount=0):
    instance = dict(instance.iteritems())
    # Remove things that we can't update in the parent.  'cell_name'
    # is included in this list.. because it'll always be empty in
    # a child zone... and we don't want it to overwrite the column
    # in the parent.
    items_to_remove = ['id', 'security_groups', 'instance_type',
            'volumes', 'cell_name', 'name', 'metadata']
    for key in items_to_remove:
        instance.pop(key, None)
    # Fixup info_cache
    info_cache = instance.pop('info_cache', None)
    if info_cache is not None:
        instance['info_cache'] = dict(info_cache.iteritems())
        instance['info_cache'].pop('id', None)
        instance['info_cache'].pop('instance', None)

    # Fixup system_metadata (should be a dict for update, not a list)
    if ('system_metadata' in instance and
            isinstance(instance['system_metadata'], list)):
        sys_metadata = dict([(md['key'], md['value'])
                for md in instance['system_metadata']])
        instance['system_metadata'] = sys_metadata

    return form_broadcast_message('up', 'instance_update',
            {'instance_info': instance},
            routing_path=routing_path, hopcount=hopcount)


def form_instance_destroy_broadcast_message(instance, routing_path=None,
        hopcount=0):
    instance_info = {'uuid': instance['uuid']}
    return form_broadcast_message('up', 'instance_destroy',
            {'instance_info': instance_info},
            routing_path=routing_path, hopcount=hopcount)


def form_fault_create_broadcast_message(fault, routing_path=None,
        hopcount=0):
    fault = dict(fault.iteritems())
    items_to_remove = ['id']
    for key in items_to_remove:
        fault.pop(key, None)

    return form_broadcast_message('up', 'instance_fault_create',
            {'fault_info': fault},
            routing_path=routing_path, hopcount=hopcount)


def reverse_path(path):
    """Reverse a path.  Used for sending responses upstream."""
    path_parts = path.split(PATH_CELL_SEP)
    path_parts.reverse()
    return PATH_CELL_SEP.join(path_parts)


def path_without_hosts(routing_path):
    """Return a path without host information."""
    def strip_host(path_part):
        if PATH_CELL_HOST_SEP in path_part:
            return path_part.split(PATH_CELL_HOST_SEP, 1)[0]
        else:
            return path_part
    return PATH_CELL_SEP.join([strip_host(path_part)
            for path_part in routing_path.split(PATH_CELL_SEP)])


def response_cell_name_from_path(routing_path):
    """Reverse the routing_path, stripping all hosts from it except for
    the last one.  This will be used as the destination cell for a
    response.
    """
    path_parts = reverse_path(routing_path).split(PATH_CELL_SEP)
    if len(path_parts) == 1:
        # The reverse is the path.
        return routing_path
    all_but_last_part = PATH_CELL_SEP.join(path_parts[:-1])
    last_part = path_parts[-1]
    return path_without_hosts(all_but_last_part) + PATH_CELL_SEP + last_part


def cell_name_for_next_hop(dest_cell_name, routing_path):
    """Return the (cell, host) for the next routing hop.

    The next hop might be ourselves if this is where the message is
    supposed to go.  (None, None) is returned in this case.
    """
    if (path_without_hosts(dest_cell_name) ==
            path_without_hosts(routing_path)):
        return (None, None)
    current_hops = routing_path.count(PATH_CELL_SEP)
    next_hop_num = current_hops + 1
    dest_hops = dest_cell_name.count(PATH_CELL_SEP)
    if dest_hops < current_hops:
        reason = _("destination is %(dest_cell_name)s but routing_path "
                "is %(routing_path)s") % locals()
        raise exception.CellRoutingInconsistency(reason=reason)
    dest_name_parts = dest_cell_name.split(PATH_CELL_SEP)
    if (PATH_CELL_SEP.join(dest_name_parts[:next_hop_num]) !=
            path_without_hosts(routing_path)):
        reason = _("destination is %(dest_cell_name)s but routing_path "
                "is %(routing_path)s") % locals()
        raise exception.CellRoutingInconsistency(reason=reason)
    next_hop_name = dest_name_parts[next_hop_num]
    host = None
    if PATH_CELL_HOST_SEP in next_hop_name:
        (next_hop_name, host) = next_hop_name.split(PATH_CELL_HOST_SEP, 1)
    return (next_hop_name, host)


def update_routing_path(fn):
    """Decorate a method and update its routing_path kwarg,
    adding our cell_name!hostname
    """
    def wrapper(self, *args, **kwargs):
        routing_path = kwargs.get('routing_path')
        routing_path = (routing_path and routing_path + PATH_CELL_SEP or '')
        kwargs['routing_path'] = routing_path + self.our_path
        return fn(self, *args, **kwargs)
    wrapper.__name__ = fn.__name__
    return wrapper
