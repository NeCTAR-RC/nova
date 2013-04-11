import time
import datetime
import random

from nova import db
from nova.cells import utils as cells_utils
from nova.openstack.common import log as logging
from nova.openstack.common import timeutils

LOG = logging.getLogger('nova.cells.consistency')


class ConsistencyHandler(object):

    def __init__(self, update_interval, update_threshold, update_number, our_path, cells_rpcapi, get_child_cells):
        self.update_interval = update_interval
        self.update_threshold = update_threshold
        self.update_number = update_number
        self.entries_to_heal = iter([])
        # TODO get db from somewhere
        self.get_entries_filtered = None
        self.updated_list = False
        self.last_heal_time = 0
        self.our_path = our_path
        self.cells_rpcapi = cells_rpcapi
        self._get_child_cells = get_child_cells
        self.model_name = 'entry'
        self.model_name_plural = 'entries'


    def reset(self):
        self.updated_list = False

    def _get_entries_to_heal(self, context, updated_since=None,
            project_id=None, deleted=True, shuffle=False):

        filters = {}
        if updated_since is not None:
            filters['changes-since'] = updated_since
        if project_id is not None:
            filters['project_id'] = project_id
        if not deleted:
            filters['deleted'] = false
        entries = self.get_entries_filtered(
                context, filters, 'deleted', 'asc')
        if shuffle:
            random.shuffle(entries)
        for entry in entries:
            yield entry

    def _get_next_entry(self, context):
        try:
            entry = self.entries_to_heal.next()
        except StopIteration:
            if self.updated_list:
                return
            threshold = self.update_threshold
            updated_since = None
            if threshold > 0:
                updated_since = timeutils.utcnow() - datetime.timedelta(
                        seconds=threshold)
            self.entries_to_heal = self._get_entries_to_heal(
                    context, updated_since=updated_since, shuffle=True)
            self.updated_list = True
            try:
                entry = self.entries_to_heal.next()
            except StopIteration:
                return
        return entry

    def is_time_to_heal(self, curr_time):
        if not self.update_interval:
            return False
        if self.last_heal_time + self.update_interval > curr_time:
            return False
        return True

    def set_last_heal_time(self, curr_time):
        self.last_heal_time = curr_time

    def _send_destroy(self, context, entry):
        msg = _('Healing process attempted to send delete message from '
                'base ConsistencyManager class, which is not supported')
        LOG.error(msg)

    def _send_create(self, context, entry):
        msg = _('Healing process attempted to send create message from '
                'base ConsistencyManager class, which is not supported')
        LOG.error(msg)

    def _sync_entry(self, context, entry):
        """broadcast an instance_update or instance_destroy message up to
        parent cells.
        """
        if entry['deleted']:
            LOG.info(_("sending message to delete %s" % (self.model_name_plural)))
            self._send_destroy(context, entry)
        else:
            LOG.info(_("sending message to create %s" % (self.model_name_plural)))
            self._send_create(context, entry)

    def heal_entries(self, context):
        num_entries = self.update_number
        LOG.info(_("Synching %s %s" % (num_entries, self.model_name_plural)))
        groups = {}
        for i in xrange(num_entries):
            while True:
                # Yield to other greenthreads
                time.sleep(0)
                entry = self._get_next_entry(context)
                if not entry:
                    LOG.info("No more %s to sync" % (self.model_name_plural))
                    return
                self._sync_entry(context, entry)
                #TODO why is this break here?
                break


class GroupConsistencyHandler(ConsistencyHandler):
    def __init__(self, *args, **kwargs):
        super(GroupConsistencyHandler, self).__init__(*args, **kwargs)
        self.get_entries_filtered = db.security_group_get_all_by_filters
        self.send_destroy_message = None
        self.send_create_message = None
        self.model_name = 'group'
        self.model_name_plural= 'groups'

    def _send_create(self, context, group):
        msg = cells_utils.form_security_group_create_broadcast_message(
            group, routing_path=self.our_path, hopcount=1)

        self.cells_rpcapi.send_message_to_cells(context, self._get_child_cells(), msg)

    def _send_destroy(self, context, group):
        msg = cells_utils.form_security_group_destroy_broadcast_message(
            group, routing_path=self.our_path, hopcount=1)

        self.cells_rpcapi.send_message_to_cells(context, self._get_child_cells(), msg)


