# vim: tabstop=4 shiftwidth=4 softtabstop=4

# Copyright 2011-2012 OpenStack LLC.
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

"""The cells extension."""
from xml.dom import minidom
from xml.parsers import expat

from webob import exc

from nova.api.openstack import common
from nova.api.openstack import extensions
from nova.api.openstack import wsgi
from nova.api.openstack import xmlutil
from nova.cells import rpcapi as cells_rpcapi
from nova.compute import api as compute
from nova import db
from nova import exception
from nova import flags
from nova.openstack.common import log as logging
from nova.openstack.common import timeutils


LOG = logging.getLogger("nova.api.openstack.compute.contrib.cells")
FLAGS = flags.FLAGS
authorize = extensions.extension_authorizer('compute', 'cells')


def make_cell(elem):
    elem.set('id')
    elem.set('name')
    elem.set('type')
    elem.set('rpc_host')
    elem.set('rpc_port')

    caps = xmlutil.SubTemplateElement(elem, 'capabilities',
            selector='capabilities')
    cap = xmlutil.SubTemplateElement(caps, xmlutil.Selector(0),
            selector=xmlutil.get_items)
    cap.text = 1


cell_nsmap = {None: wsgi.XMLNS_V10}


class CellTemplate(xmlutil.TemplateBuilder):
    def construct(self):
        root = xmlutil.TemplateElement('cell', selector='cell')
        make_cell(root)
        return xmlutil.MasterTemplate(root, 1, nsmap=cell_nsmap)


class CellsTemplate(xmlutil.TemplateBuilder):
    def construct(self):
        root = xmlutil.TemplateElement('cells')
        elem = xmlutil.SubTemplateElement(root, 'cell', selector='cells')
        make_cell(elem)
        return xmlutil.MasterTemplate(root, 1, nsmap=cell_nsmap)


class CellDeserializer(wsgi.XMLDeserializer):
    """Deserializer to handle xml-formatted cell create requests."""

    def _extract_capabilities(self, cap_node):
        caps = {}
        for cap in cap_node.childNodes:
            cap_name = cap.tagName
            caps[cap_name] = self.extract_text(cap)
        return caps

    def _extract_cell(self, node):
        cell = {}
        cell_node = self.find_first_child_named(node, 'cell')

        extract_fns = {'capabilities': self._extract_capabilities}

        for child in cell_node.childNodes:
            name = child.tagName
            extract_fn = extract_fns.get(name, self.extract_text)
            cell[name] = extract_fn(child)
        return cell

    def default(self, string):
        """Deserialize an xml-formatted cell create request"""
        try:
            node = minidom.parseString(string)
        except expat.ExpatError:
            msg = _("cannot understand XML")
            raise exception.MalformedRequestBody(reason=msg)

        return {'body': {'cell': self._extract_cell(node)}}


def _filter_keys(item, keys):
    """
    Filters all model attributes except for keys
    item is a dict

    """
    return dict((k, v) for k, v in item.iteritems() if k in keys)


def _scrub_cell(cell, detail=False):
    keys = ['id', 'name', 'username', 'rpc_host', 'rpc_port']
    if detail:
        keys.append('capabilities')

    cell_info = _filter_keys(cell, keys)
    cell_info['type'] = 'parent' if cell['is_parent'] else 'child'
    return cell_info


