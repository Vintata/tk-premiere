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
import os
import json


class TestAdobeRPC(unittest.TestCase):
    adobe = None
    resources = None

    @classmethod
    def setUpClass(cls):
        cls.resources = os.path.abspath(
            os.path.join(
                os.path.dirname(__file__),
                "..",
                "resources",
            ),
        )

    def test_simple_eval(self):
        result = self.adobe.rpc_eval("1 + 1")
        self.assertEqual(2, result)

    def test_get_payload(self):
        next_uid = self.adobe._UID + 1

        expected_payload = dict(
            method="foo",
            id=next_uid,
            jsonrpc="2.0",
            params=[1, 2, 3],
        )

        payload = self.adobe._get_payload(
            method="foo",
            proxy_object=None,
            params=[1, 2, 3],
        )

        self.assertEqual(expected_payload, payload)

    def test_handle_response(self):
        expected_response = dict(foo="bar")
        next_uid = self.adobe._UID + 1

        raw_response = json.dumps(
            dict(
                id=next_uid,
                result=expected_response,
            ),
        )

        self.adobe._handle_response(raw_response)

        try:
            self.assertEqual(self.adobe._RESULTS[next_uid], expected_response)
        finally:
            # We don't want to pollute the results registry as it'll lead to
            # some wacky behavior the next time an RPC command is run.
            del self.adobe._RESULTS[next_uid]

