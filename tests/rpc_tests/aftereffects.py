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


class TestAfterEffectsRPC(TestAdobeRPC):
    document = None

    @classmethod
    def setUpClass(cls):
        TestAdobeRPC.setUpClass()

    def setUp(self):
        self.project_path = os.path.join(self.resources, "simpleproject.aep")
        self.engine = sgtk.platform.current_engine()
        self.project = self.engine.adobe.File(self.project_path)
        self.engine.adobe.app.open(self.project)

    def tearDown(self):
        self.engine.adobe.app.project.close(self.engine.adobe.CloseOptions.DO_NOT_SAVE_CHANGES)

    def test_project_path_no_project(self):
        """
        Tests if the method project_path returns an empty string in case no project was loaded
        """
        self.engine.adobe.app.project.close(self.engine.adobe.CloseOptions.DO_NOT_SAVE_CHANGES)
        self.assertEquals(self.engine.project_path, "")

    def test_project_path_project_loaded(self):
        """
        Tests if the method project_path returns the file_path (str) to the project file
        """
        self.assertEquals(self.engine.project_path, self.project_path)

    def test_save_to_path(self):
        """
        Tests if the method save_to_path actually saves the file to the given location
        """
        temp_file = tempfile.NamedTemporaryFile(suffix='.aep')
        temp_file.close()

        self.engine.save(temp_file.name)
        self.assertEquals(self.engine.project_path.lower(), temp_file.name.lower())

    def test_is_adobe_sequence_True(self):
        """
        Tests if the is_adobe_sequence returns True when it should
        """
        test_paths = [
            '/this/is/a/test/path[###].jpg',
            '/this/is/a/test/path###.jpg',
            '/this/is/a/test/path@@.jpg',
            '/this/is/a/test/path%03d.jpg',
        ]
        while test_paths:
            self.assertTrue(self.engine.is_adobe_sequence(test_paths.pop(0)))

    def test_is_adobe_sequence_False(self):
        """
        Tests if the is_adobe_sequence returns False when it should
        """
        test_paths = [
            '/this/is/a/test/path0.jpg',
            '/this/is/a/test/12path.jpg',
            '/this/is/a/test/path[123].jpg',
        ]
        while test_paths:
            self.assertFalse(self.engine.is_adobe_sequence(test_paths.pop(0)))

    def test_iter_collection(self):
        """
        Tests if iter_collection works as expected
        """
        counter = 0
        for itm in self.engine.iter_collection(self.engine.adobe.app.project.items):
            counter += 1
        self.assertEquals(counter, 3)

    def test_is_footage_item(self):
        """
        Tests if is_footage_item works as expected
        """
        expected_name = 'testcapture'
        found_items = []
        for itm in self.engine.iter_collection(self.engine.adobe.app.project.items):
            if self.engine.is_item_of_type(itm, self.engine.AdobeItemTypes.FOOTAGE_ITEM):
                found_items.append(itm.name)
        self.assertEquals(found_items, [expected_name])

    def test_is_comp_item(self):
        """
        Tests if is_comp_item works as expected
        """
        expected_name = 'testcomp'
        found_items = []
        for itm in self.engine.iter_collection(self.engine.adobe.app.project.items):
            if self.engine.is_item_of_type(itm, self.engine.AdobeItemTypes.COMP_ITEM):
                found_items.append(itm.name)
        self.assertEquals(found_items, [expected_name])

    def test_is_folder_item(self):
        """
        Tests if is_folder_item works as expected
        """
        expected_name = 'testfolder'
        found_items = []
        for itm in self.engine.iter_collection(self.engine.adobe.app.project.items):
            if self.engine.is_item_of_type(itm, self.engine.AdobeItemTypes.FOLDER_ITEM):
                found_items.append(itm.name)
        self.assertEquals(found_items, [expected_name])
