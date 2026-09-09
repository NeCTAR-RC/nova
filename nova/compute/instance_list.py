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

import copy

import eventlet
from oslo_log import log as logging

from nova.compute import multi_cell_list
import nova.conf
from nova import context
from nova.db.main import api as db
from nova import exception
from nova import objects
from nova.objects import instance as instance_obj


CONF = nova.conf.CONF
LOG = logging.getLogger(__name__)


class InstanceSortContext(multi_cell_list.RecordSortContext):
    def __init__(self, sort_keys, sort_dirs):
        if not sort_keys:
            sort_keys = ['created_at', 'id']
            sort_dirs = ['desc', 'desc']

        if 'uuid' not in sort_keys:
            # Historically the default sort includes 'id' (see above), which
            # should give us a stable ordering. Since we're striping across
            # cell databases here, many sort_keys arrangements will yield
            # nothing unique across all the databases to give us a stable
            # ordering, which can mess up expected client pagination behavior.
            # So, throw uuid into the sort_keys at the end if it's not already
            # there to keep us repeatable.
            sort_keys = copy.copy(sort_keys) + ['uuid']
            sort_dirs = copy.copy(sort_dirs) + ['asc']

        super(InstanceSortContext, self).__init__(sort_keys, sort_dirs)


class InstanceLister(multi_cell_list.CrossCellLister):
    def __init__(self, sort_keys, sort_dirs, cells=None, batch_size=None):
        super(InstanceLister, self).__init__(
            InstanceSortContext(sort_keys, sort_dirs), cells=cells,
            batch_size=batch_size)

    @property
    def marker_identifier(self):
        return 'uuid'

    def get_marker_record(self, ctx, marker):
        try:
            im = objects.InstanceMapping.get_by_instance_uuid(ctx, marker)
        except exception.InstanceMappingNotFound:
            raise exception.MarkerNotFound(marker=marker)

        if im.cell_mapping is None:
            # Mapped, but to no cell: the instance was never scheduled or
            # its record was purged from under the mapping. There is no
            # cell to look in, so this is a plain miss. (The unguarded
            # target_cell(None) that used to run here fell through to the
            # untargeted database and reported the same miss by luck.)
            raise exception.MarkerNotFound(marker=marker)

        cell_uuid = im.cell_mapping.uuid
        # NOTE(shiyan): This lookup targets the marker's cell directly,
        # before any scatter-gather runs, so the circuit breaker never
        # sees it: gate and bound it here the same way. Unbounded, a
        # paginated listing whose marker lives in a down cell holds an
        # API worker for the whole DB reconnect ladder - the exact
        # worker-burn the breaker exists to prevent. An unreachable cell
        # must surface as CellTimeout (a retryable 503), never as
        # MarkerNotFound: that becomes a 400 wrongly telling the client
        # the marker it was handed on the previous page is invalid.
        if context._should_skip_cell(cell_uuid):
            LOG.warning('Cell %s is inside its failure hold window and '
                        'holds pagination marker %s; failing the request',
                        cell_uuid, marker)
            raise exception.CellTimeout()

        elevated = ctx.elevated(read_deleted='yes')
        with context.target_cell(elevated, im.cell_mapping) as cctx:
            try:
                with eventlet.timeout.Timeout(context.get_cell_timeout(),
                                              exception.CellTimeout):
                    # NOTE(danms): We query this with no columns_to_join()
                    # as we're just getting values for the sort keys from
                    # it and none of the valid sort keys are on joined
                    # columns.
                    db_inst = db.instance_get_by_uuid(cctx, marker,
                                                      columns_to_join=[])
            except exception.InstanceNotFound:
                # The cell answered (with a miss). Report health: this
                # request may be the elected probe for the cell, which
                # must not be left dangling in the probing state.
                context._mark_cell_healthy(cell_uuid)
                raise exception.MarkerNotFound(marker=marker)
            except context._CELL_DOWN_EXCEPTIONS:
                context._mark_cell_failed(cell_uuid)
                LOG.warning('Cell %s timed out or errored while looking '
                            'up pagination marker %s; failing the request',
                            cell_uuid, marker)
                raise exception.CellTimeout()
        context._mark_cell_healthy(cell_uuid)
        return cell_uuid, db_inst

    def get_marker_by_values(self, ctx, values):
        return db.instance_get_by_sort_filters(ctx,
                                               self.sort_ctx.sort_keys,
                                               self.sort_ctx.sort_dirs,
                                               values)

    def get_by_filters(self, ctx, filters, limit, marker, **kwargs):
        return db.instance_get_all_by_filters_sort(
            ctx, filters, limit=limit, marker=marker,
            sort_keys=self.sort_ctx.sort_keys,
            sort_dirs=self.sort_ctx.sort_dirs,
            **kwargs)


