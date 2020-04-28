"""
Shared code for tests that need to get the qemu version
"""

import re
import logging
from avocado.utils import process
from virttest.compat_52lts import decode_to_text as to_text

QEMU_LIB_VERSION = 0


def version_compare(major, minor, update):
    """
    Determine/use the current qemu library version on the system
    and compare input major, minor, and update values against it.
    If the running version is greater than or equal to the input
    params version, then return True; otherwise, return False

    This is designed to handle upstream version comparisons for
    test adjustments and/or comparisons as a result of upstream
    fixes or changes that could impact test results.

    :param major: Major version to compare against
    :param minor: Minor version to compare against
    :param update: Update value to compare against
    :return: True if running version is greater than or
                  equal to the input qemu version
    """
    global QEMU_LIB_VERSION

    if QEMU_LIB_VERSION == 0:
        try:
            regex = r'QEMU\s*emulator\s*version\s*(\d+)\.(\d+)\.(\d+)\s*'
            check_cmd = "qemu-kvm -version"
            lines = to_text(process.system_output(check_cmd))
            mobj = re.search(regex, lines)
            if bool(mobj):
                QEMU_LIB_VERSION = int(mobj.group(1)) * 1000000 + \
                    int(mobj.group(2)) * 1000 + \
                    int(mobj.group(3))
        except (ValueError, TypeError, AttributeError):
            logging.warning("Error determining qemu version")
            return False

    compare_version = major * 1000000 + minor * 1000 + update

    if QEMU_LIB_VERSION >= compare_version:
        return True
    return False
