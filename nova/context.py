# Copyright 2011 OpenStack Foundation
# Copyright 2010 United States Government as represented by the
# Administrator of the National Aeronautics and Space Administration.
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

"""RequestContext: context for requests that persist through all of nova."""

from contextlib import contextmanager
import copy
import threading
import time
import types

import eventlet.queue
import eventlet.timeout
from keystoneauth1.access import service_catalog as ksa_service_catalog
from keystoneauth1 import plugin
from oslo_context import context
from oslo_db import exception as oslo_db_exc
from oslo_db.sqlalchemy import enginefacade
from oslo_log import log as logging
from oslo_utils import timeutils

import nova.conf
from nova import exception
from nova.i18n import _
from nova import objects
from nova import policy
from nova import utils

CONF = nova.conf.CONF
LOG = logging.getLogger(__name__)
CELL_CACHE = {}
# NOTE(melwitt): Used for the scatter-gather utility to indicate we timed out
# waiting for a result from a cell.
did_not_respond_sentinel = object()
# FIXME(danms): Keep a global cache of the cells we find the
# first time we look. This needs to be refreshed on a timer or
# trigger.
CELLS = []
# Timeout value for waiting for cells to respond. Deprecated alias kept
# for out-of-tree consumers; use get_cell_timeout() which honours the
# [api] cell_timeout option.
CELL_TIMEOUT = 60
# Circuit breaker: cell_uuid -> (state, monotonic expiry). Process-local;
# controlled by [api] cell_failure_hold_seconds. Guarded by _CELL_LOCK
# because nova-api serves requests from multiple OS threads under
# mod_wsgi (under eventlet monkey-patching the lock is green; either way
# check-then-set on the shared dict must be atomic).
_CELL_FAILURES = {}
_CELL_LOCK = threading.Lock()
# _CELL_DOWN: inside the failure hold window; skip the cell until expiry.
# _CELL_PROBING: the hold expired and exactly one request (the "prober")
# is re-testing the cell; everyone else keeps skipping until the probe
# reports through _mark_cell_failed()/_mark_cell_healthy() or its lease
# expires.
_CELL_DOWN = 'down'
_CELL_PROBING = 'probing'
# A probe normally reports within cell_timeout: the scatter-gather kill
# or query_wrapper()'s timeout fires and marks the cell. The lease only
# has to cover a prober that dies without reporting at all, for example
# a client disconnect closing a listing generator mid-consumption
# (GeneratorExit bypasses the marking paths).
_PROBE_LEASE_SLACK = 10


def _should_skip_cell(cell_uuid):
    """Return True if this caller should short-circuit cell_uuid.

    Implements the failure hold window with single-flight probing:

    * down, hold running -> skip.
    * down, hold expired -> this caller is elected the sole prober: the
      entry moves to probing with a lease and the caller proceeds to
      query the cell for real.
    * probing, lease running -> skip; a probe is already in flight.
    * probing, lease expired -> the prober died without reporting;
      elect this caller as the new prober.

    Single-flight matters because the cache is only consulted before a
    query starts: if the expired entry were simply dropped (the previous
    behaviour), every request arriving between hold expiry and the first
    probe's timeout would see no mark and pile onto the dead cell, each
    hanging for up to cell_timeout and crowding the worker's greenthread
    pool - which is how a dead cell starves scatter-gathers aimed at
    healthy cells.
    """
    if CONF.api.cell_failure_hold_seconds <= 0:
        return False
    with _CELL_LOCK:
        entry = _CELL_FAILURES.get(cell_uuid)
        if entry is None:
            return False
        state, expiry = entry
        if time.monotonic() < expiry:
            return True
        lease = get_cell_timeout() + _PROBE_LEASE_SLACK
        _CELL_FAILURES[cell_uuid] = (_CELL_PROBING,
                                     time.monotonic() + lease)
    LOG.debug('Cell %s failure hold expired; electing this request to '
              'probe it', cell_uuid)
    return False