# NOTE(danms): These methods are here for legacy glue reasons. We should not
# replicate these for every data type we implement.
def get_instances_sorted(ctx, filters, limit, marker, columns_to_join,
                         sort_keys, sort_dirs, cell_mappings=None,
                         batch_size=None, cell_down_support=False):
    instance_lister = InstanceLister(sort_keys, sort_dirs,
                                     cells=cell_mappings,
                                     batch_size=batch_size)
    instance_generator = instance_lister.get_records_sorted(
        ctx, filters, limit, marker, columns_to_join=columns_to_join,
        cell_down_support=cell_down_support)
    return instance_lister, instance_generator


def get_instance_list_cells_batch_size(limit, cells):
    """Calculate the proper batch size for a list request.

    This will consider config, request limit, and cells being queried and
    return an appropriate batch size to use for querying said cells.

    :param limit: The overall limit specified in the request
    :param cells: The list of CellMapping objects being queried
    :returns: An integer batch size
    """
    strategy = CONF.api.instance_list_cells_batch_strategy
    limit = limit or CONF.api.max_limit

    if len(cells) <= 1:
        # If we're limited to one (or no) cell for whatever reason, do
        # not do any batching and just pull the desired limit from the
        # single cell in one shot.
        return limit

    if strategy == 'fixed':
        # Fixed strategy, always a static batch size
        batch_size = CONF.api.instance_list_cells_batch_fixed_size
    elif strategy == 'distributed':
        # Distributed strategy, 10% more than even partitioning
        batch_size = int((limit / len(cells)) * 1.10)

    # We never query a larger batch than the total requested, and never
    # smaller than the lower limit of 100.
    return max(min(batch_size, limit), 100)


def get_instance_objects_sorted(ctx, filters, limit, marker, expected_attrs,
                                sort_keys, sort_dirs, cell_down_support=False):
    """Return a list of instances and information about down cells.

    This returns a tuple of (objects.InstanceList, list(of down cell
    uuids) for the requested operation. The instances returned are
    those that were collected from the cells that responded. The uuids
    of any cells that did not respond (or raised an error) are included
    in the list as the second element of the tuple. That list is empty
    if all cells responded.
    """
    query_cell_subset = CONF.api.instance_list_per_project_cells
    # NOTE(danms): Replicated in part from instance_get_all_by_sort_filters(),
    # where if we're not admin we're restricted to our context's project
    if query_cell_subset and not ctx.is_admin:
        # We are not admin, and configured to only query the subset of cells
        # we could possibly have instances in.
        cell_mappings = objects.CellMappingList.get_by_project_id(
            ctx, ctx.project_id)
    else:
        # Either we are admin, or configured to always hit all cells,
        # so don't limit the list to a subset.
        context.load_cells()
        cell_mappings = context.CELLS

    batch_size = get_instance_list_cells_batch_size(limit, cell_mappings)

    columns_to_join = instance_obj._expected_cols(expected_attrs)
    instance_lister, instance_generator = get_instances_sorted(ctx, filters,
        limit, marker, columns_to_join, sort_keys, sort_dirs,
        cell_mappings=cell_mappings, batch_size=batch_size,
        cell_down_support=cell_down_support)

    if 'fault' in expected_attrs:
        # We join fault above, so we need to make sure we don't ask
        # make_instance_list to do it again for us
        expected_attrs = copy.copy(expected_attrs)
        expected_attrs.remove('fault')

    instance_list = instance_obj._make_instance_list(ctx,
        objects.InstanceList(), instance_generator, expected_attrs)
    down_cell_uuids = (instance_lister.cells_failed +
                       instance_lister.cells_timed_out)
    return instance_list, down_cell_uuids
