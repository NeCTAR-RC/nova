#    Copyright 2011 OpenStack Foundation
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

import time
from unittest import mock

from oslo_db import exception as oslo_db_exc

from oslo_context import context as o_context
from oslo_context import fixture as o_fixture
from oslo_utils.fixture import uuidsentinel as uuids

from nova import context
from nova import exception
from nova import objects
from nova import test
from nova.tests import fixtures as nova_fixtures


class ContextTestCase(test.NoDBTestCase):
    # NOTE(danms): Avoid any cells setup by claiming we will
    # do things ourselves.
    USES_DB_SELF = True

    def setUp(self):
        super(ContextTestCase, self).setUp()
        self.useFixture(o_fixture.ClearRequestContext())

    def test_request_context_elevated(self):
        user_ctxt = context.RequestContext('111',
                                           '222',
                                           is_admin=False)
        self.assertFalse(user_ctxt.is_admin)
        admin_ctxt = user_ctxt.elevated()
        self.assertTrue(admin_ctxt.is_admin)
        self.assertIn('admin', admin_ctxt.roles)
        self.assertFalse(user_ctxt.is_admin)
        self.assertNotIn('admin', user_ctxt.roles)

    def test_request_context_sets_is_admin(self):
        ctxt = context.RequestContext('111',
                                      '222',
                                      roles=['admin', 'weasel'])
        self.assertTrue(ctxt.is_admin)

    def test_request_context_sets_is_admin_by_role(self):
        ctxt = context.RequestContext('111',
                                      '222',
                                      roles=['administrator'])
        self.assertTrue(ctxt.is_admin)

    def test_request_context_sets_is_admin_upcase(self):
        ctxt = context.RequestContext('111',
                                      '222',
                                      roles=['Admin', 'weasel'])
        self.assertTrue(ctxt.is_admin)

    def test_request_context_read_deleted(self):
        ctxt = context.RequestContext('111',
                                      '222',
                                      read_deleted='yes')
        self.assertEqual('yes', ctxt.read_deleted)

        ctxt.read_deleted = 'no'
        self.assertEqual('no', ctxt.read_deleted)

    def test_request_context_read_deleted_invalid(self):
        self.assertRaises(ValueError,
                          context.RequestContext,
                          '111',
                          '222',
                          read_deleted=True)

        ctxt = context.RequestContext('111', '222')
        self.assertRaises(ValueError,
                          setattr,
                          ctxt,
                          'read_deleted',
                          True)

    def test_service_catalog_default(self):
        ctxt = context.RequestContext('111', '222')
        self.assertEqual([], ctxt.service_catalog)

        ctxt = context.RequestContext('111', '222',
                service_catalog=[])
        self.assertEqual([], ctxt.service_catalog)

        ctxt = context.RequestContext('111', '222',
                service_catalog=None)
        self.assertEqual([], ctxt.service_catalog)

    def test_service_catalog_filter(self):
        service_catalog = [
                {u'type': u'compute', u'name': u'nova'},
                {u'type': u's3', u'name': u's3'},
                {u'type': u'image', u'name': u'glance'},
                {u'type': u'volumev3', u'name': u'cinderv3'},
                {u'type': u'network', u'name': u'neutron'},
                {u'type': u'ec2', u'name': u'ec2'},
                {u'type': u'object-store', u'name': u'swift'},
                {u'type': u'identity', u'name': u'keystone'},
                {u'type': u'block-storage', u'name': u'cinder'},
                {u'type': None, u'name': u'S_withouttype'},
                {u'type': u'vo', u'name': u'S_partofvolume'}]

        volume_catalog = [{u'type': u'image', u'name': u'glance'},
                          {u'type': u'volumev3', u'name': u'cinderv3'},
                          {u'type': u'network', u'name': u'neutron'},
                          {u'type': u'identity', u'name': u'keystone'},
                          {u'type': u'block-storage', u'name': u'cinder'}]
        ctxt = context.RequestContext('111', '222',
                service_catalog=service_catalog)
        self.assertEqual(volume_catalog, ctxt.service_catalog)

    def test_to_dict_from_dict_no_log(self):
        warns = []

        def stub_warn(msg, *a, **kw):
            if (a and len(a) == 1 and isinstance(a[0], dict) and a[0]):
                a = a[0]
            warns.append(str(msg) % a)

        self.stub_out('nova.context.LOG.warning', stub_warn)

        ctxt = context.RequestContext('111',
                                      '222',
                                      roles=['admin', 'weasel'])

        context.RequestContext.from_dict(ctxt.to_dict())

        self.assertEqual(0, len(warns), warns)

    def test_store_when_no_overwrite(self):
        # If no context exists we store one even if overwrite is false
        # (since we are not overwriting anything).
        ctx = context.RequestContext('111',
                                      '222',
                                      overwrite=False)
        self.assertIs(o_context.get_current(), ctx)

    def test_no_overwrite(self):
        # If there is already a context in the cache a new one will
        # not overwrite it if overwrite=False.
        ctx1 = context.RequestContext('111',
                                      '222',
                                      overwrite=True)
        context.RequestContext('333',
                               '444',
                               overwrite=False)
        self.assertIs(o_context.get_current(), ctx1)

    def test_get_context_no_overwrite(self):
        # If there is already a context in the cache creating another context
        # should not overwrite it.
        ctx1 = context.RequestContext('111',
                                      '222',
                                      overwrite=True)
        context.get_context()
        self.assertIs(ctx1, o_context.get_current())

    def test_admin_no_overwrite(self):
        # If there is already a context in the cache creating an admin
        # context will not overwrite it.
        ctx1 = context.RequestContext('111',
                                      '222',
                                      overwrite=True)
        context.get_admin_context()
        self.assertIs(o_context.get_current(), ctx1)

    def test_convert_from_rc_to_dict(self):
        ctx = context.RequestContext(
            111, 222, request_id='req-679033b7-1755-4929-bf85-eb3bfaef7e0b',
            timestamp='2015-03-02T22:31:56.641629')
        values2 = ctx.to_dict()
        expected_values = {'auth_token': None,
                           'domain': None,
                           'is_admin': False,
                           'is_admin_project': True,
                           'project_id': 222,
                           'project_domain': None,
                           'project_name': None,
                           'quota_class': None,
                           'read_deleted': 'no',
                           'read_only': False,
                           'remote_address': None,
                           'request_id':
                               'req-679033b7-1755-4929-bf85-eb3bfaef7e0b',
                           'resource_uuid': None,
                           'roles': [],
                           'service_catalog': [],
                           'show_deleted': False,
                           'timestamp': '2015-03-02T22:31:56.641629',
                           'user': 111,
                           'user_domain': None,
                           'user_id': 111,
                           'user_identity': '111 222 - - -',
                           'user_name': None}
        for k, v in expected_values.items():
            self.assertIn(k, values2)
            self.assertEqual(values2[k], v)

    @mock.patch.object(context.policy, 'authorize')
    def test_can(self, mock_authorize):
        mock_authorize.return_value = True
        ctxt = context.RequestContext('111', '222')

        result = ctxt.can(mock.sentinel.rule)

        self.assertTrue(result)
        mock_authorize.assert_called_once_with(
          ctxt, mock.sentinel.rule, None)

    @mock.patch.object(context.policy, 'authorize')
    def test_can_fatal(self, mock_authorize):
        mock_authorize.side_effect = exception.Forbidden
        ctxt = context.RequestContext('111', '222')

        self.assertRaises(exception.Forbidden,
                          ctxt.can, mock.sentinel.rule)

    @mock.patch.object(context.policy, 'authorize')
    def test_can_non_fatal(self, mock_authorize):
        mock_authorize.side_effect = exception.Forbidden
        ctxt = context.RequestContext('111', '222')

        result = ctxt.can(mock.sentinel.rule, mock.sentinel.target,
                          fatal=False)

        self.assertFalse(result)
        mock_authorize.assert_called_once_with(ctxt, mock.sentinel.rule,
                                               mock.sentinel.target)

    @mock.patch('nova.rpc.create_transport')
    @mock.patch('nova.db.main.api.create_context_manager')
    def test_target_cell(self, mock_create_ctxt_mgr, mock_rpc):
        mock_create_ctxt_mgr.return_value = mock.sentinel.cdb
        mock_rpc.return_value = mock.sentinel.cmq
        ctxt = context.RequestContext('111',
                                      '222',
                                      roles=['admin', 'weasel'])
        # Verify the existing db_connection, if any, is restored
        ctxt.db_connection = mock.sentinel.db_conn
        ctxt.mq_connection = mock.sentinel.mq_conn
        mapping = objects.CellMapping(database_connection='fake://',
                                      transport_url='fake://',
                                      uuid=uuids.cell)
        with context.target_cell(ctxt, mapping) as cctxt:
            self.assertEqual(cctxt.db_connection, mock.sentinel.cdb)
            self.assertEqual(cctxt.mq_connection, mock.sentinel.cmq)
            self.assertEqual(cctxt.cell_uuid, mapping.uuid)
        self.assertEqual(mock.sentinel.db_conn, ctxt.db_connection)
        self.assertEqual(mock.sentinel.mq_conn, ctxt.mq_connection)
        self.assertIsNone(ctxt.cell_uuid)
        # Test again now that we have populated the cache
        with context.target_cell(ctxt, mapping) as cctxt:
            self.assertEqual(cctxt.db_connection, mock.sentinel.cdb)
            self.assertEqual(cctxt.mq_connection, mock.sentinel.cmq)
            self.assertEqual(cctxt.cell_uuid, mapping.uuid)

    @mock.patch('nova.rpc.create_transport')
    @mock.patch('nova.db.main.api.create_context_manager')
    def test_target_cell_unset(self, mock_create_ctxt_mgr, mock_rpc):
        """Tests that passing None as the mapping will temporarily
        untarget any previously set cell context.
        """
        mock_create_ctxt_mgr.return_value = mock.sentinel.cdb
        mock_rpc.return_value = mock.sentinel.cmq
        ctxt = context.RequestContext('111',
                                      '222',
                                      roles=['admin', 'weasel'])
        ctxt.db_connection = mock.sentinel.db_conn
        ctxt.mq_connection = mock.sentinel.mq_conn
        with context.target_cell(ctxt, None) as cctxt:
            self.assertIsNone(cctxt.db_connection)
            self.assertIsNone(cctxt.mq_connection)
        self.assertEqual(mock.sentinel.db_conn, ctxt.db_connection)
        self.assertEqual(mock.sentinel.mq_conn, ctxt.mq_connection)

    @mock.patch('nova.context.set_target_cell')
    def test_target_cell_regenerates(self, mock_set):
        ctxt = context.RequestContext('fake', 'fake')
        # Set a non-tracked property on the context to make sure it
        # does not make it to the targeted one (like a copy would do)
        ctxt.sentinel = mock.sentinel.parent
        with context.target_cell(ctxt, mock.sentinel.cm) as cctxt:
            # Should be a different object
            self.assertIsNot(cctxt, ctxt)

            # Should not have inherited the non-tracked property
            self.assertFalse(hasattr(cctxt, 'sentinel'),
                             'Targeted context was copied from original')

            # Set another non-tracked property
            cctxt.sentinel = mock.sentinel.child

        # Make sure we didn't pollute the original context
        self.assertNotEqual(ctxt.sentinel, mock.sentinel.child)

    def test_get_context(self):
        ctxt = context.get_context()
        self.assertIsNone(ctxt.user_id)
        self.assertIsNone(ctxt.project_id)
        self.assertFalse(ctxt.is_admin)

    @mock.patch('nova.rpc.create_transport')
    @mock.patch('nova.db.main.api.create_context_manager')
    def test_target_cell_caching(self, mock_create_cm, mock_create_tport):
        mock_create_cm.return_value = mock.sentinel.db_conn_obj
        mock_create_tport.return_value = mock.sentinel.mq_conn_obj
        ctxt = context.get_context()
        mapping = objects.CellMapping(database_connection='fake://db',
                                      transport_url='fake://mq',
                                      uuid=uuids.cell)
        # First call should create new connection objects.
        with context.target_cell(ctxt, mapping) as cctxt:
            self.assertEqual(mock.sentinel.db_conn_obj, cctxt.db_connection)
            self.assertEqual(mock.sentinel.mq_conn_obj, cctxt.mq_connection)
        mock_create_cm.assert_called_once_with('fake://db')
        mock_create_tport.assert_called_once_with('fake://mq')
        # Second call should use cached objects.
        mock_create_cm.reset_mock()
        mock_create_tport.reset_mock()
        with context.target_cell(ctxt, mapping) as cctxt:
            self.assertEqual(mock.sentinel.db_conn_obj, cctxt.db_connection)
            self.assertEqual(mock.sentinel.mq_conn_obj, cctxt.mq_connection)
        mock_create_cm.assert_not_called()
        mock_create_tport.assert_not_called()

    def test_is_cell_failure_sentinel(self):
        record = context.did_not_respond_sentinel
        self.assertTrue(context.is_cell_failure_sentinel(record))
        record = TypeError()
        self.assertTrue(context.is_cell_failure_sentinel(record))
        record = objects.Instance()
        self.assertFalse(context.is_cell_failure_sentinel(record))

    @mock.patch('nova.objects.InstanceList.get_by_filters')
    def test_scatter_gather_db_error_leaves_mark_alone(self, mock_get_inst):
        """An ambiguous DB error must not clear a failure mark.

        oslo_db maps some unreachability modes (for example mysql 1040
        'Too many connections') to plain DBError, so treating it as
        proof of health would reopen full traffic to a saturated cell -
        exactly when the breaker must hold. It is equally not proof of
        failure, so the cache must not gain a mark either.
        """
        self.useFixture(nova_fixtures.SpawnIsSynchronousFixture())
        ctxt, mapping0, mapping1, mappings = (
            self._scatter_gather_failure_cache_test_setup())
        # Hold disabled so the pre-existing mark does not short-circuit the
        # cell before we get to call it.
        self.flags(cell_failure_hold_seconds=0, group='api')
        entry = (context._CELL_DOWN, time.monotonic() + 60)
        context._CELL_FAILURES[mapping1.uuid] = entry
        mock_get_inst.side_effect = [
            mock.sentinel.instances,
            oslo_db_exc.DBError('Too many connections')]

        context.scatter_gather_cells(
            ctxt, mappings, 30, objects.InstanceList.get_by_filters,
            {'deleted': False})

        # The mark is neither cleared nor replaced, and the healthy
        # cell gained none.
        self.assertEqual(entry, context._CELL_FAILURES[mapping1.uuid])
        self.assertNotIn(mapping0.uuid, context._CELL_FAILURES)

    @mock.patch('nova.context.target_cell')
    @mock.patch('nova.objects.InstanceList.get_by_filters')
    def test_scatter_gather_cells(self, mock_get_inst, mock_target_cell):
        self.useFixture(nova_fixtures.SpawnIsSynchronousFixture())
        ctxt = context.get_context()
        mapping = objects.CellMapping(database_connection='fake://db',
                                      transport_url='fake://mq',
                                      uuid=uuids.cell)
        mappings = objects.CellMappingList(objects=[mapping])

        filters = {'deleted': False}
        context.scatter_gather_cells(
            ctxt, mappings, 60, objects.InstanceList.get_by_filters, filters,
            sort_dir='foo')

        mock_get_inst.assert_called_once_with(
            mock_target_cell.return_value.__enter__.return_value, filters,
            sort_dir='foo')

    @mock.patch('nova.context.LOG.warning')
    @mock.patch('eventlet.timeout.Timeout')
    @mock.patch('eventlet.queue.LightQueue.get')
    @mock.patch('nova.objects.InstanceList.get_by_filters')
    def test_scatter_gather_cells_timeout(self, mock_get_inst,
                                          mock_get_result, mock_timeout,
                                          mock_log_warning):
        # This is needed because we're mocking get_by_filters.
        self.useFixture(nova_fixtures.SpawnIsSynchronousFixture())
        ctxt = context.get_context()
        mapping0 = objects.CellMapping(database_connection='fake://db0',
                                       transport_url='none:///',
                                       uuid=objects.CellMapping.CELL0_UUID)
        mapping1 = objects.CellMapping(database_connection='fake://db1',
                                       transport_url='fake://mq1',
                                       uuid=uuids.cell1)
        mappings = objects.CellMappingList(objects=[mapping0, mapping1])

        # Simulate cell1 not responding.
        mock_get_result.side_effect = [(mapping0.uuid,
                                        mock.sentinel.instances),
                                       exception.CellTimeout()]

        results = context.scatter_gather_cells(
            ctxt, mappings, 30, objects.InstanceList.get_by_filters)
        self.assertEqual(2, len(results))
        self.assertIn(mock.sentinel.instances, results.values())
        self.assertIn(context.did_not_respond_sentinel, results.values())
        mock_timeout.assert_called_once_with(30, exception.CellTimeout)
        self.assertTrue(mock_log_warning.called)

    @mock.patch('nova.context.LOG.exception')
    @mock.patch('nova.objects.InstanceList.get_by_filters')
    def test_scatter_gather_cells_exception(self, mock_get_inst,
                                            mock_log_exception):
        # This is needed because we're mocking get_by_filters.
        self.useFixture(nova_fixtures.SpawnIsSynchronousFixture())
        ctxt = context.get_context()
        mapping0 = objects.CellMapping(database_connection='fake://db0',
                                       transport_url='none:///',
                                       uuid=objects.CellMapping.CELL0_UUID)
        mapping1 = objects.CellMapping(database_connection='fake://db1',
                                       transport_url='fake://mq1',
                                       uuid=uuids.cell1)
        mappings = objects.CellMappingList(objects=[mapping0, mapping1])

        # Simulate cell1 raising an exception.
        mock_get_inst.side_effect = [mock.sentinel.instances,
                                     test.TestingException()]

        filters = {'deleted': False}
        results = context.scatter_gather_cells(
            ctxt, mappings, 30, objects.InstanceList.get_by_filters, filters)
        self.assertEqual(2, len(results))
        self.assertIn(mock.sentinel.instances, results.values())
        self.assertIsInstance(results[mapping1.uuid], Exception)
        # non-NovaException gets logged
        self.assertTrue(mock_log_exception.called)

        # Now run it again with a NovaException to see it's not logged.
        mock_log_exception.reset_mock()
        mock_get_inst.side_effect = [mock.sentinel.instances,
                                     exception.NotFound()]

        results = context.scatter_gather_cells(
            ctxt, mappings, 30, objects.InstanceList.get_by_filters, filters)
        self.assertEqual(2, len(results))
        self.assertIn(mock.sentinel.instances, results.values())
        self.assertIsInstance(results[mapping1.uuid], exception.NovaException)
        # NovaExceptions are not logged, the caller should handle them.
        mock_log_exception.assert_not_called()

    @mock.patch('nova.context.scatter_gather_cells')
    def test_scatter_gather_single_cell_uses_configured_timeout(
            self, mock_scatter):
        """[api] cell_timeout flows through the wrapper helpers."""
        self.flags(cell_timeout=15, group='api')
        ctxt = context.get_context()
        mapping0 = objects.CellMapping(database_connection='fake://db0',
                                       transport_url='none:///',
                                       uuid=objects.CellMapping.CELL0_UUID)

        filters = {'deleted': False}
        context.scatter_gather_single_cell(
            ctxt, mapping0, objects.InstanceList.get_by_filters, filters)

        mock_scatter.assert_called_once_with(
            ctxt, [mapping0], 15, objects.InstanceList.get_by_filters,
            filters)

    def _scatter_gather_failure_cache_test_setup(self):
        """Common fixtures for circuit-breaker tests.

        Returns (ctxt, mapping0, mapping1, mappings). Ensures any state
        left in the module-level failure cache is cleaned up.
        """
        self.addCleanup(context._CELL_FAILURES.clear)
        ctxt = context.get_context()
        mapping0 = objects.CellMapping(database_connection='fake://db0',
                                       transport_url='none:///',
                                       uuid=objects.CellMapping.CELL0_UUID)
        mapping1 = objects.CellMapping(database_connection='fake://db1',
                                       transport_url='fake://mq1',
                                       uuid=uuids.cell1)
        mappings = objects.CellMappingList(objects=[mapping0, mapping1])
        return ctxt, mapping0, mapping1, mappings

    @mock.patch('nova.context.target_cell')
    @mock.patch('nova.objects.InstanceList.get_by_filters')
    def test_scatter_gather_failure_cache_disabled_by_default(
            self, mock_get_inst, mock_target_cell):
        """With cell_failure_hold_seconds=0 (default), no short-circuit."""
        self.useFixture(nova_fixtures.SpawnIsSynchronousFixture())
        ctxt, _m0, mapping1, mappings = (
            self._scatter_gather_failure_cache_test_setup())
        # Explicit: option disabled.
        self.flags(cell_failure_hold_seconds=0, group='api')
        # Pre-populate the cache to prove it is ignored when disabled.
        context._CELL_FAILURES[mapping1.uuid] = (
            context._CELL_DOWN, time.monotonic() + 60)

        context.scatter_gather_cells(
            ctxt, mappings, 30, objects.InstanceList.get_by_filters,
            {'deleted': False})

        # Both cells were still targeted because the option is 0.
        self.assertEqual(2, mock_target_cell.call_count)

    @mock.patch('nova.context.LOG.exception')
    @mock.patch('nova.objects.InstanceList.get_by_filters')
    def test_scatter_gather_marks_failed_cell(self, mock_get_inst,
                                              mock_log_exception):
        """A connection failure from a cell records it in the cache."""
        self.useFixture(nova_fixtures.SpawnIsSynchronousFixture())
        ctxt, _m0, mapping1, mappings = (
            self._scatter_gather_failure_cache_test_setup())
        self.flags(cell_failure_hold_seconds=60, group='api')
        # cell0 succeeds, cell1 cannot be reached.
        mock_get_inst.side_effect = [mock.sentinel.instances,
                                     oslo_db_exc.DBConnectionError()]

        context.scatter_gather_cells(
            ctxt, mappings, 30, objects.InstanceList.get_by_filters,
            {'deleted': False})

        self.assertIn(mapping1.uuid, context._CELL_FAILURES)
        self.assertNotIn(objects.CellMapping.CELL0_UUID,
                         context._CELL_FAILURES)

    @mock.patch('nova.objects.InstanceList.get_by_filters')
    def test_scatter_gather_app_error_does_not_mark_cell_failed(
            self, mock_get_inst):
        """An ordinary application error must not trip the breaker.

        A cell answering with e.g. InstanceNotFound (polling a deleted
        server) is perfectly healthy. Marking it failed would
        short-circuit it for the whole hold window and turn every
        subsequent request touching that cell into a down-cell response.
        """
        self.useFixture(nova_fixtures.SpawnIsSynchronousFixture())
        ctxt, _m0, mapping1, mappings = (
            self._scatter_gather_failure_cache_test_setup())
        self.flags(cell_failure_hold_seconds=60, group='api')
        mock_get_inst.side_effect = [
            mock.sentinel.instances,
            exception.InstanceNotFound(instance_id=uuids.instance)]

        results = context.scatter_gather_cells(
            ctxt, mappings, 30, objects.InstanceList.get_by_filters,
            {'deleted': False})

        # The exception is still returned to the caller to handle...
        self.assertIsInstance(results[mapping1.uuid],
                              exception.InstanceNotFound)
        # ...but no cell was marked as down.
        self.assertEqual({}, context._CELL_FAILURES)

    @mock.patch('nova.objects.InstanceList.get_by_filters')
    def test_scatter_gather_app_error_clears_stale_mark(self, mock_get_inst):
        """A cell answering with an error clears a stale failure mark."""
        self.useFixture(nova_fixtures.SpawnIsSynchronousFixture())
        ctxt, _m0, mapping1, mappings = (
            self._scatter_gather_failure_cache_test_setup())
        # Hold disabled so the pre-existing mark does not short-circuit the
        # cell before we get to call it.
        self.flags(cell_failure_hold_seconds=0, group='api')
        context._CELL_FAILURES[mapping1.uuid] = (
            context._CELL_DOWN, time.monotonic() + 60)
        mock_get_inst.side_effect = [
            mock.sentinel.instances,
            exception.InstanceNotFound(instance_id=uuids.instance)]

        context.scatter_gather_cells(
            ctxt, mappings, 30, objects.InstanceList.get_by_filters,
            {'deleted': False})

        self.assertNotIn(mapping1.uuid, context._CELL_FAILURES)

    def test_is_cell_down_exception(self):
        """Only unreachable-cell errors may trip the breaker."""
        self.assertTrue(context._is_cell_down_exception(
            exception.CellTimeout()))
        self.assertTrue(context._is_cell_down_exception(
            oslo_db_exc.DBConnectionError()))
        # Ordinary application errors mean the cell answered.
        self.assertFalse(context._is_cell_down_exception(
            exception.InstanceNotFound(instance_id=uuids.instance)))
        self.assertFalse(context._is_cell_down_exception(
            exception.MarkerNotFound(marker=uuids.instance)))
        self.assertFalse(context._is_cell_down_exception(
            test.TestingException()))

    @mock.patch('nova.context.LOG.warning')
    @mock.patch('eventlet.timeout.Timeout')
    @mock.patch('eventlet.queue.LightQueue.get')
    @mock.patch('nova.objects.InstanceList.get_by_filters')
    def test_scatter_gather_marks_timed_out_cell(self, mock_get_inst,
                                                 mock_get_result,
                                                 mock_timeout,
                                                 mock_log_warning):
        """A timeout records the unresponsive cell in the failure cache."""
        # This is needed because we're mocking get_by_filters.
        self.useFixture(nova_fixtures.SpawnIsSynchronousFixture())
        ctxt, mapping0, mapping1, mappings = (
            self._scatter_gather_failure_cache_test_setup())
        self.flags(cell_failure_hold_seconds=60, group='api')

        # Simulate cell1 not responding.
        mock_get_result.side_effect = [(mapping0.uuid,
                                        mock.sentinel.instances),
                                       exception.CellTimeout()]

        results = context.scatter_gather_cells(
            ctxt, mappings, 30, objects.InstanceList.get_by_filters,
            {'deleted': False})

        self.assertEqual(2, len(results))
        # The timeout machinery was entered because there was work to wait
        # on.
        mock_timeout.assert_called_once_with(30, exception.CellTimeout)
        self.assertIn(mapping1.uuid, context._CELL_FAILURES)
        self.assertNotIn(mapping0.uuid, context._CELL_FAILURES)

    @mock.patch('nova.context.target_cell')
    @mock.patch('nova.objects.InstanceList.get_by_filters')
    def test_scatter_gather_short_circuits_recently_failed_cell(
            self, mock_get_inst, mock_target_cell):
        """A cell inside the hold window returns sentinel without spawning."""
        self.useFixture(nova_fixtures.SpawnIsSynchronousFixture())
        ctxt, mapping0, mapping1, mappings = (
            self._scatter_gather_failure_cache_test_setup())
        self.flags(cell_failure_hold_seconds=60, group='api')
        # Pretend cell1 failed recently.
        entry = (context._CELL_DOWN, time.monotonic() + 60)
        context._CELL_FAILURES[mapping1.uuid] = entry

        results = context.scatter_gather_cells(
            ctxt, mappings, 30, objects.InstanceList.get_by_filters,
            {'deleted': False})

        self.assertEqual(2, len(results))
        self.assertIs(context.did_not_respond_sentinel,
                      results[mapping1.uuid])
        # Only cell0 was targeted; cell1 was short-circuited.
        self.assertEqual(1, mock_target_cell.call_count)
        # The skip did not re-mark the cell, else the hold window would
        # slide forever under constant traffic.
        self.assertEqual(entry, context._CELL_FAILURES[mapping1.uuid])

    def test_mark_cell_healthy_clears_failure(self):
        """_mark_cell_healthy removes any failure mark, including on race.

        Protects the scatter_gather race where one request is already in
        flight against a cell when another marks the cell as failed; the
        successful response should clear the spurious mark.
        """
        self.addCleanup(context._CELL_FAILURES.clear)
        self.flags(cell_failure_hold_seconds=60, group='api')
        context._CELL_FAILURES[uuids.cell1] = (
            context._CELL_DOWN, time.monotonic() + 60)

        context._mark_cell_healthy(uuids.cell1)

        self.assertNotIn(uuids.cell1, context._CELL_FAILURES)
        # Calling on a cell not in the cache is a no-op.
        context._mark_cell_healthy(uuids.cell2)
        # It also clears an in-flight probe mark (probe succeeded).
        context._CELL_FAILURES[uuids.cell1] = (
            context._CELL_PROBING, time.monotonic() + 25)
        context._mark_cell_healthy(uuids.cell1)
        self.assertNotIn(uuids.cell1, context._CELL_FAILURES)

    def test_mark_cell_failed_disabled(self):
        """_mark_cell_failed is a no-op when the hold is disabled."""
        self.addCleanup(context._CELL_FAILURES.clear)
        self.flags(cell_failure_hold_seconds=0, group='api')

        context._mark_cell_failed(uuids.cell1)

        self.assertNotIn(uuids.cell1, context._CELL_FAILURES)

    def test_should_skip_cell_down_states(self):
        """The hold window skips; expiry elects a single prober."""
        self.addCleanup(context._CELL_FAILURES.clear)
        self.flags(cell_failure_hold_seconds=60, group='api')
        self.flags(cell_timeout=15, group='api')

        # A down cell inside the hold window is skipped.
        context._CELL_FAILURES[uuids.cell1] = (
            context._CELL_DOWN, time.monotonic() + 60)
        self.assertTrue(context._should_skip_cell(uuids.cell1))

        # When the hold expires the entry is NOT dropped: the caller is
        # elected the sole prober and the entry moves to probing with a
        # lease of cell_timeout + slack.
        context._CELL_FAILURES[uuids.cell1] = (
            context._CELL_DOWN, time.monotonic() - 1)
        before = time.monotonic()
        self.assertFalse(context._should_skip_cell(uuids.cell1))
        state, expiry = context._CELL_FAILURES[uuids.cell1]
        self.assertEqual(context._CELL_PROBING, state)
        lease = context.get_cell_timeout() + context._PROBE_LEASE_SLACK
        self.assertGreaterEqual(expiry, before + lease)

        # While the probe lease is live, everyone else keeps skipping:
        # this is the single-flight property.
        self.assertTrue(context._should_skip_cell(uuids.cell1))

        # An unknown cell is never skipped.
        self.assertFalse(context._should_skip_cell(uuids.cell2))
        self.assertNotIn(uuids.cell2, context._CELL_FAILURES)

    def test_should_skip_cell_probe_lease_takeover(self):
        """An expired probe lease means the prober died; re-elect."""
        self.addCleanup(context._CELL_FAILURES.clear)
        self.flags(cell_failure_hold_seconds=60, group='api')
        self.flags(cell_timeout=15, group='api')

        # A probing entry whose lease has expired (the prober died
        # without reporting, e.g. a client disconnect closed a listing
        # generator): the next caller takes over as prober.
        context._CELL_FAILURES[uuids.cell1] = (
            context._CELL_PROBING, time.monotonic() - 1)
        self.assertFalse(context._should_skip_cell(uuids.cell1))
        state, expiry = context._CELL_FAILURES[uuids.cell1]
        self.assertEqual(context._CELL_PROBING, state)
        self.assertGreater(expiry, time.monotonic())

    def test_should_skip_cell_probe_outcomes(self):
        """A probe result moves the entry out of the probing state."""
        self.addCleanup(context._CELL_FAILURES.clear)
        self.flags(cell_failure_hold_seconds=60, group='api')

        # Probe failed: back to down with a fresh hold window.
        context._CELL_FAILURES[uuids.cell1] = (
            context._CELL_PROBING, time.monotonic() + 25)
        context._mark_cell_failed(uuids.cell1)
        state, _expiry = context._CELL_FAILURES[uuids.cell1]
        self.assertEqual(context._CELL_DOWN, state)
        self.assertTrue(context._should_skip_cell(uuids.cell1))

        # Probe succeeded: mark cleared, cell back in service for all.
        context._CELL_FAILURES[uuids.cell1] = (
            context._CELL_PROBING, time.monotonic() + 25)
        context._mark_cell_healthy(uuids.cell1)
        self.assertFalse(context._should_skip_cell(uuids.cell1))

    @mock.patch('nova.context.target_cell')
    @mock.patch('nova.objects.InstanceList.get_by_filters')
    def test_scatter_gather_elected_probe_targets_cell(
            self, mock_get_inst, mock_target_cell):
        """A scatter arriving after hold expiry probes the cell."""
        self.useFixture(nova_fixtures.SpawnIsSynchronousFixture())
        ctxt, _m0, mapping1, mappings = (
            self._scatter_gather_failure_cache_test_setup())
        self.flags(cell_failure_hold_seconds=60, group='api')
        context._CELL_FAILURES[mapping1.uuid] = (
            context._CELL_DOWN, time.monotonic() - 1)

        context.scatter_gather_cells(
            ctxt, mappings, 30, objects.InstanceList.get_by_filters,
            {'deleted': False})

        # Both cells were targeted: this request was elected to probe
        # cell1, and the successful (mocked) result cleared the mark.
        self.assertEqual(2, mock_target_cell.call_count)
        self.assertNotIn(mapping1.uuid, context._CELL_FAILURES)

    @mock.patch('nova.context.target_cell')
    @mock.patch('nova.objects.InstanceList.get_by_filters')
    def test_scatter_gather_skips_cell_while_probe_in_flight(
            self, mock_get_inst, mock_target_cell):
        """A scatter during another request's probe keeps skipping."""
        self.useFixture(nova_fixtures.SpawnIsSynchronousFixture())
        ctxt, _m0, mapping1, mappings = (
            self._scatter_gather_failure_cache_test_setup())
        self.flags(cell_failure_hold_seconds=60, group='api')
        entry = (context._CELL_PROBING, time.monotonic() + 25)
        context._CELL_FAILURES[mapping1.uuid] = entry

        results = context.scatter_gather_cells(
            ctxt, mappings, 30, objects.InstanceList.get_by_filters,
            {'deleted': False})

        self.assertIs(context.did_not_respond_sentinel,
                      results[mapping1.uuid])
        # Only cell0 was targeted and the probe entry was left alone.
        self.assertEqual(1, mock_target_cell.call_count)
        self.assertEqual(entry, context._CELL_FAILURES[mapping1.uuid])

    @mock.patch('nova.context.target_cell')
    def test_scatter_gather_generator_result_does_not_mark_healthy(
            self, mock_target_cell):
        """Spawning a listing generator must not clear the probe mark.

        For listings fn is query_wrapper, a generator function: calling
        it executes nothing, so the cell has not answered yet. If
        gather_result treated the returned generator as a success it
        would clear the probing mark immediately and every request in
        the next cell_timeout seconds would pile onto the dead cell -
        the stampede single-flight exists to prevent. query_wrapper owns
        the health reporting on that path.
        """
        self.useFixture(nova_fixtures.SpawnIsSynchronousFixture())
        ctxt, _m0, mapping1, mappings = (
            self._scatter_gather_failure_cache_test_setup())
        self.flags(cell_failure_hold_seconds=60, group='api')
        # Expired hold: this scatter is elected to probe cell1.
        context._CELL_FAILURES[mapping1.uuid] = (
            context._CELL_DOWN, time.monotonic() - 1)

        def fake_query_wrapper(cctxt):
            yield mock.sentinel.record

        context.scatter_gather_cells(ctxt, mappings, 30,
                                     fake_query_wrapper)

        # The probe mark is still in place; only consuming the generator
        # (in the merge feeder) may report the outcome.
        state, _expiry = context._CELL_FAILURES[mapping1.uuid]
        self.assertEqual(context._CELL_PROBING, state)

    @mock.patch('nova.context.target_cell')
    @mock.patch('eventlet.timeout.Timeout')
    def test_scatter_gather_all_short_circuited(self, mock_timeout,
                                                mock_target_cell):
        """All cells skipped -> returns immediately without waiting."""
        ctxt, mapping0, mapping1, mappings = (
            self._scatter_gather_failure_cache_test_setup())
        self.flags(cell_failure_hold_seconds=60, group='api')
        entry = (context._CELL_DOWN, time.monotonic() + 60)
        context._CELL_FAILURES[mapping0.uuid] = entry
        context._CELL_FAILURES[mapping1.uuid] = entry

        results = context.scatter_gather_cells(
            ctxt, mappings, 30, objects.InstanceList.get_by_filters,
            {'deleted': False})

        self.assertEqual({mapping0.uuid: context.did_not_respond_sentinel,
                          mapping1.uuid: context.did_not_respond_sentinel},
                         results)
        # Nothing was targeted or spawned and the timeout machinery was
        # not entered.
        mock_target_cell.assert_not_called()
        mock_timeout.assert_not_called()

    @mock.patch('nova.context.scatter_gather_cells')
    @mock.patch('nova.objects.CellMappingList.get_all')
    def test_scatter_gather_all_cells(self, mock_get_all, mock_scatter):
        ctxt = context.get_context()
        mapping0 = objects.CellMapping(database_connection='fake://db0',
                                       transport_url='none:///',
                                       uuid=objects.CellMapping.CELL0_UUID)
        mapping1 = objects.CellMapping(database_connection='fake://db1',
                                       transport_url='fake://mq1',
                                       uuid=uuids.cell1)
        mock_get_all.return_value = objects.CellMappingList(
            objects=[mapping0, mapping1])

        filters = {'deleted': False}
        context.scatter_gather_all_cells(
            ctxt, objects.InstanceList.get_by_filters, filters, sort_dir='foo')

        mock_scatter.assert_called_once_with(
            ctxt, mock_get_all.return_value, 60,
            objects.InstanceList.get_by_filters, filters, sort_dir='foo')

    @mock.patch('nova.context.scatter_gather_cells')
    @mock.patch('nova.objects.CellMappingList.get_all')
    def test_scatter_gather_skip_cell0(self, mock_get_all, mock_scatter):
        ctxt = context.get_context()
        mapping0 = objects.CellMapping(database_connection='fake://db0',
                                       transport_url='none:///',
                                       uuid=objects.CellMapping.CELL0_UUID)
        mapping1 = objects.CellMapping(database_connection='fake://db1',
                                       transport_url='fake://mq1',
                                       uuid=uuids.cell1)
        mock_get_all.return_value = objects.CellMappingList(
            objects=[mapping0, mapping1])

        filters = {'deleted': False}
        context.scatter_gather_skip_cell0(
            ctxt, objects.InstanceList.get_by_filters, filters, sort_dir='foo')

        mock_scatter.assert_called_once_with(
            ctxt, [mapping1], 60, objects.InstanceList.get_by_filters, filters,
            sort_dir='foo')

    @mock.patch('nova.context.scatter_gather_cells')
    def test_scatter_gather_single_cell(self, mock_scatter):
        ctxt = context.get_context()
        mapping0 = objects.CellMapping(database_connection='fake://db0',
                                       transport_url='none:///',
                                       uuid=objects.CellMapping.CELL0_UUID)

        filters = {'deleted': False}
        context.scatter_gather_single_cell(ctxt, mapping0,
            objects.InstanceList.get_by_filters, filters, sort_dir='foo')

        mock_scatter.assert_called_once_with(
            ctxt, [mapping0], context.get_cell_timeout(),
            objects.InstanceList.get_by_filters, filters,
            sort_dir='foo')