class RuleConsistencyHandler(ConsistencyHandler):
    def __init__(self, *args, **kwargs):
        super(RuleConsistencyHandler, self).__init__(*args, **kwargs)
        self.get_entries_filtered = db.security_group_rule_get_all_by_filters
        self.send_destroy_message = None
        self.send_create_message = None
        self.model_name = 'rule'
        self.model_name_plural= 'rules'

    def _send_create(self, context, rule):
        group = db.security_group_get(context, rule['parent_group_id'])
        msg = cells_utils.form_security_group_rule_create_broadcast_message(
            rule, group, routing_path=self.our_path, hopcount=1)

        self.cells_rpcapi.send_message_to_cells(context, self._get_child_cells(), msg)

    def _send_destroy(self, context, rule):
        group = db.security_group_get(context, rule['parent_group_id'])
        msg = cells_utils.form_security_group_rule_destroy_broadcast_message(
            rule, group, routing_path=self.our_path, hopcount=1)

        self.cells_rpcapi.send_message_to_cells(context,
                self._get_child_cells(), msg)

    #Disabling this for now
    def heal_entries_disabled(self, context):
        num_entries = self.update_number
        LOG.info(_("Synching %s entries" % (num_entries)))
        groups = {}
        for i in xrange(num_entries):
            while True:
                # Yield to other greenthreads
                time.sleep(0)
                entry = self._get_next_entry(context)
                if not entry:
                    LOG.info(_("No more %s to sync") % (self.model_name_plural))
                    return
                self._sync_entry(context, entry)
                security_group = db.security_group_get(context, entry['parent_group_id'])
                if not security_group in groups:
                    groups[security_group] = set()
                for instance in security_group['instances']:
                    groups[security_group].add((instance['cell_name'], instance['host']))
                break

        for security_group, cell_name_and_host_pairs in groups.items():
            for cell_name, host in cell_name_and_host_pairs:
                msg = _("Refreshing instance security group rules for %s on cell %s")
                LOG.info(msg % (security_group, cell_name))
                group_identifiers = [security_group['name'], security_group['project_id']]
                self.cells_rpcapi.cast_service_api_method(context, cell_name, 'securitygroup_rpc',
                        'refresh_security_group_rules', group_identifiers, host)


class MappingConsistencyHandler(ConsistencyHandler):

    def __init__(self, *args, **kwargs):
        super(MappingConsistencyHandler, self).__init__(*args, **kwargs)
        create_function = None
        destroy_function = None

    def _send_create(self, context, entry):
        uuid = entry['uuid']
        id = entry['id']
        self.cells_rpcapi.broadcast_dbmethod_down(context, self.create_function, uuid, id=id)
        LOG.info(_('Sent broadcast message down to create %s with id=%s and uuid=%s') % (self.model_name, id, uuid))

    def _send_destroy(self, context, entry):
        uuid =entry['uuid']
        id = entry['id']
        self.cells_rpcapi.broadcast_dbmethod_down(context, self.create_function, uuid, id=id)
        LOG.info(_('Sent broadcast message down to destroy %s with id=%s and uuid=%s') % (self.model_name, id, uuid))

class S3ImageConsistencyHandler(MappingConsistencyHandler):

    def __init__(self, *args, **kwargs):
        super(S3ImageConsistencyHandler, self).__init__(*args, **kwargs)
        self.get_entries_filtered = db.s3_image_get_all_by_filters
        self.model_name = 's3 image'
        self.model_name_plural= 's3 images'
        self.create_function = 's3_image_create'

    def _send_destroy(self, context, s3_image):
        msg = _('Healing process attempted to delete an S3 image entry, which is not supported')
        LOG.error(msg)

class InstanceIDMappingConsistencyHandler(MappingConsistencyHandler):

    def __init__(self, *args, **kwargs):
        super(InstanceIDMappingConsistencyHandler, self).__init__(*args, **kwargs)
        self.get_entries_filtered = db.ec2_instance_get_all_by_filters
        self.model_name = 'instance id mapping'
        self.model_name_plural= 'instance id mapping'
        self.create_function = 'ec2_instance_create'

    def _send_destroy(self, context, instance_id_mapping):
        msg = _('Healing process attempted to delete an instance id mapping , which is not supported')
        LOG.error(msg)

class VolumeIDMappingConsistencyHandler(MappingConsistencyHandler):

    def __init__(self, *args, **kwargs):
        super(VolumeIDMappingConsistencyHandler, self).__init__(*args, **kwargs)
        self.get_entries_filtered = db.ec2_volume_get_all_by_filters
        self.model_name = 'volume id mapping'
        self.model_name_plural= 'volume id mapping'
        self.create_function = 'ec2_volume_create'

    def _send_create(self, context, entry):
        uuid = entry['uuid']
        id = entry['id']
        self.cells_rpcapi.broadcast_dbmethod_down(context, self.create_function, uuid, forced_id=id)
        LOG.info(_('Sent broadcast message down to create %s with id=%s and uuid=%s') % (self.model_name, id, uuid))

    def _send_destroy(self, context, instance_id_mapping):
        msg = _('Healing process attempted to delete an volume id mapping , which is not supported')
        LOG.error(msg)


class InstanceAssociationConsistencyHandler(ConsistencyHandler):

    def __init__(self, *args, **kwargs):
        super(InstanceAssociationConsistencyHandler, self).__init__(*args, **kwargs)
        self.get_entries_filtered = db.security_group_instance_association_get_all_by_filters
        self.model_name = 'instance association'
        self.model_name_plural= 'instance associations'

    def _send_create(self, context, instance_association):
        group = db.security_group_get(context, instance_association['security_group_id'])

        msg = cells_utils.form_instance_association_create_broadcast_message(
            instance_association, group, routing_path=self.our_path, hopcount=1)

        self.cells_rpcapi.send_message_to_cells(context, self._get_child_cells(), msg)

    def _send_destroy(self, context, instance_association):
        group = db.security_group_get(context, instance_association['security_group_id'])

        msg = cells_utils.form_instance_association_destroy_broadcast_message(
            instance_association, group, routing_path=self.our_path, hopcount=1)

        self.cells_rpcapi.send_message_to_cells(context,
                self._get_child_cells(), msg)
