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
Cells Scheduler
"""
import copy
import time

from nova.cells import filters
from nova.cells import rpcapi as cells_rpcapi
from nova.cells import utils as cells_utils
from nova.cells import weights
from nova import compute
from nova.compute import vm_states
from nova.db import base
from nova import exception
from nova import flags
from nova.openstack.common import cfg
from nova.openstack.common import log as logging
from nova.openstack.common import rpc
from nova.scheduler import rpcapi as scheduler_rpcapi

flag_opts = [
        cfg.ListOpt('cell_scheduler_filters',
                default=['nova.cells.filters.standard_filters'],
                help='Filter classes the cells scheduler should use.  '
                        'An entry of "nova.cells.filters.standard_filters"'
                        'maps to all cells filters included with nova.'),
        cfg.ListOpt('cell_scheduler_weighters',
                default=['nova.cells.weights.standard_weighters'],
                help='Weighter classes the cells scheduler should use.  '
                        'An entry of "nova.cells.weights.standard_weighters"'
                        'maps to all cell weighters included with nova.'),
        cfg.IntOpt('cell_scheduler_retries',
                default=10,
                help='How many retries when no cells are available.'),
        cfg.IntOpt('cell_scheduler_retry_delay',
                default=2,
                help='How often to retry in seconds when no cells are '
                        'available.')
]

LOG = logging.getLogger(__name__)
FLAGS = flags.FLAGS
FLAGS.register_opts(flag_opts)


class CellsScheduler(base.Base):
    """The cells scheduler."""

    def __init__(self, manager):
        super(CellsScheduler, self).__init__()
        self.manager = manager
        self.cells_rpcapi = cells_rpcapi.CellsAPI()
        self.compute_api = compute.API()
        self.filter_classes = filters.get_filter_classes(
                FLAGS.cell_scheduler_filters)
        self.weighter_classes = weights.get_weighter_classes(
                FLAGS.cell_scheduler_weighters)

        self.scheduler_rpcapi = scheduler_rpcapi.SchedulerAPI()

    @property
    def our_path(self):
        """Our routing path part.  Needed for update_routing_path
        decorator to work.
        """
        return self.manager.our_path

    def _create_instance_here(self, context, request_spec, **kwargs):
        instance_values = request_spec['instance_properties']
        instance = self.compute_api.create_db_entry_for_new_instance(
                context,
                request_spec['instance_type'],
                request_spec['image'],
                instance_values,
                request_spec['security_group'],
                request_spec['block_device_mapping'])
        bcast_msg = cells_utils.form_instance_update_broadcast_message(
                instance)
        self.manager.broadcast_message(context, **bcast_msg['args'])

    def _get_possible_cells(self):
        cells = set(self.manager.get_child_cells())
        our_cell = self.manager.my_cell_info
        # Include our cell in the list, if we have any capacity info
        if not cells or our_cell.capacities:
            cells.add(our_cell)
        return cells

    def _filter_cells(self, cells, filter_properties):
        for filter_cls in self.filter_classes:
            filter_inst = filter_cls()
            fn = getattr(filter_inst, 'filter_cells')
            if not fn:
                continue
            filter_response = fn(cells, filter_properties)
            if not filter_response:
                continue
            if 'action' in filter_response:
                return filter_response
            if 'drop' in filter_response:
                for cell in filter_response.get('drop', []):
                    try:
                        cells.remove(cell)
                    except KeyError:
                        pass
        return None

    def _route_to_cell(self, context, cell_name, method, method_kwargs,
            routing_path=None):
        message = cells_utils.form_routing_message(
                cell_name, 'down', method, method_kwargs,
                routing_path=routing_path)
        self.manager.send_routing_message(context, cell_name, message)

    def _schedule_run_instance(self, context, routing_path, **kwargs):
        """Attempt to schedule an instance.  If we have no cells
        to try, raise exception.NoCellsAvailable
        """
        request_spec = kwargs['request_spec']

        # HACK(brian.lamar): We don't work with multiple instances being
        #                    created at the same time, this hack makes
        #                    sure the UUID is where we expect it to be
        instance_uuid = request_spec['instance_uuids'][0]
        request_spec['instance_properties']['uuid'] = instance_uuid

        fw_properties = copy.copy(kwargs.get('filter_properties', {}))
        fw_properties.update({'context': context,
                              'scheduler': self,
                              'routing_path': routing_path,
                              'request_spec': request_spec})

        LOG.debug(_("Scheduling with routing_path=%(routing_path)s"),
                locals(), instance_uuid=instance_uuid)

        fwd_msg = {'method': 'schedule_run_instance',
                   'args': kwargs}

        cells = self._get_possible_cells()
        filter_resp = self._filter_cells(cells, fw_properties)
        if filter_resp and 'action' in filter_resp:
            if filter_resp['action'] == 'direct_route':
                target = filter_resp['target']
                if target == cells_utils.path_without_hosts(routing_path):
                    # Ah, it's for me.
                    cells = [self.manager.my_cell_info]
                else:
                    self._route_to_cell(context, target,
                            'schedule_run_instance_direct', kwargs,
                            routing_path=routing_path)
                    return

        if not cells:
            raise exception.NoCellsAvailable()

        weighted_cells = weights.get_weighted_cells(self.weighter_classes,
                cells, fw_properties)
        LOG.debug(_("Weighted cells: %(weighted_cells)s"), locals(),
                instance_uuid=instance_uuid)

        # Keep trying until one works
        for weighted_cell in weighted_cells:
            cell = weighted_cell.cell
            try:
                if cell.is_me:
                    # Need to create instance DB entry as scheduler
                    # thinks it's already created... At least how things
                    # currently work.
                    self._create_instance_here(context, **kwargs)
                    fwd_msg['method'] = 'run_instance'

                    self._cast_to_scheduler(context, fwd_msg)
                else:
                    # Forward request to cell
                    fwd_msg['method'] = 'schedule_run_instance'
                    self.manager.cells_rpcapi.send_message_to_cell(context,
                            cell, fwd_msg)
                return
            except Exception:
                LOG.exception(_("Couldn't communicate with cell '%s'") %
                        cell.name)
        # FIXME(comstud)
        msg = _("Couldn't communicate with any cells")
        LOG.error(msg)
        raise exception.NoCellsAvailable()

    def _cast_to_scheduler(self, context, fwd_msg):
        # NOTE(belliott) Kinda hackish, but this runs the
        # message through the scheduler rpcapi and tags it
        # with a proper version:
        self.scheduler_rpcapi.run_instance(context,
                fwd_msg['args']['request_spec'],
                fwd_msg['args']['admin_password'],
                fwd_msg['args']['injected_files'],
                fwd_msg['args']['requested_networks'],
                fwd_msg['args']['is_first_time'],
                fwd_msg['args']['filter_properties'])
        #rpc.cast(context, FLAGS.scheduler_topic, fwd_msg)

    def schedule_run_instance_direct(self, context, routing_path, **kwargs):
        """Pick a cell where we should create a new instance.
        Called when a schedule method was direct-routed to this cell
        via 'route_message'.  This means routing_path is already updated.
        """
        try:
            for i in xrange(max(0, FLAGS.cell_scheduler_retries) + 1):
                try:
                    return self._schedule_run_instance(context,
                            routing_path, **kwargs)
                except exception.NoCellsAvailable:
                    if i == max(0, FLAGS.cell_scheduler_retries):
                        raise
                    sleep_time = max(1, FLAGS.cell_scheduler_retry_delay)
                    LOG.info(_("No cells available when scheduling.  Will "
                            "retry in %(sleep_time)s second(s)"), locals())
                    time.sleep(sleep_time)
                    continue
        except Exception:
            instance = kwargs['request_spec']['instance_properties']
            instance_uuid = instance['uuid']
            LOG.exception(_("Error scheduling"),
                    instance_uuid=instance_uuid)
            if self.manager.get_parent_cells():
                self.cells_rpcapi.instance_update(context,
                        {'uuid': instance_uuid,
                         'vm_state': vm_states.ERROR})
            else:
                self.db.instance_update(context,
                        instance_uuid,
                        {'vm_state': vm_states.ERROR})

    @cells_utils.update_routing_path
    def schedule_run_instance(self, context, routing_path, **kwargs):
        """Pick a cell where we should create a new instance.

        Called when a parent cell (or the OS API) has requested
        a new build.  A decorator updates the routing_path for us.
        """
        return self.schedule_run_instance_direct(context, routing_path,
                **kwargs)
