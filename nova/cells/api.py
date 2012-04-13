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
from nova.openstack.common import log as logging
from nova.openstack.common import rpc

LOG = logging.getLogger('nova.cells.api')

FLAGS = flags.FLAGS


def cell_call(context, cell_name, method, **kwargs):
    """Route a call to a specific cell."""
    routing_message = cells_utils.form_routing_message(cell_name,
            'down', method, kwargs, need_response=True)
    return rpc.call(context, FLAGS.cells_topic, routing_message)


def cell_cast(context, cell_name, method, **kwargs):
    """Route a cast to a specific cell."""
    routing_message = cells_utils.form_routing_message(cell_name,
            'down', method, kwargs)
    rpc.cast(context, FLAGS.cells_topic, routing_message)


def cell_broadcast(context, direction, method, **kwargs):
    """Route a cast to a specific cell."""
    bcast_message = cells_utils.form_broadcast_message(direction, method,
            kwargs)
    rpc.cast(context, FLAGS.cells_topic, bcast_message)


def cell_broadcast_call(context, direction, method, **kwargs):
    bcast_message = cells_utils.form_broadcast_call_message(direction, method,
            kwargs)
    return rpc.call(context, FLAGS.cells_topic, bcast_message)


def broadcast_service_api_method(context, service_name, method, *args,
        **kwargs):
    """Encapsulate a call to a service API within a broadcast message"""

    method_info = {'method': method,
                   'method_args': args,
                   'method_kwargs': kwargs}
    cell_broadcast(context, 'down', 'run_service_api_method',
            service_name=service_name, method_info=method_info,
            is_broadcast=True)


def cast_service_api_method(context, cell_name, service_name, method,
        *args, **kwargs):
    """Encapsulate a call to a service API within a routing call"""

    method_info = {'method': method,
                   'method_args': args,
                   'method_kwargs': kwargs}
    cell_cast(context, cell_name, 'run_service_api_method',
            service_name=service_name, method_info=method_info,
            is_broadcast=False)


def call_service_api_method(context, cell_name, service_name, method,
        *args, **kwargs):
    """Encapsulate a call to a service API within a routing call"""

    method_info = {'method': method,
                   'method_args': args,
                   'method_kwargs': kwargs}
    return cell_call(context, cell_name, 'run_service_api_method',
            service_name=service_name, method_info=method_info,
            is_broadcast=False)


def schedule_run_instance(context, **kwargs):
    """Schedule a new instance for creation."""
    message = {'method': 'schedule_run_instance',
               'args': kwargs}
    rpc.cast(context, FLAGS.cells_topic, message)


def call_dbapi_method(context, method, args, kwargs=None, sub_topic=None):
    """Broadcast message up, saying to call a DB API method."""
    if kwargs is None:
        kwargs = {}
    db_method_info = {'method': method,
                      'method_args': args,
                      'method_kwargs': kwargs}
    bcast_message = cells_utils.form_broadcast_message('up',
            'call_dbapi_method',
            {'db_method_info': db_method_info})
    topic = FLAGS.cells_topic
    if sub_topic:
        topic += '.' + sub_topic
    rpc.cast(context, topic, bcast_message)


def instance_update(context, instance):
    """Broadcast upwards that an instance was updated."""
    if not FLAGS.enable_cells:
        return
    bcast_message = cells_utils.form_instance_update_broadcast_message(
            instance)
    rpc.cast(context, FLAGS.cells_topic, bcast_message)


def instance_destroy(context, instance):
    """Broadcast upwards that an instance was destroyed."""
    if not FLAGS.enable_cells:
        return
    bcast_message = cells_utils.form_instance_destroy_broadcast_message(
            instance)
    rpc.cast(context, FLAGS.cells_topic, bcast_message)


def instance_fault_create(context, instance_fault):
    """Broadcast upwards that an instance fault was created."""
    if not FLAGS.enable_cells:
        return
    instance_fault = dict(instance_fault.iteritems())
    items_to_remove = ['id']
    for key in items_to_remove:
        instance_fault.pop(key, None)
    call_dbapi_method(context, 'instance_fault_create',
            (instance_fault, ))


def bw_usage_update(context, *args, **kwargs):
    """Broadcast upwards that bw_usage was updated."""
    if not FLAGS.enable_cells:
        return
    call_dbapi_method(context, 'bw_usage_update',
            args, kwargs=kwargs, sub_topic='bw_updates')


def instance_metadata_update(context, *args, **kwargs):
    """Broadcast upwards that bw_usage was updated."""
    if not FLAGS.enable_cells:
        return
    call_dbapi_method(context, 'instance_metadata_update', args,
            kwargs=kwargs)


def get_all_cell_info(context):
    """Get the list of cells and their information from the manager."""
    msg = {'method': 'get_cell_info',
           'args': {}}
    return rpc.call(context, FLAGS.cells_topic, msg)


def sync_instances(context, project_id=None, updated_since=None,
        deleted=False):
    """Broadcast message down to tell cells to sync instance data."""
    if not FLAGS.enable_cells:
        return
    bcast_message = cells_utils.form_broadcast_message('down',
            'sync_instances', {'project_id': project_id,
                               'updated_since': updated_since,
                               'deleted': deleted})
    rpc.cast(context, FLAGS.cells_topic, bcast_message)
