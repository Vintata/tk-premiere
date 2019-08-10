# Copyright (c) 2019 Shotgun Software Inc.
# 
# CONFIDENTIAL AND PROPRIETARY
# 
# This work is provided "AS IS" and subject to the Shotgun Pipeline Toolkit 
# Source Code License included in this distribution package. See LICENSE.
# By accessing, using, copying or modifying this work you indicate your 
# agreement to the Shotgun Pipeline Toolkit Source Code License. All rights 
# not expressly granted therein are reserved by Shotgun Software Inc.

import os
import tempfile


import sgtk


from . import TestAdobeRPC


class TestPremiereRPC(TestAdobeRPC):
    document = None

    @classmethod
    def setUpClass(cls):
        TestAdobeRPC.setUpClass()

    def setUp(self):
        pass
