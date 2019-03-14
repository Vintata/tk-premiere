# Copyright (c) 2019 Shotgun Software Inc.
# 
# CONFIDENTIAL AND PROPRIETARY
# 
# This work is provided "AS IS" and subject to the Shotgun Pipeline Toolkit 
# Source Code License included in this distribution package. See LICENSE.
# By accessing, using, copying or modifying this work you indicate your 
# agreement to the Shotgun Pipeline Toolkit Source Code License. All rights 
# not expressly granted therein are reserved by Shotgun Software Inc.

import unittest

from .basic import TestAdobeRPC
from .aftereffects import TestAfterEffectsRPC


def get_tests_by_app_id(app_id, adobe):
    """
    Constructs the appropriate test suite for the app_id that is
    provided.

    :param str app_id: The runtime application id string. This will
                       be something like "AEFT" for After Effects. See constants.js
                       in the CEP extension packaged with this bundle
                       for a full list of supported applications.
    """
    TestAdobeRPC.adobe = adobe
    suite = unittest.TestSuite()
    test_cases = [TestAdobeRPC]

    if app_id in ["AEFT"]:
        test_cases = [TestAfterEffectsRPC]

    for case in test_cases:
        for method in [m for m in dir(case) if m.startswith("test_")]:
            suite.addTest(case(method))

    return suite