def _mark_cell_failed(cell_uuid):
    """Record a cell failure with the configured hold window."""
    hold = CONF.api.cell_failure_hold_seconds
    if hold > 0:
        with _CELL_LOCK:
            _CELL_FAILURES[cell_uuid] = (_CELL_DOWN,
                                         time.monotonic() + hold)
        # WARNING rather than INFO: this withdraws the cell from every
        # scatter-gather in this process for the hold window, and the
        # breaker runs wherever scatter-gather runs (the scheduler and
        # conductor too, if the options are set there). This line is what
        # explains that capacity disappearing.
        LOG.warning('Cell %s marked failed for %d seconds', cell_uuid, hold)


def _mark_cell_healthy(cell_uuid):
    """Clear any failure or probing mark after a successful response."""
    with _CELL_LOCK:
        _CELL_FAILURES.pop(cell_uuid, None)


# NOTE(shiyan): Exceptions that mean the cell itself did not answer, as
# opposed to an ordinary application error the cell answered *with* (for
# example InstanceNotFound when polling a deleted server, or
# MarkerNotFound while paginating). Only the former may trip the circuit
# breaker: treating a normal error as a cell failure would short-circuit a
# perfectly healthy cell for the whole hold window, turning every
# subsequent request touching it into a down-cell response.
_CELL_DOWN_EXCEPTIONS = (exception.CellTimeout, oslo_db_exc.DBConnectionError)


def _is_cell_down_exception(exc):
    """Return True if exc indicates the cell was unreachable."""
    return isinstance(exc, _CELL_DOWN_EXCEPTIONS)


def get_cell_timeout():
    """Total time in seconds to wait for cells to respond in a scatter."""
    return CONF.api.cell_timeout


class _ContextAuthPlugin(plugin.BaseAuthPlugin):
    """A keystoneauth auth plugin that uses the values from the Context.

    Ideally we would use the plugin provided by auth_token middleware however
    this plugin isn't serialized yet so we construct one from the serialized
    auth data.
    """

    def __init__(self, auth_token, sc):
        super(_ContextAuthPlugin, self).__init__()

        self.auth_token = auth_token
        self.service_catalog = ksa_service_catalog.ServiceCatalogV2(sc)

    def get_token(self, *args, **kwargs):
        return self.auth_token

    def get_endpoint(self, session, service_type=None, interface=None,
                     region_name=None, service_name=None, **kwargs):
        return self.service_catalog.url_for(service_type=service_type,
                                            service_name=service_name,
                                            interface=interface,
                                            region_name=region_name)