class Controller(object):
    """Controller for Cell resources."""

    def __init__(self):
        self.compute_api = compute.API()
        self.cells_rpcapi = cells_rpcapi.CellsAPI()

    def _get_cells(self, ctxt, req, detail=False):
        """Return all cells."""
        # Ask the CellsManager for the most recent data
        items = self.cells_rpcapi.get_all_cell_info(ctxt)
        items = common.limited(items, req)
        items = [_scrub_cell(item, detail=detail) for item in items]
        return dict(cells=items)

    @wsgi.serializers(xml=CellsTemplate)
    def index(self, req):
        """Return all cells in brief"""
        ctxt = req.environ['nova.context']
        authorize(ctxt)
        return self._get_cells(ctxt, req)

    @wsgi.serializers(xml=CellsTemplate)
    def detail(self, req):
        """Return all cells in detail"""
        ctxt = req.environ['nova.context']
        authorize(ctxt)
        return self._get_cells(ctxt, req, detail=True)

    @wsgi.serializers(xml=CellTemplate)
    def info(self, req):
        """Return name and capabilities for this cell."""
        context = req.environ['nova.context']
        authorize(context)
        cell_capabs = {}
        my_caps = FLAGS.cell_capabilities
        for cap in my_caps:
            key, value = cap.split('=')
            cell_capabs[key] = value
        cell = {'id': 0,
                'name': FLAGS.cell_name,
                'type': 'self',
                'rpc_host': None,
                'rpc_port': 0,
                'capabilities': cell_capabs}
        return dict(cell=cell)

    @wsgi.serializers(xml=CellTemplate)
    def show(self, req, id):
        """Return data about the given cell id"""
        context = req.environ['nova.context']
        authorize(context)
        try:
            cell_id = int(id)
        except ValueError:
            expl = _('Cell ID should be an integer')
            raise exc.HTTPBadRequest(explanation=expl)
        try:
            cell = db.cell_get(context, cell_id)
        except exception.CellNotFound:
            raise exc.HTTPNotFound()
        return dict(cell=_scrub_cell(cell))

    def delete(self, req, id):
        """Delete a child cell entry."""
        context = req.environ['nova.context']
        authorize(context)
        try:
            cell_id = int(id)
        except ValueError:
            expl = _('Cell ID should be an integer')
            raise exc.HTTPBadRequest(explanation=expl)
        num_deleted = db.cell_delete(context, cell_id)
        if num_deleted == 0:
            raise exc.HTTPNotFound()
        return {}

    def _validate_cell_name(self, cell_name):
        """Validate cell_name is not empty and doesn't contain '!' or '.'."""
        if not cell_name:
            msg = _("Cell name cannot be empty")
            LOG.error(msg)
            raise exc.HTTPBadRequest(explanation=msg)
        if '!' in cell_name or '.' in cell_name:
            msg = _("Cell name cannot contain '!' or '.'")
            LOG.error(msg)
            raise exc.HTTPBadRequest(explanation=msg)

    def _validate_cell_type(self, cell_type):
        """Validate cell_type is 'parent' or 'child'."""
        if cell_type not in ['parent', 'child']:
            msg = _("Cell type must be 'parent' or 'child'")
            LOG.error(msg)
            raise exc.HTTPBadRequest(explanation=msg)

    def _convert_cell_type(self, cell):
        """Convert cell['type'] to is_parent boolean."""
        if 'type' in cell:
            self._validate_cell_type(cell['type'])
            cell['is_parent'] = cell['type'] == 'parent'
            del cell['type']
        else:
            cell['is_parent'] = False

    @wsgi.serializers(xml=CellTemplate)
    @wsgi.deserializers(xml=CellDeserializer)
    def create(self, req, body):
        """Create a child cell entry."""
        context = req.environ['nova.context']
        authorize(context)
        if 'cell' not in body:
            msg = _("No cell information in request")
            LOG.error(msg)
            raise exc.HTTPBadRequest(explanation=msg)
        cell = body['cell']
        if 'name' not in cell:
            msg = _("No cell name in request")
            LOG.error(msg)
            raise exc.HTTPBadRequest(explanation=msg)
        self._validate_cell_name(cell['name'])
        self._convert_cell_type(cell)
        cell = db.cell_create(context, cell)
        return dict(cell=_scrub_cell(cell))

    @wsgi.serializers(xml=CellTemplate)
    @wsgi.deserializers(xml=CellDeserializer)
    def update(self, req, id, body):
        """Update a child cell entry."""
        context = req.environ['nova.context']
        authorize(context)
        if 'cell' not in body:
            msg = _("No cell information in request")
            LOG.error(msg)
            raise exc.HTTPBadRequest(explanation=msg)
        cell = body['cell']
        try:
            cell_id = int(id)
        except ValueError:
            expl = _('Cell ID should be an integer')
            raise exc.HTTPBadRequest(explanation=expl)
        cell.pop('id', None)
        if 'name' in cell:
            self._validate_cell_name(cell['name'])
        self._convert_cell_type(cell)
        try:
            cell = db.cell_update(context, cell_id, cell)
        except exception.CellNotFound:
            raise exc.HTTPNotFound()
        return dict(cell=_scrub_cell(cell))

    def sync_instances(self, req, body):
        """Tell all cells to sync instance info."""
        context = req.environ['nova.context']
        authorize(context)
        project_id = body.pop('project_id', None)
        deleted = body.pop('deleted', False)
        updated_since = body.pop('updated_since', None)
        if body:
            msg = _("Only 'updated_since' and 'project_id' are understood.")
            raise exc.HTTPBadRequest(explanation=msg)
        if updated_since:
            try:
                timeutils.parse_isotime(updated_since)
            except ValueError:
                msg = _('Invalid changes-since value')
                raise exc.HTTPBadRequest(explanation=msg)
        self.cells_rpcapi.sync_instances(context, project_id=project_id,
                updated_since=updated_since, deleted=deleted)


class Cells(extensions.ExtensionDescriptor):
    """Enables cells-related functionality such as adding child cells,
    listing child cells, getting the capabilities of the local cell,
    and returning build plans to parent cells' schedulers
    """

    name = "Cells"
    alias = "os-cells"
    namespace = "http://docs.openstack.org/compute/ext/cells/api/v1.1"
    updated = "2011-09-21T00:00:00+00:00"

    def get_resources(self):
        coll_actions = {
                'detail': 'GET',
                'info': 'GET',
                'sync_instances': 'POST',
        }

        res = extensions.ResourceExtension('os-cells',
                Controller(), collection_actions=coll_actions)
        return [res]
