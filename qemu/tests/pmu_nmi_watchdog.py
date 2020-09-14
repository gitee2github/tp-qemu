"""
pmu nmi watchdog
"""
import logging
import time
import os.path

from virttest import utils_test
from virttest import env_process
from virttest import error_context
from virttest import data_dir

@error_context.context_aware
def run(test, params, env):
    """
    Test the function of pmu nmi watchdog

    Test Step:
        1. see every function step

    :param test: qemu test object
    :param params: Dictionary with the test parameters.
    :param env: Dictionary with test environment.
    """
    # pylint: disable=R0914, C0103, W1201, W0641, R0915
    if "custom_smp" in params:
        params["smp"] = params["custom_smp"]
    env_process.preprocess(test, params, env)
    vm = env.get_vm(params["main_vm"])
    vm.create(params=params)
    vm.verify_alive()
    timeout = float(params.get("login_timeout", 240))
    session = vm.wait_for_login(timeout=timeout)
    end_time = time.time() + timeout

    deadlock_test_link = params.get("deadlock_test_link")
    deadlock_test_path = params.get("deadlock_test_path")
    src_link = os.path.join(data_dir.get_deps_dir(""),
                            deadlock_test_link)
    vm.copy_files_to(src_link, deadlock_test_path, timeout=60)
    pre_cmd = params["pre_cmd"]
    session.cmd(pre_cmd)

    deadlock_test_cmd = params["deadlock_test_cmd"]

    def _nmi_watchdog_check(session):
        """
        check if pmu_nmi_watchdog run successfully
        """
        session.cmd(deadlock_test_cmd, ignore_all_errors=True)

        qmp_monitors = vm.get_monitors_by_type("qmp")
        if qmp_monitors:
            qmp_monitor = qmp_monitors[0]
        else:
            test.error("Could not find a QMP monitor, aborting test.")

        while time.time() < end_time:
            if qmp_monitor.get_event("RESET"):
                logging.info("pmu_nmi_watchdog run successfully.")
                return True

        return False

    def nmi_watchdog_test():
        """
        Basic functions
        """
        res = _nmi_watchdog_check(session)
        if not res:
            logging.error("pmu_nmi_watchdog doesn't run successfully.")

    def nmi_watchdog_edit():
        """
        Test whether this switch takes effect.
        """
        switch_cmd0 = params["switch_cmd0"]
        session.cmd(switch_cmd0)
        deadlock_test_cmd = params["deadlock_test_cmd"]
        session.cmd(deadlock_test_cmd, ignore_all_errors=True)

        rmmod_deadlock = params["rmmod_deadlock_cmd"]
        session.cmd(rmmod_deadlock)

        switch_cmd1 = params["switch_cmd1"]
        session.cmd(switch_cmd1)

        res = _nmi_watchdog_check(session)
        if not res:
            logging.error("pmu_nmi_watchdog doesn't run successfully.")

    def cmdline_test():
        """
        Test pmu nmi watchdog work with different cmdline,
        such as set "irqchip.gicv3_pseudo_nmi=0",then pmu nmi watchdog cannot run.
        """
        boot_option_added = params.get("boot_option_added")
        boot_option_removed = params.get("boot_option_removed")

        utils_test.update_boot_option(vm,
                                      args_removed=boot_option_removed,
                                      args_added=boot_option_added)

        res = _nmi_watchdog_check(session)
        if not res:
            logging.debug("pmu_nmi_watchdog doesn't run successfully.")
        else:
            logging.error("pmu_nmi_watchdog run successfully is not our target!")

    def workwith_i6300esb():
        """
        Testing if i6300esb can work with pmu nmi watchdog
        """
        trigger_cmd = params.get("trigger_cmd", "echo c > /dev/watchdog")
        watchdog_action = params.get("watchdog_action", "reset")

        def _trigger_watchdog(session, trigger_cmd=None):
            """
            Trigger watchdog action
            Param session: guest connect session.
            Param trigger_cmd: cmd trigger the watchdog
            """
            if trigger_cmd is not None:
                error_context.context(("Trigger Watchdog action using:'%s'." %
                                       trigger_cmd), logging.info)
                session.sendline(trigger_cmd)

        # Testing i6300esb
        _trigger_watchdog(session, trigger_cmd)
        if watchdog_action == "reset":
            logging.info("Try to login the guest after reboot")
            vm.wait_for_login(timeout=timeout)
        logging.info("Watchdog action '%s' come into effect." %
                     watchdog_action)

        res = _nmi_watchdog_check(session)
        if not res:
            logging.error("pmu_nmi_watchdog doesn't run successfully.")

    # main procedure
    test_type = params.get("test_type")

    if test_type in locals():
        test_running = locals()[test_type]
        test_running()
    else:
        test.error("Oops test %s doesn't exist, have a check please."
                   % test_type)