@enginefacade.transaction_context_provider
class RequestContext(context.RequestContext):
    """Security context and request information.

    Represents the user taking a given action within the system.

    """

    def __init__(self, user_id=None, project_id=None, is_admin=None,
                 read_deleted="no", remote_address=None, timestamp=None,
                 quota_class=None, service_catalog=None,
                 user_auth_plugin=None, **kwargs):
        """:param read_deleted: 'no' indicates deleted records are hidden,
                'yes' indicates deleted records are visible,
                'only' indicates that *only* deleted records are visible.

           :param overwrite: Set to False to ensure that the greenthread local
                copy of the index is not overwritten.

           :param user_auth_plugin: The auth plugin for the current request's
                authentication data.
        """
        if user_id:
            kwargs['user_id'] = user_id
        if project_id:
            kwargs['project_id'] = project_id

        super(RequestContext, self).__init__(is_admin=is_admin, **kwargs)

        self.read_deleted = read_deleted
        self.remote_address = remote_address
        if not timestamp:
            timestamp = timeutils.utcnow()
        if isinstance(timestamp, str):
            timestamp = timeutils.parse_strtime(timestamp)
        self.timestamp = timestamp

        if service_catalog:
            # Only include required parts of service_catalog
            self.service_catalog = [s for s in service_catalog
                if s.get('type') in ('image', 'block-storage', 'volumev2',
                                     'volumev3', 'key-manager', 'placement',
                                     'network', 'accelerator', 'identity')]
        else:
            # if list is empty or none
            self.service_catalog = []

        # NOTE(markmc): this attribute is currently only used by the
        # rs_limits turnstile pre-processor.
        # See https://lists.launchpad.net/openstack/msg12200.html
        self.quota_class = quota_class

        # NOTE(dheeraj): The following attributes are used by cellsv2 to store
        # connection information for connecting to the target cell.
        # It is only manipulated using the target_cell contextmanager
        # provided by this module
        self.db_connection = None
        self.mq_connection = None
        self.cell_uuid = None

        self.user_auth_plugin = user_auth_plugin
        if self.is_admin is None:
            self.is_admin = policy.check_is_admin(self)

    def get_auth_plugin(self):
        if self.user_auth_plugin:
            return self.user_auth_plugin
        else:
            return _ContextAuthPlugin(self.auth_token, self.service_catalog)

    def _get_read_deleted(self):
        return self._read_deleted

    def _set_read_deleted(self, read_deleted):
        if read_deleted not in ('no', 'yes', 'only'):
            raise ValueError(_("read_deleted can only be one of 'no', "
                               "'yes' or 'only', not %r") % read_deleted)
        self._read_deleted = read_deleted

    def _del_read_deleted(self):
        del self._read_deleted

    read_deleted = property(_get_read_deleted, _set_read_deleted,
                            _del_read_deleted)

    def to_dict(self):
        values = super(RequestContext, self).to_dict()
        # FIXME(dims): defensive hasattr() checks need to be
        # removed once we figure out why we are seeing stack
        # traces
        values.update({
            'user_id': getattr(self, 'user_id', None),
            'project_id': getattr(self, 'project_id', None),
            'is_admin': getattr(self, 'is_admin', None),
            'read_deleted': getattr(self, 'read_deleted', 'no'),
            'remote_address': getattr(self, 'remote_address', None),
            'timestamp': utils.strtime(self.timestamp) if hasattr(
                self, 'timestamp') else None,
            'request_id': getattr(self, 'request_id', None),
            'quota_class': getattr(self, 'quota_class', None),
            'user_name': getattr(self, 'user_name', None),
            'service_catalog': getattr(self, 'service_catalog', None),
            'project_name': getattr(self, 'project_name', None),
        })
        # NOTE(tonyb): This can be removed once we're certain to have a
        # RequestContext contains 'is_admin_project', We can only get away with
        # this because we "know" the default value of 'is_admin_project' which
        # is very fragile.
        values.update({
            'is_admin_project': getattr(self, 'is_admin_project', True),
        })
        return values

    @classmethod
    def from_dict(cls, values):
        return super(RequestContext, cls).from_dict(
            values,
            user_id=values.get('user_id'),
            project_id=values.get('project_id'),
            # TODO(sdague): oslo.context has show_deleted, if
            # possible, we should migrate to that in the future so we
            # don't need to be different here.
            read_deleted=values.get('read_deleted', 'no'),
            remote_address=values.get('remote_address'),
            timestamp=values.get('timestamp'),
            quota_class=values.get('quota_class'),
            service_catalog=values.get('service_catalog'),
        )

    def elevated(self, read_deleted=None):
        """Return a version of this context with admin flag set."""
        context = copy.copy(self)
        # context.roles must be deepcopied to leave original roles
        # without changes
        context.roles = copy.deepcopy(self.roles)
        context.is_admin = True

        if 'admin' not in context.roles:
            context.roles.append('admin')

        if read_deleted is not None:
            context.read_deleted = read_deleted

        return context

    def can(self, action, target=None, fatal=True):
        """Verifies that the given action is valid on the target in this
        context.

        :param action: string representing the action to be checked.
        :param target: dictionary representing the object of the action
            for object creation this should be a dictionary representing the
            location of the object
            e.g. ``{'project_id': instance.project_id}``.
        :param fatal: if False, will return False when an exception.Forbidden
           occurs.

        :raises nova.exception.Forbidden: if verification fails and fatal is
            True.

        :return: returns a non-False value (not necessarily "True") if
            authorized and False if not authorized and fatal is False.
        """
        try:
            return policy.authorize(self, action, target)
        except exception.Forbidden:
            if fatal:
                raise
            return False

    def to_policy_values(self):
        policy = super(RequestContext, self).to_policy_values()
        policy['is_admin'] = self.is_admin
        return policy

    def __str__(self):
        return "<Context %s>" % self.to_dict()


