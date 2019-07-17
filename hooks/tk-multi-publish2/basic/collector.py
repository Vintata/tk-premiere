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


class PremiereSceneCollector(HookBaseClass):
    """
    Collector that operates on the current Premiere document. Should inherit
    from the basic collector hook.
    """

    @property
    def settings(self):
        """
        Dictionary defining the settings that this collector expects to receive
        through the settings parameter in the process_current_session and
        process_file methods.

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

        # grab any base class settings
        collector_settings = super(PremiereSceneCollector, self).settings or {}

        # settings specific to this collector
        premiere_session_settings = {
            "Work Template": {
                "type": "template",
                "default": None,
                "description": "Template path for artist work files. Should "
                               "correspond to a template defined in "
                               "templates.yml. If configured, is made available"
                               "to publish plugins via the collected item's "
                               "properties. ",
            },
        }

        # update the base settings with these settings
        collector_settings.update(premiere_session_settings)

        return collector_settings

    def process_current_session(self, settings, parent_item):
        """
        Analyzes the open documents in Premiere and creates publish items
        parented under the supplied item.

        :param dict settings: Configured settings for this collector
        :param parent_item: Root item instance
        """

        # check if the current project was saved already
        # if not we will not add a publish item for it
        parent_item = self.__get_project_publish_item(settings, parent_item)

    def __icon_path(self):
        return os.path.join(
            self.disk_location,
            os.pardir,
            "icons",
            "premiere.png"
        )

    def __get_work_template_for_item(self, settings):
        # try to get the work-template
        work_template_setting = settings.get("Work Template")
        if work_template_setting:
            return self.parent.engine.get_template_by_name(
                work_template_setting.value)

    def __get_project_publish_item(self, settings, parent_item):
        """
        Will create a project publish item.

        :param settings: dict. Configured settings for this collector
        :param parent_item: Root item instance
        :returns: the newly created project item
        """
        project_name = "Untitled"
        path = self.parent.engine.project_path
        if path:
            project_name = self.parent.engine.adobe.app.project.name
        project_item = parent_item.create_item(
            "premiere.project",
            "Premiere Scene",
            project_name
        )
        self.logger.info(
            "Collected Premiere document: {}".format(project_name))

        project_item.set_icon_from_path(self.__icon_path())
        project_item.thumbnail_enabled = True
        project_item.properties["file_path"] = path
        project_item.properties["published_renderings"] = []
        if path:
            project_item.set_thumbnail_from_path(path)

        work_template = self.__get_work_template_for_item(settings)
        if work_template is not None:
            project_item.properties["work_template"] = work_template
            self.logger.debug("Work template defined for Premiere collection.")
        return project_item

