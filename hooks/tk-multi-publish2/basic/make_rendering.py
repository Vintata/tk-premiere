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


import sgtk


HookBaseClass = sgtk.get_hook_baseclass()


class RenderingFailed(Exception):
    pass


class AfterEffectsRenderPlugin(HookBaseClass):
    """
    Plugin for publishing after effects renderings.

    This hook relies on functionality found in the base file publisher hook in
    the publish2 app and should inherit from it in the configuration. The hook
    setting for this plugin should look something like this::

        hook: "{self}/publish_file.py:{engine}/tk-multi-publish2/basic/publish_rendering.py"

    """

    REJECTED, PARTIALLY_ACCEPTED, FULLY_ACCEPTED = range(3)

    @property
    def icon(self):
        """
        Path to an png icon on disk
        """
        # look for icon one level up from this hook's folder in "icons" folder
        return os.path.join(
            self.disk_location,
            os.pardir,
            "icons",
            "rendering.png"
        )

    @property
    def description(self):
        """
        Verbose, multi-line description of what the plugin does. This can
        contain simple html for formatting.
        """
        return """
            Will render the given render queue item in case it didn't render before.
            In case not all frames were rendered, it is optional to actually render
            the item.
        """

    @property
    def settings(self):
        """
        Dictionary defining the settings that this plugin expects to receive
        through the settings parameter in the accept, validate, publish and
        finalize methods.

        A dictionary on the following form::

            {
                "Settings Name": {
                    "type": "settings_type",
                    "default": "default_value",
                    "description": "One line description of the setting"
            }

        The type string should be one of the data types that toolkit accepts as
        part of its environment configuration.
        """

        # inherit the settings from the base publish plugin
        base_settings = \
            super(AfterEffectsRenderPlugin, self).settings or {}

        return base_settings

    @property
    def item_filters(self):
        """
        List of item types that this plugin is interested in.

        Only items matching entries in this list will be presented to the
        accept() method. Strings can contain glob patters such as *, for example
        ["maya.*", "file.maya"]
        """
        return ["aftereffects.rendering"]

    def accept(self, settings, item):
        """
        Method called by the publisher to determine if an item is of any
        interest to this plugin. Only items matching the filters defined via the
        item_filters property will be presented to this method.

        A publish task will be generated for each item accepted here. Returns a
        dictionary with the following booleans:

            - accepted: Indicates if the plugin is interested in this value at
               all. Required.
            - enabled: If True, the plugin will be enabled in the UI, otherwise
                it will be disabled. Optional, True by default.
            - visible: If True, the plugin will be visible in the UI, otherwise
                it will be hidden. Optional, True by default.
            - checked: If True, the plugin will be checked in the UI, otherwise
                it will be unchecked. Optional, True by default.

        :param settings: Dictionary of Settings. The keys are strings, matching
            the keys returned in the settings property. The values are `Setting`
            instances.
        :param item: Item to process

        :returns: dictionary with boolean keys accepted, required and enabled
        """
        if self.__is_acceptable(settings, item) is self.REJECTED:
            return {"accepted": False}
        elif self.__is_acceptable(settings, item) is self.PARTIALLY_ACCEPTED:
            return {
                "accepted": True,
                "checked": False
            }
        return {
            "accepted": True,
            "checked": True
        }

    def validate(self, settings, item):
        """
        Validates the given item to check that it is ok to publish.

        Returns a boolean to indicate validity.

        :param settings: Dictionary of Settings. The keys are strings, matching
            the keys returned in the settings property. The values are `Setting`
            instances.
        :param item: Item to process

        :returns: True if item is valid, False otherwise.
        """
        if self.__is_acceptable(settings, item) is not self.FULLY_ACCEPTED:
            return False

        # run the base class validation
        return super(AfterEffectsRenderPlugin, self).validate(
            settings, item)

    def publish(self, settings, item):
        """
        Executes the publish logic for the given item and settings.

        :param settings: Dictionary of Settings. The keys are strings, matching
            the keys returned in the settings property. The values are `Setting`
            instances.
        :param item: Item to process
        """
        # we get the neccessary settings
        queue_item = item.properties.get("queue_item")
        queue_item_index = item.properties.get("queue_item_index", "")

        # render the queue item
        render_success = self.parent.engine.render_queue_item(queue_item)
        if not render_success:
            raise RenderingFailed("Rendering the render queue item {} failed.".format(queue_item_index))

    def __is_acceptable(self, settings, item):
        """
        This method is a helper to decide, whether the current publish item
        is valid. it is called from the validate and the accept method.

        :param settings: Dictionary of Settings. The keys are strings, matching
            the keys returned in the settings property. The values are `Setting`
            instances.
        :param item: Item to process

        :returns: int indicating the acceptance-level. One of
            REJECTED, PARTIALLY_ACCEPTED, FULLY_ACCEPTED
        """
        queue_item = item.properties.get("queue_item")
        render_paths = item.properties.get("renderpaths")
        project_path = sgtk.util.ShotgunPath.normalize(self.parent.engine.project_path)

        if queue_item is None:
            self.logger.warn(("No queue_item was set. This is most likely due to "
                              "a mismatch of the collector and this publish-plugin."))
            return self.REJECTED

        if not project_path:
            self.logger.warn(
                "Project has to be saved in order to allow publishing renderings",
                extra=self.__get_save_as_action()
            )
            return self.REJECTED

        # we now know, that we have templates available, so we can do extended checking
        if queue_item.status == self.parent.engine.adobe.RQItemStatus.DONE:
            if self.__render_files_existing(queue_item, render_paths) == self.REJECTED:
                return self.REJECTED

        return self.FULLY_ACCEPTED

    def __render_files_existing(self, queue_item, render_paths):
        """
        Helper that verifies, that all render-files are actually existing on disk.

        :param queue_item: an after effects render-queue-item
        :param render_paths: list of strings describing after-effects style render files. Sequences are marked like [####]
        """

        if not render_paths:
            self.logger.warn("No render path for the queue item. Please add at least one output module")
            return self.REJECTED

        has_incomplete_renderings = False
        for each_path in render_paths:
            if not self.parent.engine.check_sequence(each_path, queue_item):
                has_incomplete_renderings = True
            self.logger.info(("Render Queue item %s has incomplete renderings, "
                              "but status is DONE. "
                              "Rerendering is needed.") % (queue_item.comp.name,))
        if has_incomplete_renderings:
            return self.PARTIALLY_ACCEPTED

        self.logger.info(("No rendering needed in case the render queue item "
                          "is already rendered."))
        return self.REJECTED

    def __get_save_as_action(self):
        """
        Simple helper for returning a log action dict for saving the project
        """

        engine = self.parent.engine

        # default save callback
        callback = lambda: engine.save_as()

        # if workfiles2 is configured, use that for file save
        if "tk-multi-workfiles2" in engine.apps:
            app = engine.apps["tk-multi-workfiles2"]
            if hasattr(app, "show_file_save_dlg"):
                callback = app.show_file_save_dlg

        return {
            "action_button": {
                "label": "Save As...",
                "tooltip": "Save the active project",
                "callback": callback
            }
        }