def get_context():
    """A helper method to get a blank context.

    Note that overwrite is False here so this context will not update the
    greenthread-local stored context that is used when logging.
    """
    return RequestContext(user_id=None,
                          project_id=None,
                          is_admin=False,
                          overwrite=False)


def get_admin_context(read_deleted="no"):
    # NOTE(alaski): This method should only be used when an admin context is
    # necessary for the entirety of the context lifetime. If that's not the
    # case please use get_context(), or create the RequestContext manually, and
    # use context.elevated() where necessary. Some periodic tasks may use
    # get_admin_context so that their database calls are not filtered on
    # project_id.
    return RequestContext(user_id=None,
                          project_id=None,
                          is_admin=True,
                          read_deleted=read_deleted,
                          overwrite=False)


def is_user_context(context):
    """Indicates if the request context is a normal user."""
    if not context:
        return False
    if context.is_admin:
        return False
    if not context.user_id or not context.project_id:
        return False
    return True


def require_context(ctxt):
    """Raise exception.Forbidden() if context is not a user or an
    admin context.
    """
    if not ctxt.is_admin and not is_user_context(ctxt):
        raise exception.Forbidden()


def authorize_project_context(context, project_id):
    """Ensures a request has permission to access the given project."""
    if is_user_context(context):
        if not context.project_id:
            raise exception.Forbidden()
        elif context.project_id != project_id:
            raise exception.Forbidden()


def authorize_user_context(context, user_id):
    """Ensures a request has permission to access the given user."""
    if is_user_context(context):
        if not context.user_id:
            raise exception.Forbidden()
        elif context.user_id != user_id:
            raise exception.Forbidden()


def authorize_quota_class_context(context, class_name):
    """Ensures a request has permission to access the given quota class."""
    if is_user_context(context):
        if not context.quota_class:
            raise exception.Forbidden()
        elif context.quota_class != class_name:
            raise exception.Forbidden()


def set_target_cell(context, cell_mapping):
    """Adds database connection information to the context
    for communicating with the given target_cell.

    This is used for permanently targeting a cell in a context.
    Use this when you want all subsequent code to target a cell.

    Passing None for cell_mapping will untarget the context.

    :param context: The RequestContext to add connection information
    :param cell_mapping: An objects.CellMapping object or None
    """
    global CELL_CACHE
    if cell_mapping is not None:
        # avoid circular import
        from nova.db.main import api as db
        from nova import rpc

        # Synchronize access to the cache by multiple API workers.
        @utils.synchronized(cell_mapping.uuid)
        def get_or_set_cached_cell_and_set_connections():
            try:
                cell_tuple = CELL_CACHE[cell_mapping.uuid]
            except KeyError:
                db_connection_string = cell_mapping.database_connection
                context.db_connection = db.create_context_manager(
                    db_connection_string)
                if not cell_mapping.transport_url.startswith('none'):
                    context.mq_connection = rpc.create_transport(
                        cell_mapping.transport_url)
                context.cell_uuid = cell_mapping.uuid
                CELL_CACHE[cell_mapping.uuid] = (context.db_connection,
                                                 context.mq_connection)
            else:
                context.db_connection = cell_tuple[0]
                context.mq_connection = cell_tuple[1]
                context.cell_uuid = cell_mapping.uuid

        get_or_set_cached_cell_and_set_connections()
    else:
        context.db_connection = None
        context.mq_connection = None
        context.cell_uuid = None


