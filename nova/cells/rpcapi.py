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
Client side of nova-cells RPC API (for talking to the nova-cells service
within a cell).

This is different than communication between child and parent nova-cells
services.  That communication is handled by the cells driver via the
messging module.
"""

from oslo.config import cfg

from nova import exception
from nova.openstack.common import jsonutils
from nova.openstack.common import log as logging
from nova.openstack.common.rpc import proxy as rpc_proxy

LOG = logging.getLogger(__name__)

LOG = logging.getLogger(__name__)
CONF = cfg.CONF
CONF.import_opt('enable', 'nova.cells.opts', group='cells')
CONF.import_opt('topic', 'nova.cells.opts', group='cells')


class CellsAPI(rpc_proxy.RpcProxy):
    '''Cells client-side RPC API

    API version history:

        1.0 - Initial version.
        1.1 - Adds get_cell_info_for_neighbors() and sync_instances()
        1.2 - Adds service_get_all(), service_get_by_compute_host(),
              and proxy_rpc_to_compute_manager()
        1.3 - Adds task_log_get_all()
        1.4 - Adds compute_node_get(), compute_node_get_all(), and
              compute_node_stats()
        1.5 - Adds actions_get(), action_get_by_request_id(), and
              action_events_get()
        1.6 - Adds consoleauth_delete_tokens() and validate_console_port()
        1.6.1 - Adds create_aggregate(), get_aggregate(), get_aggregate_list(),
              update_aggregate(), update_aggregate_metadata(),
              delete_aggregate(), add_host_to_aggregate(),
              and remove_host_from_aggregate()
    '''
    BASE_RPC_API_VERSION = '1.0'

    def __init__(self):
        super(CellsAPI, self).__init__(topic=CONF.cells.topic,
                default_version=self.BASE_RPC_API_VERSION)

    def cast_compute_api_method(self, ctxt, cell_name, method,
            *args, **kwargs):
        """Make a cast to a compute API method in a certain cell."""
        method_info = {'method': method,
                       'method_args': args,
                       'method_kwargs': kwargs}
        self.cast(ctxt, self.make_msg('run_compute_api_method',
                                      cell_name=cell_name,
                                      method_info=method_info,
                                      call=False))

    def call_compute_api_method(self, ctxt, cell_name, method,
            *args, **kwargs):
        """Make a call to a compute API method in a certain cell."""
        method_info = {'method': method,
                       'method_args': args,
                       'method_kwargs': kwargs}
        return self.call(ctxt, self.make_msg('run_compute_api_method',
                                             cell_name=cell_name,
                                             method_info=method_info,
                                             call=True))

    def cast_securitygroup_api_method(self, ctxt, cell_name, method,
            *args, **kwargs):
        """Make a cast to a securitygroup API method in a certain cell."""
        method_info = {'method': method,
                       'method_args': args,
                       'method_kwargs': kwargs}
        self.cast(ctxt, self.make_msg('run_securitygroup_api_method',
                                      cell_name=cell_name,
                                      method_info=method_info,
                                      call=False))

    def schedule_run_instance(self, ctxt, **kwargs):
        """Schedule a new instance for creation."""
        self.cast(ctxt, self.make_msg('schedule_run_instance',
                                      host_sched_kwargs=kwargs))

    def instance_update_at_top(self, ctxt, instance):
        """Update instance at API level."""
        if not CONF.cells.enable:
            return
        # Make sure we have a dict, not a SQLAlchemy model
        instance_p = jsonutils.to_primitive(instance)
        self.cast(ctxt, self.make_msg('instance_update_at_top',
                                      instance=instance_p))

    def instance_destroy_at_top(self, ctxt, instance):
        """Destroy instance at API level."""
        if not CONF.cells.enable:
            return
        instance_p = jsonutils.to_primitive(instance)
        self.cast(ctxt, self.make_msg('instance_destroy_at_top',
                                      instance=instance_p))

    def instance_delete_everywhere(self, ctxt, instance, delete_type):
        """Delete instance everywhere.  delete_type may be 'soft'
        or 'hard'.  This is generally only used to resolve races
        when API cell doesn't know to what cell an instance belongs.
        """
        if not CONF.cells.enable:
            return
        instance_p = jsonutils.to_primitive(instance)
        self.cast(ctxt, self.make_msg('instance_delete_everywhere',
                                      instance=instance_p,
                                      delete_type=delete_type))

    def instance_fault_create_at_top(self, ctxt, instance_fault):
        """Create an instance fault at the top."""
        if not CONF.cells.enable:
            return
        instance_fault_p = jsonutils.to_primitive(instance_fault)
        self.cast(ctxt, self.make_msg('instance_fault_create_at_top',
                                      instance_fault=instance_fault_p))

    def bw_usage_update_at_top(self, ctxt, uuid, mac, start_period,
            bw_in, bw_out, last_ctr_in, last_ctr_out, last_refreshed=None):
        """Broadcast upwards that bw_usage was updated."""
        if not CONF.cells.enable:
            return
        bw_update_info = {'uuid': uuid,
                          'mac': mac,
                          'start_period': start_period,
                          'bw_in': bw_in,
                          'bw_out': bw_out,
                          'last_ctr_in': last_ctr_in,
                          'last_ctr_out': last_ctr_out,
                          'last_refreshed': last_refreshed}
        self.cast(ctxt, self.make_msg('bw_usage_update_at_top',
                                      bw_update_info=bw_update_info))

    def instance_info_cache_update_at_top(self, ctxt, instance_info_cache):
        """Broadcast up that an instance's info_cache has changed."""
        if not CONF.cells.enable:
            return
        iicache = jsonutils.to_primitive(instance_info_cache)
        instance = {'uuid': iicache['instance_uuid'],
                    'info_cache': iicache}
        self.cast(ctxt, self.make_msg('instance_update_at_top',
                                      instance=instance))

    def get_cell_info_for_neighbors(self, ctxt):
        """Get information about our neighbor cells from the manager."""
        if not CONF.cells.enable:
            return []
        return self.call(ctxt, self.make_msg('get_cell_info_for_neighbors'),
                         version='1.1')

    def sync_instances(self, ctxt, project_id=None, updated_since=None,
            deleted=False):
        """Ask all cells to sync instance data."""
        if not CONF.cells.enable:
            return
        return self.cast(ctxt, self.make_msg('sync_instances',
                                             project_id=project_id,
                                             updated_since=updated_since,
                                             deleted=deleted),
                         version='1.1')

    def service_get_all(self, ctxt, filters=None):
        """Ask all cells for their list of services."""
        return self.call(ctxt,
                         self.make_msg('service_get_all',
                                       filters=filters),
                         version='1.2')

    def service_get_by_compute_host(self, ctxt, host_name):
        """Get the service entry for a host in a particular cell.  The
        cell name should be encoded within the host_name.
        """
        return self.call(ctxt, self.make_msg('service_get_by_compute_host',
                                             host_name=host_name),
                         version='1.2')

    def proxy_rpc_to_manager(self, ctxt, rpc_message, topic, call=False,
                             timeout=None):
        """Proxy RPC to a compute manager.  The host in the topic
        should be encoded with the target cell name.
        """
        return self.call(ctxt, self.make_msg('proxy_rpc_to_manager',
                                             topic=topic,
                                             rpc_message=rpc_message,
                                             call=call,
                                             timeout=timeout),
                         timeout=timeout,
                         version='1.2')

    def task_log_get_all(self, ctxt, task_name, period_beginning,
                         period_ending, host=None, state=None):
        """Get the task logs from the DB in child cells."""
        return self.call(ctxt, self.make_msg('task_log_get_all',
                                   task_name=task_name,
                                   period_beginning=period_beginning,
                                   period_ending=period_ending,
                                   host=host, state=state),
                         version='1.3')

    def compute_node_get(self, ctxt, compute_id):
        """Get a compute node by ID in a specific cell."""
        return self.call(ctxt, self.make_msg('compute_node_get',
                                             compute_id=compute_id),
                         version='1.4')

    def compute_node_get_all(self, ctxt, hypervisor_match=None):
        """Return list of compute nodes in all cells, optionally
        filtering by hypervisor host.
        """
        return self.call(ctxt,
                         self.make_msg('compute_node_get_all',
                                       hypervisor_match=hypervisor_match),
                         version='1.4')

    def compute_node_stats(self, ctxt):
        """Return compute node stats from all cells."""
        return self.call(ctxt, self.make_msg('compute_node_stats'),
                         version='1.4')

    def actions_get(self, ctxt, instance):
        if not instance['cell_name']:
            raise exception.InstanceUnknownCell(instance_uuid=instance['uuid'])
        return self.call(ctxt, self.make_msg('actions_get',
                                             cell_name=instance['cell_name'],
                                             instance_uuid=instance['uuid']),
                         version='1.5')

    def action_get_by_request_id(self, ctxt, instance, request_id):
        if not instance['cell_name']:
            raise exception.InstanceUnknownCell(instance_uuid=instance['uuid'])
        return self.call(ctxt, self.make_msg('action_get_by_request_id',
                                             cell_name=instance['cell_name'],
                                             instance_uuid=instance['uuid'],
                                             request_id=request_id),
                         version='1.5')

    def action_events_get(self, ctxt, instance, action_id):
        if not instance['cell_name']:
            raise exception.InstanceUnknownCell(instance_uuid=instance['uuid'])
        return self.call(ctxt, self.make_msg('action_events_get',
                                             cell_name=instance['cell_name'],
                                             action_id=action_id),
                         version='1.5')

    def consoleauth_delete_tokens(self, ctxt, instance_uuid):
        """Delete consoleauth tokens for an instance in API cells."""
        self.cast(ctxt, self.make_msg('consoleauth_delete_tokens',
                                      instance_uuid=instance_uuid),
                  version='1.6')

    def validate_console_port(self, ctxt, instance_uuid, console_port,
                              console_type):
        """Validate console port with child cell compute node."""
        return self.call(ctxt,
                self.make_msg('validate_console_port',
                              instance_uuid=instance_uuid,
                              console_port=console_port,
                              console_type=console_type),
                version='1.6')

    def create_aggregate(self, ctxt, cell_name,
                         aggregate_name, availability_zone):
        return self.call(ctxt,
                self.make_msg('create_aggregate',
                              cell_name=cell_name,
                              aggregate_name=aggregate_name,
                              availability_zone=availability_zone),
                version='1.6.1')

    def get_aggregate(self, ctxt, cell_name, aggregate_id):
        return self.call(ctxt,
                self.make_msg('get_aggregate',
                              cell_name=cell_name,
                              aggregate_id=aggregate_id),
                version='1.6.1')

    def get_aggregate_list(self, ctxt, cell_name=None):
        return self.call(ctxt,
                self.make_msg('get_aggregate_list', cell_name=cell_name),
                version='1.6.1')

    def update_aggregate(self, ctxt, cell_name, aggregate_id, values):
        return self.call(ctxt,
                self.make_msg('update_aggregate', cell_name=cell_name,
                              aggregate_id=aggregate_id,
                              values=values),
                version='1.6.1')

    def update_aggregate_metadata(self, ctxt, cell_name,
                                  aggregate_id, metadata):
        return self.call(ctxt,
                self.make_msg('update_aggregate_metadata',
                              cell_name=cell_name,
                              aggregate_id=aggregate_id,
                              metadata=metadata),
                version='1.6.1')

    def delete_aggregate(self, ctxt, cell_name, aggregate_id):
        self.cast(ctxt,
                  self.make_msg('delete_aggregate',
                                cell_name=cell_name,
                                aggregate_id=aggregate_id),
                  version='1.6.1')

    def add_host_to_aggregate(self, ctxt, cell_name, aggregate_id, host_name):
        return self.call(ctxt,
                self.make_msg('add_host_to_aggregate',
                              cell_name=cell_name,
                              aggregate_id=aggregate_id, host_name=host_name),
                version='1.6.1')

    def remove_host_from_aggregate(self, ctxt, cell_name,
                                  aggregate_id, host_name):
        return self.call(ctxt,
                self.make_msg('remove_host_from_aggregate',
                              cell_name=cell_name,
                              aggregate_id=aggregate_id,
                              host_name=host_name),
                version='1.6.1')

    def authorize_console(self, ctxt, cell_name, token, console_type, host, port,
                          internal_access_path, instance_uuid=None):
        # The remote side doesn't return anything, but we want to block
        # until it completes.
        return self.call(ctxt,
                self.make_msg('authorize_console', cell_name=cell_name,
                              token=token, console_type=console_type,
                              host=host, port=port,
                              internal_access_path=internal_access_path,
                              instance_uuid=instance_uuid),
                version="1.6.1")

    def bdm_update_or_create_at_top(self, ctxt, bdm, create=None):
        """Create or update a block device mapping in API cells.  If
        create is True, only try to create.  If create is None, try to
        update but fall back to create.  If create is False, only attempt
        to update.  This maps to nova-conductor's behavior.
        """
        if not CONF.cells.enable:
            return
        try:
            self.cast(ctxt, self.make_msg('bdm_update_or_create_at_top',
                                          bdm=bdm, create=create),
                      version='1.6.1')
        except Exception:
            LOG.exception(_("Failed to notify cells of BDM update/create."))

    def bdm_destroy_at_top(self, ctxt, instance_uuid, device_name=None,
                           volume_id=None):
        """Broadcast upwards that a block device mapping was destroyed.
        One of device_name or volume_id should be specified.
        """
        if not CONF.cells.enable:
            return
        try:
            self.cast(ctxt, self.make_msg('bdm_destroy_at_top',
                                          instance_uuid=instance_uuid,
                                          device_name=device_name,
                                          volume_id=volume_id),
                      version='1.6.1')
        except Exception:
            LOG.exception(_("Failed to notify cells of BDM destroy."))

    def security_group_create(self, ctxt, group):
        """Broadcast security group create request downward"""
        if not CONF.cells.enable:
            return
        group_p = jsonutils.to_primitive(group)
        self.cast(ctxt, self.make_msg('security_group_create', group=group_p),
                  version='1.6.1')

    def security_group_destroy(self, ctxt, group):
        """Broadcast security group destroy request downward"""
        if not CONF.cells.enable:
            return
        group_p = jsonutils.to_primitive(group)
        self.cast(ctxt, self.make_msg('security_group_destroy', group=group_p))

    def security_group_rule_create(self, ctxt, group, rule):
        """Broadcast security group rule create request downward"""
        if not CONF.cells.enable:
            return
        group_p = jsonutils.to_primitive(group)
        self.cast(ctxt, self.make_msg('security_group_rule_create',
                                      rule=rule, group=group_p),
                  version='1.6.1')

    def security_group_rule_destroy(self, ctxt, group, rule):
        """Broadcast security group rule create request downward"""
        if not CONF.cells.enable:
            return
        group_p = jsonutils.to_primitive(group)
        self.cast(ctxt, self.make_msg('security_group_rule_destroy',
                                      rule=rule, group=group_p),
                  version='1.6.1')

    def instance_add_security_group(self, ctxt, instance_uuid,
                                    security_group_id):
        """Broadcast security group instance association add upward"""
        if not CONF.cells.enable:
            return
        self.cast(ctxt, self.make_msg('instance_add_security_group',
                                      instance_uuid=instance_uuid,
                                      group_id=security_group_id),
                  version='1.6.1')

    def instance_remove_security_group(self, ctxt, instance_uuid,
                                       security_group_id):
        """Broadcast security group instance association remove upward"""
        if not CONF.cells.enable:
            return
        self.cast(ctxt, self.make_msg('instance_remove_security_group',
                                      instance_uuid=instance_uuid,
                                      group_id=security_group_id),
                  version='1.6.1')

    def ec2_instance_create(self, ctxt, instance_uuid, ec2_id):
        """Broadcast EC2 mappings downwards."""
        if not CONF.cells.enable:
            return
        self.cast(ctxt, self.make_msg('ec2_instance_create',
                                      instance_uuid=instance_uuid,
                                      ec2_id=ec2_id),
                  version='1.6.1')

    def s3_image_create(self, ctxt, image_uuid, s3_id):
        """Broadcast S3 mappings downwards."""
        if not CONF.cells.enable:
            return
        self.cast(ctxt, self.make_msg('s3_image_create',
                                      image_uuid=image_uuid,
                                      s3_id=s3_id),
                  version='1.6.1')

    def ec2_volume_create(self, ctxt, volume_uuid, ec2_id):
        """Broadcast EC2 mappings downwards."""
        if not CONF.cells.enable:
            return
        self.cast(ctxt, self.make_msg('ec2_volume_create',
                                      volume_uuid=volume_uuid,
                                      ec2_id=ec2_id),
                  version='1.6.1')

    def get_host_availability_zone(self, ctxt, cell_name, host):

        return self.call(ctxt,
                self.make_msg('get_host_availability_zone',
                              cell_name=cell_name, host=host),
                version='1.6.1')

    def instance_type_create(self, ctxt, cell_name, values):

        return self.call(ctxt,
                self.make_msg('instance_type_create',
                              cell_name=cell_name, values=values),
                version='1.6.1')
