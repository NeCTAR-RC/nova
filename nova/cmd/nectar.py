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

import socket

from nova.conductor import rpcapi as conductor_rpcapi
from nova import context
from nova.objects import block_device
from nova.objects import instance as instance_obj


# Decorators for actions
def args(*args, **kwargs):
    def _decorator(func):
        func.__dict__.setdefault('args', []).insert(0, (args, kwargs))
        return func
    return _decorator


class NectarCommands(object):
    """Class for NeCTAR commands."""

    @args('--instance', metavar='<instance>', help='Instance')
    @args('--volume', metavar='<volume>', help='Volume')
    def remove_bdm(self, instance, volume):
        ctx = context.get_admin_context()
        c = conductor_rpcapi.ConductorAPI()
        instance = instance_obj.Instance.get_by_uuid(ctx, instance)

        if instance['host'] != socket.gethostname():
            print("Instance not running on this host")
            return
        bdms = c.block_device_mapping_get_all_by_instance(ctx, instance)
        for bdm in bdms:
            if bdm['volume_id'] == volume:
                b = block_device.BlockDeviceMapping.get_by_volume_id(
                    ctx, volume, instance_uuid=instance['uuid'])

                print("Deleting BDM with ID %s" % b.id)
                b.destroy()
