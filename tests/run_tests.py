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
import rpc_tests


def run_tests(engine):

    engine.log_debug("Getting test suite...")
    suite = rpc_tests.get_tests_by_app_id(engine.app_id, engine.adobe)

    engine.log_debug("Running test suite...")
    unittest.TextTestRunner().run(suite)

    engine.log_debug("Testing finished.")
