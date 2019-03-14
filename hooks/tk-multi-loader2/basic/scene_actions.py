# Copyright (c) 2019 Shotgun Software Inc.
# 
# CONFIDENTIAL AND PROPRIETARY
# 
# This work is provided "AS IS" and subject to the Shotgun Pipeline Toolkit 
# Source Code License included in this distribution package. See LICENSE.
# By accessing, using, copying or modifying this work you indicate your 
# agreement to the Shotgun Pipeline Toolkit Source Code License. All rights 
# not expressly granted therein are reserved by Shotgun Software Inc.

"""
Hook that loads defines all the available actions, broken down by publish type. 
"""
import os
import re
import glob


import sgtk


HookBaseClass = sgtk.get_hook_baseclass()


# Name of available actions. Corresponds to both the environment config values and the action instance names.
_ADD_TO_COMP = "add_to_comp"
_ADD_TO_PROJECT = "add_to_project"


class AfterEffectsActions(HookBaseClass):

    ##############################################################################################################
    # public interface - to be overridden by deriving classes 

    def generate_actions(self, sg_publish_data, actions, ui_area):
        """
        Returns a list of action instances for a particular publish.
        This method is called each time a user clicks a publish somewhere in the UI.
        The data returned from this hook will be used to populate the actions menu for a publish.

        The mapping between Publish types and actions are kept in a different place
        (in the configuration) so at the point when this hook is called, the loader app
        has already established *which* actions are appropriate for this object.

        The hook should return at least one action for each item passed in via the 
        actions parameter.

        This method needs to return detailed data for those actions, in the form of a list
        of dictionaries, each with name, params, caption and description keys.

        Because you are operating on a particular publish, you may tailor the output 
        (caption, tooltip etc) to contain custom information suitable for this publish.

        The ui_area parameter is a string and indicates where the publish is to be shown. 
        - If it will be shown in the main browsing area, "main" is passed. 
        - If it will be shown in the details area, "details" is passed.
        - If it will be shown in the history area, "history" is passed. 

        Please note that it is perfectly possible to create more than one action "instance" for 
        an action! You can for example do scene introspection - if the action passed in 
        is "character_attachment" you may for example scan the scene, figure out all the nodes
        where this object can be attached and return a list of action instances:
        "attach to left hand", "attach to right hand" etc. In this case, when more than 
        one object is returned for an action, use the params key to pass additional 
        data into the run_action hook.

        :param sg_publish_data: Shotgun data dictionary with all the standard publish fields.
        :param actions: List of action strings which have been defined in the app configuration.
        :param ui_area: String denoting the UI Area (see above).
        :returns List of dictionaries, each with keys name, params, caption and description
        """
        app = self.parent
        app.log_debug("Generate actions called for UI element %s. "
                      "Actions: %s. Publish Data: %s" % (ui_area, actions, sg_publish_data))

        action_instances = []
        try:
            # call base class first
            action_instances += HookBaseClass.generate_actions(self, sg_publish_data, actions, ui_area)
        except AttributeError:
            # base class doesn't have the method, so ignore and continue
            pass

        active_item = self.parent.engine.selected_item
        is_comp_selected = False
        if active_item:
            is_comp_selected = self.parent.engine.is_item_of_type(active_item, self.parent.engine.AdobeItemTypes.COMP_ITEM)
        if _ADD_TO_COMP in actions and is_comp_selected:
            action_instances.append({"name": _ADD_TO_COMP,
                                     "params": None,
                                     "caption": "Add to current composition",
                                     "description": "Adds the current item to the currently selected comp as a layer."})

        if _ADD_TO_PROJECT in actions:
            action_instances.append({"name": _ADD_TO_PROJECT,
                                     "params": None,
                                     "caption": "Add to project",
                                     "description": "Adds the current item to the active project."})

        return action_instances

    def execute_multiple_actions(self, actions):
        """
        Executes the specified action on a list of items.

        The default implementation dispatches each item from ``actions`` to
        the ``execute_action`` method.

        The ``actions`` is a list of dictionaries holding all the actions to
        execute.

        Each entry will have the following values:

            name: Name of the action to execute
            sg_publish_data: Publish information coming from Shotgun
            params: Parameters passed down from the generate_actions hook.

        .. note::
            This is the default entry point for the hook. It reuses the
            ``execute_action`` method for backward compatibility with hooks
            written for the previous version of the loader.

        .. note::
            The hook will stop applying the actions on the selection if an error
            is raised midway through.

        :param list actions: Action dictionaries.
        """
        for single_action in actions:
            name = single_action["name"]
            sg_publish_data = single_action["sg_publish_data"]
            params = single_action["params"]
            self.execute_action(name, params, sg_publish_data)

    def execute_action(self, name, params, sg_publish_data):
        """
        Execute a given action. The data sent to this be method will
        represent one of the actions enumerated by the generate_actions method.

        :param name: Action name string representing one of the items returned
                     by generate_actions.
        :param params: Params data, as specified by generate_actions.
        :param sg_publish_data: Shotgun data dictionary with all the standard
                                publish fields.
        """
        app = self.parent
        app.log_debug("Execute action called for action %s. "
                      "Parameters: %s. Publish Data: %s" % (name, params, sg_publish_data))

        # resolve path
        # toolkit uses utf-8 encoded strings internally and the After Effects API expects unicode
        # so convert the path to ensure filenames containing complex characters are supported
        path = self.get_publish_path(sg_publish_data).decode('utf-8')

        if self.parent.engine.is_adobe_sequence(path):
            frame_range = self.parent.engine.find_sequence_range(path)
            if frame_range:
                glob_path = re.sub("[\[]?([#@]+|%0\d+d)[\]]?", "*{}".format(frame_range[0]), path)
                for each_path in sorted(glob.glob(glob_path)):
                    path = each_path
                    break

        if not os.path.exists(path):
            raise IOError("File not found on disk - '%s'" % path)

        if name == _ADD_TO_COMP:
            self._add_to_comp(path)
        if name == _ADD_TO_PROJECT:
            self.parent.engine.import_filepath(path)

    ###########################################################################
    # helper methods

    def _add_to_comp(self, path):
        """
        Helper method to add the footage described by path to a comp
        """
        adobe = self.parent.engine.adobe
        comp_item = adobe.app.project.activeItem
        if not comp_item or not self.parent.engine.is_item_of_type(comp_item, self.parent.engine.AdobeItemTypes.COMP_ITEM):
            return False

        new_items = self.parent.engine.import_filepath(path)

        for item in new_items:
            if self.parent.engine.is_item_of_type(item, self.parent.engine.AdobeItemTypes.FOLDER_ITEM):
                self.parent.engine.add_items_to_comp(item.items, comp_item)
                continue
            comp_item.layers.add(item)

        return True

