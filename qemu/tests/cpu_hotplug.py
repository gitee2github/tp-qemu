import os
import logging
import re
import platform

from avocado.utils import cpu as conn
from virttest import error_context
from virttest import utils_test
from virttest import utils_misc
from virttest import cpu
from virttest import utils_qemu
from virttest.utils_version import VersionInterval

@error_context.context_aware
def run(test, params, env):
    """
    Runs CPU hotplug test:

    1) Boot the vm with -smp X,maxcpus=Y
    2) After logged into the vm, check CPUs number
    3) Send the monitor command cpu_set [cpu id] for each cpu we wish to have
    4) Verify if guest has the additional CPUs showing up
    5) reboot the vm
    6) recheck guest get hot-pluged CPUs
    7) Try to bring them online by writing 1 to the 'online' file inside
       that dir(Linux guest only)
    8) Run the CPU Hotplug test suite shipped with autotest inside guest
       (Linux guest only)

    :param test: QEMU test object.
    :param params: Dictionary with test parameters.
    :param env: Dictionary with the test environment.
    """
    # aarch64 use vm.hotplug_vcpu_device instead of vm.hotplug_vcpu
    if platform.machine() == 'aarch64':
        machine_type = params["machine_type"].split(':', 1)[1]
        params["vcpu_maxcpus"] = 512
    else:
        machine_type = params["machine_type"]

    qemu_binary = utils_misc.get_qemu_binary(params)
    machine_info = utils_qemu.get_machines_info(qemu_binary)[machine_type]
    machine_info = re.search(r'\(alias of (\S+)\)', machine_info)
    current_machine = machine_info.group(1) if machine_info else machine_type
    supported_maxcpus = (params.get_numeric("vcpu_maxcpus") or
                         utils_qemu.get_maxcpus_hard_limit(qemu_binary,
                                                           current_machine))

    if not params.get_boolean("allow_pcpu_overcommit"):
        supported_maxcpus = min(supported_maxcpus, cpu.online_cpus_count())

    logging.info("Define the CPU topology of guest")
    vcpu_devices = []
    if (conn.get_cpu_vendor_name() == "amd" and
            params.get_numeric("vcpu_threads") != 1):
        test.cancel("AMD cpu does not support multi threads")
    elif machine_type.startswith("pseries"):
        host_kernel_ver = os.uname()[2].split("-")[0]
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
    params["smp"] = 1

    vm = env.get_vm(params["main_vm"])
    vm.create(params=params)
    vm.verify_alive()
    vm.wait_for_login()

    error_context.context("boot the vm, with '-smp X,maxcpus=Y' option,"
                          "thus allow hotplug vcpu", logging.info)

    n_cpus_add = int(params.get("n_cpus_add", 1))
    maxcpus = int(params.get("maxcpus", 160))
    current_cpus = int(params.get("smp", 1))
    onoff_iterations = int(params.get("onoff_iterations", 20))
    cpu_hotplug_cmd = params.get("cpu_hotplug_cmd", "")

    if n_cpus_add + current_cpus > maxcpus:
        logging.warn("CPU quantity more than maxcpus, set it to %s", maxcpus)
        total_cpus = maxcpus
    else:
        total_cpus = current_cpus + n_cpus_add

    error_context.context("check if CPUs in guest matches qemu cmd "
                          "before hot-plug", logging.info)
    if not cpu.check_if_vm_vcpu_match(current_cpus, vm):
        test.error("CPU quantity mismatch cmd before hotplug !")

    if platform.machine() == 'aarch64':
        vcpu_devices = []
        vcpu_devices = vcpu_devices or ["vcpu%d" % vcpu for vcpu in
                                        range(current_cpus, total_cpus)]
        for vcpu_device in vcpu_devices:
            vm.hotplug_vcpu_device(vcpu_device)
    else:
        for cpuid in range(current_cpus, total_cpus):
            error_context.context("hot-pluging vCPU %s" % cpuid, logging.info)
            vm.hotplug_vcpu(cpu_id=cpuid, plug_command=cpu_hotplug_cmd)

    output = vm.monitor.send_args_cmd("info cpus")
    logging.debug("Output of info CPUs:\n%s", output)

    cpu_regexp = re.compile(r"CPU #(\d+)")
    total_cpus_monitor = len(cpu_regexp.findall(output))
    if total_cpus_monitor != total_cpus:
        test.fail("Monitor reports %s CPUs, when VM should have"
                  " %s" % (total_cpus_monitor, total_cpus))
    # Windows is a little bit lazy that needs more secs to recognize.
    error_context.context("hotplugging finished, let's wait a few sec and"
                          " check CPUs quantity in guest.", logging.info)
    if not utils_misc.wait_for(lambda: cpu.check_if_vm_vcpu_match(
                               total_cpus, vm),
                               60 + total_cpus, first=10,
                               step=5.0, text="retry later"):
        test.fail("CPU quantity mismatch cmd after hotplug !")
    error_context.context("rebooting the vm and check CPU quantity !",
                          logging.info)
    session = vm.reboot()
    if not cpu.check_if_vm_vcpu_match(total_cpus, vm):
        test.fail("CPU quantity mismatch cmd after hotplug and reboot !")

    # Window guest doesn't support online/offline test
    if params['os_type'] == "windows":
        return

    error_context.context("locating online files for guest's new CPUs")
    r_cmd = 'find /sys/devices/system/cpu/cpu*/online -maxdepth 0 -type f'
    online_files = session.cmd(r_cmd)
    # Sometimes the return value include command line itself
    if "find" in online_files:
        online_files = " ".join(online_files.strip().split("\n")[1:])
    logging.debug("CPU online files detected: %s", online_files)
    online_files = online_files.split()
    online_files.sort()

    if not online_files:
        test.fail("Could not find CPUs that can be enabled/disabled on guest")

    control_path = os.path.join(test.virtdir, "control",
                                "cpu_hotplug.control")

    timeout = int(params.get("cpu_hotplug_timeout", 300))
    error_context.context("running cpu_hotplug autotest after cpu addition")

    # Last, but not least, let's offline/online the CPUs in the guest
    # several times
    irq = 15
    irq_mask = "f0"
    # if default irq num(15) is not exist, need verfiy another.
    cmd = 'ls -l /proc/irq/15/smp_affinity'
    status = session.cmd_status(cmd)
    if status == 0:
        irq = 15
    else:
        cmd = "cat /proc/interrupts | grep virtio[0-9]-input.0 | head -1 | awk -F ':' '{print$1}'"
        irq = session.cmd_output(cmd).replace('\n','').replace(' ','')
        if not irq:
            logging.warn("Can not find irq number for virtio device, please check")
    for i in range(onoff_iterations):
        if irq:
            session.cmd("echo %s > /proc/irq/%s/smp_affinity" % (irq_mask, irq))
        for online_file in online_files:
            session.cmd("echo 0 > %s" % online_file)
            session.cmd("echo 1 > %s" % online_file)