@contextmanager
def target_cell(context, cell_mapping):
    """Yields a new context with connection information for a specific cell.

    This function yields a copy of the provided context, which is targeted to
    the referenced cell for MQ and DB connections.

    Passing None for cell_mapping will yield an untargetd copy of the context.

    :param context: The RequestContext to add connection information
    :param cell_mapping: An objects.CellMapping object or None
    """
    # Create a sanitized copy of context by serializing and deserializing it
    # (like we would do over RPC). This help ensure that we have a clean
    # copy of the context with all the tracked attributes, but without any
    # of the hidden/private things we cache on a context. We do this to avoid
    # unintentional sharing of cached thread-local data across threads.
    # Specifically, this won't include any oslo_db-set transaction context, or
    # any existing cell targeting.
    cctxt = RequestContext.from_dict(context.to_dict())
    set_target_cell(cctxt, cell_mapping)
    yield cctxt


def scatter_gather_cells(context, cell_mappings, timeout, fn, *args, **kwargs):
    """Target cells in parallel and return their results.

    The first parameter in the signature of the function to call for each cell
    should be of type RequestContext.

    :param context: The RequestContext for querying cells
    :param cell_mappings: The CellMappings to target in parallel
    :param timeout: The total time in seconds to wait for all the results to be
                    gathered
    :param fn: The function to call for each cell
    :param args: The args for the function to call for each cell, not including
                 the RequestContext
    :param kwargs: The kwargs for the function to call for each cell
    :returns: A dict {cell_uuid: result} containing the joined results. The
              did_not_respond_sentinel will be returned if a cell did not
              respond within the timeout. The exception object will
              be returned if the call to a cell raised an exception. The
              exception will be logged.
    """
    greenthreads = []
    queue = eventlet.queue.LightQueue()
    results = {}
    skipped_results = {}

    def gather_result(cell_uuid, fn, *args, **kwargs):
        try:
            result = fn(*args, **kwargs)
        except Exception as e:
            # Only log the exception traceback for non-nova exceptions.
            if not isinstance(e, exception.NovaException):
                LOG.exception('Error gathering result from cell %s', cell_uuid)
            result = e
            if _is_cell_down_exception(e):
                _mark_cell_failed(cell_uuid)
            elif isinstance(e, oslo_db_exc.DBError):
                # A DB-layer error that is not a recognised down shape is
                # ambiguous: oslo_db maps some unreachability modes (for
                # example mysql 1040 'Too many connections') to plain
                # DBError, so treating it as proof of health would clear a
                # legitimate failure mark while the cell is saturated. It
                # is equally not proof of failure (healthy cells throw
                # transient DB errors such as deadlocks), so leave the
                # failure cache untouched either way.
                pass
            else:
                # The cell answered, it just answered with an error, so as
                # far as the circuit breaker is concerned it is healthy.
                _mark_cell_healthy(cell_uuid)
        else:
            # Calling a generator function (the multi-cell listing path,
            # where fn is query_wrapper) executes nothing: the cell has
            # not answered yet, so it proves nothing about health -
            # marking here would clear an in-flight probe and reopen the
            # stampede on the listing path. query_wrapper() owns the
            # health reporting for generators.
            if not isinstance(result, types.GeneratorType):
                _mark_cell_healthy(cell_uuid)
        # The queue is already synchronized.
        queue.put((cell_uuid, result))

    for cell_mapping in cell_mappings:
        # Short-circuit cells that recently failed so a single down cell
        # cannot exhaust the API worker pool by forcing every request to
        # wait the full per-cell timeout. Controlled by
        # [api] cell_failure_hold_seconds (0 disables, preserving legacy
        # behaviour). When the hold expires, _should_skip_cell() lets
        # exactly one request through to re-test the cell (single-flight
        # probing); everyone else keeps skipping until that probe
        # reports. Skips are kept out of ``results`` because the gather
        # loop below equates completion with
        # len(results) == len(greenthreads). A skipped cell is deliberately
        # not re-marked as failed, otherwise the hold window would slide
        # forever under constant traffic.
        if _should_skip_cell(cell_mapping.uuid):
            skipped_results[cell_mapping.uuid] = did_not_respond_sentinel
            LOG.debug('Skipping cell %s: inside failure hold or probe '
                      'window', cell_mapping.uuid)
            continue
        with target_cell(context, cell_mapping) as cctxt:
            greenthreads.append((cell_mapping.uuid,
                                 utils.spawn(gather_result, cell_mapping.uuid,
                                             fn, cctxt, *args, **kwargs)))

    # If every cell was short-circuited by the failure cache there is
    # nothing to wait on.
    if greenthreads:
        with eventlet.timeout.Timeout(timeout, exception.CellTimeout):
            try:
                while len(results) != len(greenthreads):
                    cell_uuid, result = queue.get()
                    results[cell_uuid] = result
            except exception.CellTimeout:
                # NOTE(melwitt): We'll fill in did_not_respond_sentinels at
                # the same time we kill/wait for the green threads.
                pass

    # Kill the green threads still pending and wait on those we know are done.
    for cell_uuid, greenthread in greenthreads:
        if cell_uuid not in results:
            greenthread.kill()
            results[cell_uuid] = did_not_respond_sentinel
            _mark_cell_failed(cell_uuid)
            LOG.warning('Timed out waiting for response from cell %s',
                        cell_uuid)
        else:
            greenthread.wait()

    results.update(skipped_results)
    return results


