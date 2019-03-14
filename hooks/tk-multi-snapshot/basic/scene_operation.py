# Copyright (c) 2019 Shotgun Software Inc.
# 
# CONFIDENTIAL AND PROPRIETARY
# 
# This work is provided "AS IS" and subject to the Shotgun Pipeline Toolkit 
# Source Code License included in this distribution package. See LICENSE.
# By accessing, using, copying or modifying this work you indicate your 
# agreement to the Shotgun Pipeline Toolkit Source Code License. All rights 
# not expressly granted therein are reserved by Shotgun Software Inc.
from sgtk import Hook
from sgtk import TankError


class SceneOperation(Hook):
    """
    Hook called to perform an operation with the 
    current scene
    """

    def execute(self, operation, file_path, **kwargs):
        """
        Main hook entry point

        :operation: String
                    Scene operation to perform

        :file_path: String
                    File path to use if the operation
                    requires it (e.g. open)

        :returns:   Depends on operation:
                    'current_path' - Return the current scene
                                     file path as a String
                    all others     - None
        """
        adobe = self.parent.engine.adobe

        if operation == "current_path":
            file_obj = adobe.app.project.file
            if file_obj != None:
                return file_obj.fsName
            raise TankError("The active document must be saved!")

        elif operation == "open":
            adobe.app.project.close(adobe.CloseOptions.DO_NOT_SAVE_CHANGES)
            adobe.app.open(adobe.File(file_path))

        elif operation == "save":
            # save the current script
            adobe.app.project.save()


