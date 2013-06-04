# vim: tabstop=4 shiftwidth=4 softtabstop=4
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
Tests For Compute w/ Cells
"""
import functools

from oslo.config import cfg

from nova.compute import api as compute_api
from nova.compute import cells_api as compute_cells_api
from nova import db
from nova import exception
from nova.openstack.common import jsonutils
from nova.openstack.common import log as logging
from nova import quota
from nova.tests.compute import test_compute


LOG = logging.getLogger('nova.tests.test_compute_cells')

ORIG_COMPUTE_API = None
cfg.CONF.import_opt('enable', 'nova.cells.opts', group='cells')


def stub_call_to_cells(context, instance, method, *args, **kwargs):
    fn = getattr(ORIG_COMPUTE_API, method)
    original_instance = kwargs.pop('original_instance', None)
    if original_instance:
        instance = original_instance
        # Restore this in 'child cell DB'
        db.instance_update(context, instance['uuid'],
                dict(vm_state=instance['vm_state'],
                     task_state=instance['task_state']))

    # Use NoopQuotaDriver in child cells.
    saved_quotas = quota.QUOTAS
    quota.QUOTAS = quota.QuotaEngine(
            quota_driver_class=quota.NoopQuotaDriver())
    compute_api.QUOTAS = quota.QUOTAS
    try:
        return fn(context, instance, *args, **kwargs)
    finally:
        quota.QUOTAS = saved_quotas
        compute_api.QUOTAS = saved_quotas


def stub_cast_to_cells(context, instance, method, *args, **kwargs):
    fn = getattr(ORIG_COMPUTE_API, method)
    original_instance = kwargs.pop('original_instance', None)
    if original_instance:
        instance = original_instance
        # Restore this in 'child cell DB'
        db.instance_update(context, instance['uuid'],
                dict(vm_state=instance['vm_state'],
                     task_state=instance['task_state']))

    # Use NoopQuotaDriver in child cells.
    saved_quotas = quota.QUOTAS
    quota.QUOTAS = quota.QuotaEngine(
            quota_driver_class=quota.NoopQuotaDriver())
    compute_api.QUOTAS = quota.QUOTAS
    try:
        fn(context, instance, *args, **kwargs)
    finally:
        quota.QUOTAS = saved_quotas
        compute_api.QUOTAS = saved_quotas


def deploy_stubs(stubs, api, original_instance=None):
    call = stub_call_to_cells
    cast = stub_cast_to_cells

    if original_instance:
        kwargs = dict(original_instance=original_instance)
        call = functools.partial(stub_call_to_cells, **kwargs)
        cast = functools.partial(stub_cast_to_cells, **kwargs)

    stubs.Set(api, '_call_to_cells', call)
    stubs.Set(api, '_cast_to_cells', cast)


def wrap_create_instance(func):
    @functools.wraps(func)
    def wrapper(self, *args, **kwargs):
        instance = self._create_fake_instance()

        def fake(*args, **kwargs):
            return instance

        self.stubs.Set(self, '_create_fake_instance', fake)
        original_instance = jsonutils.to_primitive(instance)
        deploy_stubs(self.stubs, self.compute_api,
                     original_instance=original_instance)
        return func(self, *args, **kwargs)

    return wrapper


class CellsComputeAPITestCase(test_compute.ComputeAPITestCase):
    def setUp(self):
        super(CellsComputeAPITestCase, self).setUp()
        global ORIG_COMPUTE_API
        ORIG_COMPUTE_API = self.compute_api
        self.flags(enable=True, group='cells')

        def _fake_cell_read_only(*args, **kwargs):
            return False

        def _fake_validate_cell(*args, **kwargs):
            return

        def _nop_update(context, instance, **kwargs):
            return instance

        self.compute_api = compute_cells_api.ComputeCellsAPI()
        self.stubs.Set(self.compute_api, '_cell_read_only',
                _fake_cell_read_only)
        self.stubs.Set(self.compute_api, '_validate_cell',
                _fake_validate_cell)

        # NOTE(belliott) Don't update the instance state
        # for the tests at the API layer.  Let it happen after
        # the stub cast to cells so that expected_task_states
        # match.
        self.stubs.Set(self.compute_api, 'update', _nop_update)

        deploy_stubs(self.stubs, self.compute_api)

    def tearDown(self):
        global ORIG_COMPUTE_API
        self.compute_api = ORIG_COMPUTE_API
        super(CellsComputeAPITestCase, self).tearDown()

    def test_instance_metadata(self):
        self.skipTest("Test is incompatible with cells.")

    def test_get_backdoor_port(self):
        self.skipTest("Test is incompatible with cells.")

    def test_snapshot_given_image_uuid(self):
        self.skipTest("Test doesn't apply to API cell.")

    def test_spice_console(self):
        self.skipTest("Test doesn't apply to API cell.")

    def test_vnc_console(self):
        self.skipTest("Test doesn't apply to API cell.")

    @wrap_create_instance
    def test_snapshot(self):
        return super(CellsComputeAPITestCase, self).test_snapshot()

    @wrap_create_instance
    def test_snapshot_image_metadata_inheritance(self):
        return super(CellsComputeAPITestCase,
                self).test_snapshot_image_metadata_inheritance()

    @wrap_create_instance
    def test_snapshot_minram_mindisk(self):
        return super(CellsComputeAPITestCase,
                self).test_snapshot_minram_mindisk()

    @wrap_create_instance
    def test_snapshot_minram_mindisk_VHD(self):
        return super(CellsComputeAPITestCase,
                self).test_snapshot_minram_mindisk_VHD()

    @wrap_create_instance
    def test_snapshot_minram_mindisk_img_missing_minram(self):
        return super(CellsComputeAPITestCase,
                self).test_snapshot_minram_mindisk_img_missing_minram()

    @wrap_create_instance
    def test_snapshot_minram_mindisk_no_image(self):
        return super(CellsComputeAPITestCase,
                self).test_snapshot_minram_mindisk_no_image()

    @wrap_create_instance
    def test_backup(self):
        return super(CellsComputeAPITestCase, self).test_backup()

    def test_detach_volume(self):
        self.skipTest("This test is failing due to TypeError: "
                      "detach_volume() takes exactly 3 arguments (4 given).")

    def test_no_detach_volume_in_rescue_state(self):
        self.skipTest("This test is failing due to TypeError: "
                      "detach_volume() takes exactly 3 arguments (4 given).")

    def test_evacuate(self):
        self.skipTest("Test is incompatible with cells.")

    def test_delete_instance_no_cell(self):
        cells_rpcapi = self.compute_api.cells_rpcapi
        self.mox.StubOutWithMock(cells_rpcapi,
                                 'instance_delete_everywhere')
        self.mox.StubOutWithMock(self.compute_api,
                                 '_cast_to_cells')
        inst = self._create_fake_instance()
        exc = exception.InstanceUnknownCell(instance_uuid=inst['uuid'])
        self.compute_api._cast_to_cells(self.context, inst,
                                        'delete').AndRaise(exc)
        cells_rpcapi.instance_delete_everywhere(self.context,
                inst, 'hard')
        self.mox.ReplayAll()
        self.compute_api.delete(self.context, inst)

    def test_soft_delete_instance_no_cell(self):
        cells_rpcapi = self.compute_api.cells_rpcapi
        self.mox.StubOutWithMock(cells_rpcapi,
                                 'instance_delete_everywhere')
        self.mox.StubOutWithMock(self.compute_api,
                                 '_cast_to_cells')
        inst = self._create_fake_instance()
        exc = exception.InstanceUnknownCell(instance_uuid=inst['uuid'])
        self.compute_api._cast_to_cells(self.context, inst,
                                        'soft_delete').AndRaise(exc)
        cells_rpcapi.instance_delete_everywhere(self.context,
                inst, 'soft')
        self.mox.ReplayAll()
        self.compute_api.soft_delete(self.context, inst)


class CellsComputePolicyTestCase(test_compute.ComputePolicyTestCase):
    def setUp(self):
        super(CellsComputePolicyTestCase, self).setUp()
        global ORIG_COMPUTE_API
        ORIG_COMPUTE_API = self.compute_api
        self.compute_api = compute_cells_api.ComputeCellsAPI()
        deploy_stubs(self.stubs, self.compute_api)

    def tearDown(self):
        global ORIG_COMPUTE_API
        self.compute_api = ORIG_COMPUTE_API
        super(CellsComputePolicyTestCase, self).tearDown()


class AggregateAPITestCase(test_compute.BaseTestCase):

    def setUp(self):
        super(AggregateAPITestCase, self).setUp()
        self.api = compute_cells_api.AggregateAPI()
        self.mox.StubOutWithMock(self.api.db, 'cell_get')
        self.api.db.cell_get('context', 'fake_cell')
        self.mox.ReplayAll()

    def test_create_aggregate(self):
        # The code doesn't call cell_get, so we pretend it did
        self.api.db.cell_get('context', 'fake_cell')
        self.mox.StubOutWithMock(self.api.cells_rpcapi, 'create_aggregate')
        self.api.cells_rpcapi.create_aggregate(
            'context', 'fake_cell', 'aggregate_name', None).AndReturn(
                'fake_result')
        self.mox.ReplayAll()
        result = self.api.create_aggregate(
            'context', 'fake_cell@aggregate_name', None)
        self.assertEqual('fake_result', result)

    def test_get_aggregate(self):
        # The code doesn't call cell_get, so we pretend it did
        self.api.db.cell_get('context', 'fake_cell')
        self.mox.StubOutWithMock(self.api.cells_rpcapi, 'get_aggregate')
        self.api.cells_rpcapi.get_aggregate(
            'context', 'fake_cell', 42).AndReturn('fake_result')
        self.mox.ReplayAll()
        result = self.api.get_aggregate('context', 'fake_cell@42')
        self.assertEqual('fake_result', result)

    def test_get_aggregate_list(self):
        self.mox.StubOutWithMock(self.api.cells_rpcapi, 'get_aggregate_list')
        self.api.cells_rpcapi.get_aggregate_list(
            'context', 'fake_cell').AndReturn(['fake', 'result'])
        self.mox.ReplayAll()
        result = self.api.get_aggregate_list('context', 'fake_cell')
        self.assertEqual(['fake', 'result'], result)

    def test_get_aggregate_list_no_cell(self):
        # The code doesn't call cell_get, so we pretend it did
        self.api.db.cell_get('context', 'fake_cell')
        self.mox.StubOutWithMock(self.api.cells_rpcapi, 'get_aggregate_list')
        self.api.cells_rpcapi.get_aggregate_list(
            'context', None).AndReturn(['fake', 'result'])
        self.mox.ReplayAll()
        result = self.api.get_aggregate_list('context')
        self.assertEqual(['fake', 'result'], result)

    def test_update_aggregate(self):
        # The code doesn't call cell_get, so we pretend it did
        self.api.db.cell_get('context', 'fake_cell')
        self.mox.StubOutWithMock(self.api.cells_rpcapi, 'update_aggregate')
        self.api.cells_rpcapi.update_aggregate(
            'context', 'fake_cell', 42, {'name': 'spares'}).AndReturn(
                'fake_result')
        self.mox.ReplayAll()
        result = self.api.update_aggregate('context', 'fake_cell@42',
                                           {'name': 'spares'})
        self.assertEqual('fake_result', result)

    def test_update_aggregate_metadata(self):
        # The code doesn't call cell_get, so we pretend it did
        self.api.db.cell_get('context', 'fake_cell')
        self.mox.StubOutWithMock(self.api.cells_rpcapi,
                                 'update_aggregate_metadata')
        self.api.cells_rpcapi.update_aggregate_metadata(
            'context', 'fake_cell', 42, {'is_spare': True}).AndReturn(
                'fake_result')
        self.mox.ReplayAll()
        result = self.api.update_aggregate_metadata(
            'context', 'fake_cell@42', {'is_spare': True})
        self.assertEqual('fake_result', result)

    def test_delete_aggregate(self):
        # The code doesn't call cell_get, so we pretend it did
        self.api.db.cell_get('context', 'fake_cell')
        self.mox.StubOutWithMock(self.api.cells_rpcapi, 'delete_aggregate')
        self.api.cells_rpcapi.delete_aggregate(
            'context', 'fake_cell', 42)
        self.mox.ReplayAll()
        self.api.delete_aggregate('context', 'fake_cell@42')

    def test_add_host_to_aggregate(self):
        # The code doesn't call cell_get, so we pretend it did
        self.api.db.cell_get('context', 'fake_cell')
        self.mox.StubOutWithMock(self.api.cells_rpcapi,
                                 'add_host_to_aggregate')
        self.api.cells_rpcapi.add_host_to_aggregate(
            'context', 'fake_cell', 42, 'fake_host').AndReturn('fake_result')
        self.mox.ReplayAll()
        result = self.api.add_host_to_aggregate('context', 'fake_cell@42',
                                                'fake_host')
        self.assertEqual('fake_result', result)

    def test_remove_host_from_aggregate(self):
        # The code doesn't call cell_get, so we pretend it did
        self.api.db.cell_get('context', 'fake_cell')
        self.mox.StubOutWithMock(self.api.cells_rpcapi,
                                 'remove_host_from_aggregate')
        self.api.cells_rpcapi.remove_host_from_aggregate(
            'context', 'fake_cell', 42, 'fake_host').AndReturn('fake_result')
        self.mox.ReplayAll()
        result = self.api.remove_host_from_aggregate('context', 'fake_cell@42',
                                                   'fake_host')
        self.assertEqual('fake_result', result)
