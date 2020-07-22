import re
import time
import random
import logging
import platform

from provider import cpu_utils

from virttest import error_context
from virttest import utils_misc
from virttest import utils_package
from virttest.utils_test import BackgroundTest


@error_context.context_aware
def run(test, params, env):
    """
    Test hotplug vcpu devices and execute stress test.

    1) Boot up guest without vcpu device.
    2) Hotplug vcpu devices and check successfully or not. (qemu side)
    3) Check if the number of CPUs in guest changes accordingly. (guest side)
    4) Execute stress test on all hotplugged vcpu devices
    5) Hotunplug vcpu devices during stress test
    6) Recheck the number of CPUs in guest.

    :param test:   QEMU test object.
    :param params: Dictionary with the test parameters.
    :param env:    Dictionary with test environment.
    """

    def heavyload_install():
        if session.cmd_status(test_installed_cmd) != 0:
            logging.warning("Could not find installed heavyload in guest, will"
                            " install it via winutils.iso ")
            winutil_drive = utils_misc.get_winutils_vol(session)
            if not winutil_drive:
                test.cancel("WIN_UTILS CDROM not found.")
            install_cmd = params["install_cmd"] % winutil_drive
            session.cmd(install_cmd)

    os_type = params["os_type"]
    login_timeout = params.get_numeric("login_timeout", 360)
    stress_duration = params.get_numeric("stress_duration", 180)
    verify_wait_timeout = params.get_numeric("verify_wait_timeout", 60)
    vcpu_devices = params.objects("vcpu_devices")

    vm = env.get_vm(params["main_vm"])
    vm.verify_alive()
    session = vm.wait_for_login(timeout=login_timeout)
    smp = vm.cpuinfo.smp
    maxcpus = vm.cpuinfo.maxcpus

    guest_cpu_ids = cpu_utils.get_guest_cpu_ids(session, os_type)
    for vcpu_dev in vcpu_devices:
        error_context.context("Hotplug vcpu device: %s" % vcpu_dev,
                              logging.info)
        vm.hotplug_vcpu_device(vcpu_dev)
    if not utils_misc.wait_for(lambda: vm.get_cpu_count() == maxcpus,
                               verify_wait_timeout):
        test.fail("Actual number of guest CPUs is not equal to expected")

    if os_type == "linux":
        stress_args = params["stress_args"]
        stress_tool = cpu_utils.VMStressBinding(vm, params,
                                                stress_args=stress_args)
        current_guest_cpu_ids = cpu_utils.get_guest_cpu_ids(session, os_type)
        plugged_cpu_ids = list(current_guest_cpu_ids - guest_cpu_ids)
        plugged_cpu_ids.sort()
        for cpu_id in plugged_cpu_ids:
            error_context.context("Run stress on vCPU(%d) inside guest."
                                  % cpu_id, logging.info)
            stress_tool.load_stress_tool(cpu_id)
        error_context.context("Successfully launched stress sessions, execute "
                              "stress test for %d seconds" % stress_duration,
                              logging.info)
        time.sleep(stress_duration)
        if utils_package.package_install("sysstat", session):
            error_context.context("Check usage of guest CPUs", logging.info)
            mpstat_cmd = "mpstat 1 5 -P %s | cat" % ",".join(
                map(str, plugged_cpu_ids))
            mpstat_out = session.cmd_output(mpstat_cmd)
            cpu_stat = dict(re.findall(r"Average:\s+(\d+)\s+(\d+\.\d+)",
                                       mpstat_out, re.M))
            for cpu_id in plugged_cpu_ids:
                cpu_usage_rate = float(cpu_stat[str(cpu_id)])
                if cpu_usage_rate < 50:
                    test.error("Stress test on vCPU(%s) failed, usage rate: "
                               "%.2f%%" % (cpu_id, cpu_usage_rate))
                logging.info("Usage rate of vCPU(%s) is: %.2f%%", cpu_id,
                             cpu_usage_rate)
        # aarch64 do not support vcpu hot-unplug by now.
        if platform.machine() != 'aarch64':
            for vcpu_dev in vcpu_devices:
                error_context.context("Hotunplug vcpu device: %s" % vcpu_dev,
                                      logging.info)
                vm.hotunplug_vcpu_device(vcpu_dev)
                # Drift the running stress task to other vCPUs
                time.sleep(random.randint(5, 10))
            if vm.get_cpu_count() != smp:
                test.fail("Actual number of guest CPUs is not equal to expected")
        stress_tool.unload_stress()
        stress_tool.clean()
    else:
        install_path = params["install_path"]
        test_installed_cmd = 'dir "%s" | findstr /I heavyload' % install_path
        heavyload_install()
        error_context.context("Run heavyload inside guest.", logging.info)
        heavyload_bin = r'"%s\heavyload.exe" ' % install_path
        heavyload_options = ["/CPU %d" % maxcpus,
                             "/DURATION %d" % (stress_duration // 60),
                             "/AUTOEXIT",
                             "/START"]
        start_cmd = heavyload_bin + " ".join(heavyload_options)
        stress_tool = BackgroundTest(session.cmd, (start_cmd, stress_duration,
                                                   stress_duration))
        stress_tool.start()
        if not utils_misc.wait_for(stress_tool.is_alive, verify_wait_timeout,
                                   first=5):
            test.error("Failed to start heavyload process.")
        stress_tool.join(stress_duration)

    session.close()
