import socket
import time
from functools import partial
from multiprocessing import Process

from avocado.utils import process

from virttest.qemu_devices.qdevices import QBlockdevFormatNode
from virttest.qemu_monitor import MonitorProtocolError, MonitorSocketError
from virttest.utils_misc import parallel

from provider.backup_utils import copyif
from provider.blockdev_live_backup_base import BlockdevLiveBackupBaseTest
from provider.job_utils import query_jobs
from provider.nbd_image_export import InternalNBDExportImage


class BlockdevIncBackupPullModePoweroffVMTest(BlockdevLiveBackupBaseTest):
    """Poweroff VM during pulling image from 4 clients"""

    def __init__(self, test, params, env):
        super(BlockdevIncBackupPullModePoweroffVMTest, self).__init__(
            test, params, env)

        self._is_qemu_hang = False
        self._job = None
        self._nbd_export = None
        localhost = socket.gethostname()
        self.params['nbd_server'] = localhost if localhost else 'localhost'

        # the fleecing image to be exported
        self.params['image_name_image1'] = self.params['image_name']
        self.params['image_format_image1'] = self.params['image_format']
        self._fleecing_image_obj = self.source_disk_define_by_params(
            self.params, self._full_bk_images[0])
        self.trash.append(self._fleecing_image_obj)

        # local target images, where data is copied from nbd image
        self._targets = []
        self._clients = []
        self._client_image_objs = []
        nbd_image = self.params['nbd_image_%s' % self._full_bk_images[0]]
        for tag in self.params.objects('client_images'):
            self._client_image_objs.append(
                self.source_disk_define_by_params(self.params, tag)
            )
            p = Process(target=copyif, args=(self.params, nbd_image, tag))
            self._clients.append(p)
            self._targets.append(partial(self._join_process, p=p))
        self.trash.extend(self._client_image_objs)

    def add_target_data_disks(self):
        self._fleecing_image_obj.create(self.params)

        tag = self._fleecing_image_obj.tag
        devices = self.main_vm.devices.images_define_by_params(
            tag, self.params.object_params(tag), 'disk')
        devices.pop()  # ignore the front end device

        for dev in devices:
            if isinstance(dev, QBlockdevFormatNode):
                dev.params["backing"] = self._source_nodes[0]
            ret = self.main_vm.devices.simple_hotplug(
                dev, self.main_vm.monitor)
            if not ret[1]:
                self.test.fail("Failed to hotplug '%s'" % dev)

    def generate_data_file(self, tag, filename=None):
        """
        No need to create files, just start vm from the target,
        also note that, currently, creating a file may cause
        qemu core dumped due to a product bug 1879437
        """
        pass

    def remove_files_from_system_image(self, tmo=60):
        """No need to remove files for no file is created"""
        pass

    def prepare_test(self):
        super(BlockdevIncBackupPullModePoweroffVMTest, self).prepare_test()
        self._nbd_export = InternalNBDExportImage(
            self.main_vm, self.params, self._full_bk_images[0])
        self._nbd_export.start_nbd_server()
        for obj in self._client_image_objs:
            obj.create(self.params)

    def _wait_till_all_qemu_io_active(self):
        def _wait_till_qemu_io_active(tag):
            for i in range(self.params.get_numeric('cmd_timeout', 20)*10):
                if process.system(self.params['grep_qemu_io_cmd'] % tag,
                                  ignore_status=True, shell=True) == 0:
                    break
                time.sleep(0.1)
            else:
                self.test.error('Failed to detect the active qemu-io process')

        list(map(_wait_till_qemu_io_active,
                 [o.tag for o in self._client_image_objs]))

    def _poweroff_vm_during_data_copy(self):
        self._wait_till_all_qemu_io_active()
        s = self.main_vm.wait_for_login()
        s.cmd(cmd='poweroff', ignore_all_errors=True)

    def destroy_vms(self):
        if self._is_qemu_hang:
            # kill qemu instead of send shell/qmp command,
            # which will wait for minutes
            self.main_vm.monitors = []
            self.main_vm.destroy(gracefully=False)
        elif self.main_vm.is_alive():
            self.main_vm.destroy()

    def _check_qemu_responsive(self):
        try:
            self.main_vm.monitor.cmd(cmd="query-status", timeout=10)
        except MonitorProtocolError:
            self._is_qemu_hang = True
            self.test.fail('qemu hangs')
        except MonitorSocketError:
            self.test.fail('Failed to send qmp cmd to qemu')
        else:
            self.test.error('Too slow I/O to finish pulling data '
                            'set process_timeout to a larger value in cfg')

    def _join_process(self, p):
        p.join(timeout=self.params.get_numeric('process_timeout', 1800))

    def pull_data_and_poweroff_vm_in_parallel(self):
        """pull data and poweroff vm in parallel"""
        results = []
        list(map(lambda p: p.start(), self._clients))
        try:
            self._poweroff_vm_during_data_copy()
            parallel(self._targets)
            # never do join again when p.exitcode is None,
            # in case qemu-io hangs, process never returns
            results = list(map(lambda p: p.exitcode is not None and p.exitcode == 0,
                               self._clients))
        finally:
            list(map(lambda p: p.terminate(), self._clients))
            list(map(lambda p: p.join(), self._clients))

        if not all(results):
            # timeout(still running) or process quit unexpectedly
            self._check_qemu_responsive()

    def cancel_job(self):
        self.main_vm.monitor.cmd('job-cancel', {'id': self._job})

    def export_full_bk_fleecing_img(self):
        self._nbd_export.add_nbd_image(self._full_bk_nodes[0])

    def do_full_backup(self):
        super(BlockdevIncBackupPullModePoweroffVMTest, self).do_full_backup()
        self._job = [job['id'] for job in query_jobs(self.main_vm)][0]

    def do_test(self):
        self.do_full_backup()
        self.export_full_bk_fleecing_img()
        self.pull_data_and_poweroff_vm_in_parallel()
        self.cancel_job()


def run(test, params, env):
    """
    Poweroff VM while pulling data from fleecing image

    test steps:
        1. boot VM
        2. add fleecing disk for full backup to VM via qmp commands
        3. do full backup(sync=none) with bitmap
        4. export the full backup image by internal nbd server
        5. copy data from nbd image exported in step 4 (at least 3 clients)
           into an image
        6. poweroff vm while pulling data

    :param test: test object
    :param params: test configuration dict
    :param env: env object
    """
    inc_test = BlockdevIncBackupPullModePoweroffVMTest(test, params, env)
    inc_test.run_test()
