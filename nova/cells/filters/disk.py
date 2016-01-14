from oslo_log import log as logging

from nova.cells import filters


LOG = logging.getLogger(__name__)


class DiskFilter(filters.BaseCellFilter):
    """Filters cells by availability zone.

    Works with cell capabilities using the key
    'availability_zones'
    Note: cell can have multiple availability zones
    """

    def cell_passes(self, cell, filter_properties):
        """Use the 'disk_free' for a particular instance_type advertised from a
        child cell's capacity to compute a weight.  We want to direct the
        build to a cell with a higher capacity.  Since higher weights win,
        we just return the number of units available for the instance_type.
        """
        LOG.debug('Filtering on disk for cell %s' % cell)
        request_spec = filter_properties['request_spec']
        props = request_spec.get('instance_properties', {})
        availability_zone = props.get('availability_zone')
        # If AZ specified then skip disk filter
        if availability_zone:
            return True

        instance_type = request_spec['instance_type']
        disk_needed = (instance_type['root_gb'] +
                       instance_type['ephemeral_gb']) * 1024
        LOG.debug('Disk space needed (MB): %s' % disk_needed)
        disk_free = cell.capacities.get('disk_free', {})
        units_by_mb = disk_free.get('units_by_mb', {})

        if units_by_mb.get(str(disk_needed), 0) < 2:
            return False

        return True
