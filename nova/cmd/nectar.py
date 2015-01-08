import socket

from nova import context
from nova.compute import manager
from nova.conductor import rpcapi as conductor_rpcapi
from nova.objects import block_device


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
        instance = c.instance_get_by_uuid(ctx, instance)
        if instance['host'] != socket.gethostname():
            print("Instance not running on this host")
            return
        bdms = c.block_device_mapping_get_all_by_instance(ctx, instance)
        compute_mgr = manager.ComputeManager(
            compute_driver='nova.virt.libvirt.LibvirtDriver')
        attached_disks = compute_mgr.driver.get_disks(instance['name'])
        for bdm in bdms:
            if bdm['volume_id'] == volume:
                b = block_device.BlockDeviceMapping.get_by_volume_id(
                    ctx, volume, instance_uuid=instance['uuid'])
                disk = b.device_name.split('/')[-1]
                if disk in attached_disks:
                    print("Not deleting, device attached to virt domain")
                    return

                print("Deleting BDM with ID %s" % b.id)
                b.destroy()
