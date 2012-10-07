from nova.cells.filters import BaseCellFilter
import logging

LOG = logging.getLogger(__name__)

class PickCellFilter(BaseCellFilter):
    def filter_cells(self, cells, filter_properties):
        LOG.info(_("Filtering cells for a specific cell name"))
        scheduler_hints = filter_properties.get('scheduler_hints', None)
        cell_name = scheduler_hints.get('use_cell', None)
        cell_name = cell_name.replace('-', '!')
        if cell_name:
            LOG.info(_("Filtering for cell '%(cell_name)s'"), locals())
            resp = {
                'action': 'direct_route',
                'target': cell_name,
                }
            return resp
        return []