def load_cells():
    global CELLS
    if not CELLS:
        CELLS = objects.CellMappingList.get_all(get_admin_context())
        LOG.debug('Found %(count)i cells: %(cells)s',
                  dict(count=len(CELLS),
                       cells=','.join([c.identity for c in CELLS])))

    if not CELLS:
        LOG.error('No cells are configured, unable to continue')


def is_cell_failure_sentinel(record):
    return (record is did_not_respond_sentinel or
            isinstance(record, Exception))


def scatter_gather_skip_cell0(context, fn, *args, **kwargs):
    """Target all cells except cell0 in parallel and return their results.

    The first parameter in the signature of the function to call for
    each cell should be of type RequestContext. There is a timeout for
    waiting on all results to be gathered.

    :param context: The RequestContext for querying cells
    :param fn: The function to call for each cell
    :param args: The args for the function to call for each cell, not including
                 the RequestContext
    :param kwargs: The kwargs for the function to call for each cell
    :returns: A dict {cell_uuid: result} containing the joined results. The
              did_not_respond_sentinel will be returned if a cell did not
              respond within the timeout. The exception object will
              be returned if the call to a cell raised an exception. The
              exception will be logged.
    """
    load_cells()
    cell_mappings = [cell for cell in CELLS if not cell.is_cell0()]
    return scatter_gather_cells(context, cell_mappings, get_cell_timeout(),
                                fn, *args, **kwargs)


def scatter_gather_single_cell(context, cell_mapping, fn, *args, **kwargs):
    """Target the provided cell and return its results or sentinels in case of
    failure.

    The first parameter in the signature of the function to call for each cell
    should be of type RequestContext.

    :param context: The RequestContext for querying cells
    :param cell_mapping: The CellMapping to target
    :param fn: The function to call for each cell
    :param args: The args for the function to call for each cell, not including
                 the RequestContext
    :param kwargs: The kwargs for the function to call for this cell
    :returns: A dict {cell_uuid: result} containing the joined results. The
              did_not_respond_sentinel will be returned if the cell did not
              respond within the timeout. The exception object will
              be returned if the call to the cell raised an exception. The
              exception will be logged.
    """
    return scatter_gather_cells(context, [cell_mapping], get_cell_timeout(),
                                fn,
                                *args, **kwargs)


def scatter_gather_all_cells(context, fn, *args, **kwargs):
    """Target all cells in parallel and return their results.

    The first parameter in the signature of the function to call for
    each cell should be of type RequestContext. There is a timeout for
    waiting on all results to be gathered.

    :param context: The RequestContext for querying cells
    :param fn: The function to call for each cell
    :param args: The args for the function to call for each cell, not including
                 the RequestContext
    :param kwargs: The kwargs for the function to call for each cell
    :returns: A dict {cell_uuid: result} containing the joined results. The
              did_not_respond_sentinel will be returned if a cell did not
              respond within the timeout. The exception object will
              be returned if the call to a cell raised an exception. The
              exception will be logged.
    """
    load_cells()
    return scatter_gather_cells(context, CELLS, get_cell_timeout(),
                                fn, *args, **kwargs)
