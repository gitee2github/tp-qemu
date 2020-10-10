# pylint: disable= C0111
from virttest import env_process
from virttest import error_context
from avocado.utils import process


@error_context.context_aware
def run(test, params, env):
    """
    cpu custom model test:
    steps:
    1). boot guest with cpu custom mode
    2). select some cpu feature
    3). check cpu feature inside vm or qemu cmdline

    :param test: QEMU test object
    :param params: Dictionary with the test parameters
    :param env: Dictionary with test environment.
    """
    mode_name = params.get("model_name")
    feature_name = params.get("feature_name")
    feature_policy = params.get("flag")
    cpu_model_flags = "," + feature_name + "=" + feature_policy
    vm0 = env.get_vm(params["main_vm"])

    params["cpu_model"] = mode_name
    params["cpu_model_flags"] = cpu_model_flags
    params["start_vm"] = "yes"
    env_process.preprocess_vm(test, params, env, params["main_vm"])
    session = vm0.wait_for_login()

    def check_cpu_feature(feature, policy):
        """
        check cpu feature in vm according to the policy
        :param feature: cpu feature name
        :param policy: enable or disable cpu feature
        """
        status, result = session.cmd_status_output("lscpu | grep Flags | grep %s" % feature)
        if policy == "on":
            if status != 0:
                test.fail("no feature found %s, stdout is %s" % (feature, result))
        else:
            if status == 0:
                test.fail("feature found %s, stdout is %s" % (feature, result))

    def check_qemu_cmdline(feature, policy):
        """
        check qemu cmdline on host whether the cpu feature is correct or not
        :param feature: cpu feature name
        :param policy: enable or disable cpu feature
        """
        keyword = feature + "=" + policy
        cmd = "ps aux | grep qemu-kvm | grep -v grep | grep %s" % keyword
        result = process.run(cmd, shell=True, ignore_status=True).stdout_text
        if not result:
            test.fail("cmdline has no %s" % keyword)

    check_cpu_feature(feature_name, feature_policy)
    check_qemu_cmdline(feature_name, feature_policy)
