from nova.cells.filters import BaseCellFilter
import logging

LOG = logging.getLogger(__name__)

class PickCellFilter(BaseCellFilter):
    def filter_cells(self, cells, filter_properties):
        LOG.info(_("Filtering cells for a specific cell name"))
        scheduler_hints = filter_properties.get('scheduler_hints', None)
        cell_name = scheduler_hints.get('use_cell', None)
        if not cell_name:
            return {}
        # remove this as once the call reaches the 
        # specified top cell, we want to proceed
        # using normal scheduling
        scheduler_hints.pop('use_cell')
        
        # FIXME: cell name will come in hyphen separated
        # from the CLI as bangs break it. This should be
        # massaged out at a higher level
        cell_name = cell_name.replace('-', '!')

        LOG.info(_("Filtering for cell '%(cell_name)s'"), locals())
        resp = {
            'action': 'direct_route',
            'target': cell_name,
            }
        return resp
