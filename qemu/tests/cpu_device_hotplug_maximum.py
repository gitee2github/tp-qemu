import re
import logging
import platform
import multiprocessing
from os import uname

from avocado.utils import cpu

from virttest import error_context
from virttest import utils_misc
from virttest import utils_qemu
from virttest.utils_version import VersionInterval

from provider import cpu_utils


@error_context.context_aware
def run(test, params, env):
    """
    Test hotplug maximum vCPU device.

    1) Launch a guest without vCPU device.
    2) Hotplug all vCPU devices and check successfully or not. (qemu side)
    3) Check if the number of CPUs in guest changes accordingly. (guest side)
    4) Reboot guest.
    5) Hotunplug all vCPU devices and check successfully or not. (qemu side)

    :param test:   QEMU test object.
    :param params: Dictionary with the test parameters.
    :param env:    Dictionary with test environment.
    """
    os_type = params["os_type"]
    if platform.machine() == 'aarch64':
        machine_type = params["machine_type"].split(':', 1)[1]
    else:
        machine_type = params["machine_type"]
    reboot_timeout = params.get_numeric("reboot_timeout")
    mismatch_text = "Actual number of guest CPUs is not equal to the expected"
    not_equal_text = "CPU quantity mismatched! Guest got %s but expected is %s"
    # Many vCPUs will be plugged, it takes some time to bring them online.
    verify_wait_timeout = params.get_numeric("verify_wait_timeout", 300)
    qemu_binary = utils_misc.get_qemu_binary(params)
    machine_info = utils_qemu.get_machines_info(qemu_binary)[machine_type]
    machine_info = re.search(r'\(alias of (\S+)\)', machine_info)
    current_machine = machine_info.group(1) if machine_info else machine_type
    supported_maxcpus = (params.get_numeric("vcpu_maxcpus") or
                         utils_qemu.get_maxcpus_hard_limit(qemu_binary,
                                                           current_machine))
    host_cpu = int(multiprocessing.cpu_count())
    maxcpus = 3 * host_cpu
    supported_maxcpus = min(maxcpus, supported_maxcpus)

    if not params.get_boolean("allow_pcpu_overcommit"):
        supported_maxcpus = min(supported_maxcpus, cpu.online_cpus_count())

    logging.info("Define the CPU topology of guest")
    vcpu_devices = []
    if (cpu.get_cpu_vendor_name() == "amd" and
            params.get_numeric("vcpu_threads") != 1):
        test.cancel("AMD cpu does not support multi threads")
    elif machine_type.startswith("pseries"):
        host_kernel_ver = uname()[2].split("-")[0]
        if params.get_numeric("vcpu_threads") == 8:
            supported_maxcpus -= divmod(supported_maxcpus, 8)[1]
            vcpu_devices = ["vcpu%d" % c for c in
                            range(1, supported_maxcpus // 8)]
        # The maximum value of vcpu_id in 'linux-3.x' is 2048, so
        # (vcpu_id * ms->smp.threads / spapr->vsmt) <= 256, need to adjust it
        elif (supported_maxcpus > 256 and
              host_kernel_ver not in VersionInterval("[4, )")):
            supported_maxcpus = 256
    vcpu_devices = vcpu_devices or ["vcpu%d" % vcpu for vcpu in
                                    range(1, supported_maxcpus)]
    params["vcpu_maxcpus"] = str(supported_maxcpus)
    params["vcpu_devices"] = " ".join(vcpu_devices)
    params["start_vm"] = "yes"

    vm = env.get_vm(params["main_vm"])
    vm.create(params=params)
    vm.verify_alive()
    session = vm.wait_for_login()
    cpuinfo = vm.cpuinfo
    smp = cpuinfo.smp

    error_context.context("Hotplug all vCPU devices", logging.info)
    for vcpu_device in vcpu_devices:
        vm.hotplug_vcpu_device(vcpu_device)

    error_context.context("Check Number of vCPU in guest", logging.info)
    if not utils_misc.wait_for(lambda: vm.get_cpu_count() == supported_maxcpus,
                               verify_wait_timeout, first=5, step=10):
        logging.error(not_equal_text, vm.get_cpu_count(), supported_maxcpus)
        test.fail(mismatch_text)
    logging.info("CPU quantity is as expected: %s", supported_maxcpus)

    error_context.context("Check CPU topology of guest", logging.info)
    if not cpu_utils.check_guest_cpu_topology(session, os_type, cpuinfo):
        test.fail("CPU topology of guest is not as expected.")
    session = vm.reboot(session, timeout=reboot_timeout)
    if not cpu_utils.check_guest_cpu_topology(session, os_type, cpuinfo):
        test.fail("CPU topology of guest is not as expected after reboot.")

    # aarch64 do not support vcpu hot-unplug by now.
    if platform.machine() != 'aarch64':
        error_context.context("Hotunplug all vCPU devices", logging.info)
        for vcpu_device in reversed(vcpu_devices):
            vm.hotunplug_vcpu_device(vcpu_device)
        if not utils_misc.wait_for(lambda: vm.get_cpu_count() == smp,
                                   verify_wait_timeout, first=5, step=10):
            logging.error(not_equal_text, vm.get_cpu_count(), smp)
            test.fail(mismatch_text)
        logging.info("CPU quantity is as expected after hotunplug: %s", smp)
    session.close()
