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


class AfterEffectsSceneCollector(HookBaseClass):
    """
    Collector that operates on the current After Effects document. Should inherit
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
        collector_settings = \
            super(AfterEffectsSceneCollector, self).settings or {}

        # settings specific to this collector
        aftereffects_session_settings = {
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
        collector_settings.update(aftereffects_session_settings)

        return collector_settings

    def process_current_session(self, settings, parent_item):
        """
        Analyzes the open documents in After Effects and creates publish items
        parented under the supplied item.

        :param dict settings: Configured settings for this collector
        :param parent_item: Root item instance
        """
        adobe = self.parent.engine.adobe

        # check if the current project was saved already
        # if not we will not add a publish item for it
        parent_item = self.__get_project_publish_item(settings, parent_item)
        if adobe.app.project.file == None:
            return

        work_template = self.__get_work_template_for_item(settings)

        # itering through the render queue items
        for i, queue_item in enumerate(self.parent.engine.iter_collection(adobe.app.project.renderQueue.items)):
            if queue_item.status not in [adobe.RQItemStatus.QUEUED, adobe.RQItemStatus.DONE]:
                continue

            render_paths = []
            for output_module in self.parent.engine.iter_collection(queue_item.outputModules):
                render_paths.append(output_module.file.fsName)

            action = "register only"
            comment = "Registers the Rendered Imagepaths"
            if work_template:
                if queue_item.status == adobe.RQItemStatus.DONE:
                    action = "copy"
                    comment = "Register & Copy Rendered Images to the Publishpath"
                else:
                    action = "render"
                    comment = "Render, Register & Copy Images to the Publishpath"

            comp_item_name = "Render Queue Item #{} - {} - {}".format(
                i + 1, queue_item.comp.name,
                action
            )

            self.__create_comp_publish_item(
                parent_item, comp_item_name, comment,
                queue_item, render_paths, i,
                work_template
            )

            self.logger.info("Collected After Effects renderings: {}".format(comp_item_name))

    def __icon_path(self):
        return os.path.join(
            self.disk_location,
            os.pardir,
            "icons",
            "aftereffects.png"
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
            project_name = self.parent.engine.adobe.app.project.file.name
        project_item = parent_item.create_item(
            "aftereffects.project",
            "After Effects Scene",
            project_name
        )
        self.logger.info(
            "Collected After Effects document: {}".format(project_name))

        project_item.set_icon_from_path(self.__icon_path())
        project_item.thumbnail_enabled = True
        project_item.properties["file_path"] = path
        project_item.properties["published_renderings"] = []
        if path:
            project_item.set_thumbnail_from_path(path)

        work_template = self.__get_work_template_for_item(settings)
        if work_template is not None:
            project_item.properties["work_template"] = work_template
            self.logger.debug("Work template defined for After Effects collection.")
        return project_item

    def __create_comp_publish_item(self, parent_item, name, comment, queue_item, render_paths, queue_index, work_template=None):
        """
        Will create a comp publish item.

        :param parent_item: Root item instance
        :param name: str name of the new item
        :param comment: str comment/subtitle of the comp item
        :param queue_item: adobe.RenderQueueItem item to be associated with the comp item
        :param render_paths: list-of-str. filepaths to be expected from the render queue item. Sequence-paths
                should use the adobe-style sequence pattern [###]
        :param queue_index: int. The number of the render queue item within the render queue. Index starting at 0!
        :param work_template: Template. The configured work template
        :returns: the newly created comp item
        """
        # create a publish item for the document
        comp_item = parent_item.create_item(
            "aftereffects.rendering",
            comment,
            name
        )

        comp_item.set_icon_from_path(self.__icon_path())

        # disable thumbnail creation for After Effects documents. for the
        # default workflow, the thumbnail will be auto-updated after the
        # version creation plugin runs
        comp_item.thumbnail_enabled = False
        comp_item.context_change_allowed = False

        comp_item.properties["queue_item_index"] = queue_index
        comp_item.properties["queue_item"] = queue_item
        comp_item.properties["renderpaths"] = render_paths

        # enable the rendered render queue items and expand it. other documents are
        # collapsed and disabled.
        if queue_item.status == self.parent.engine.adobe.RQItemStatus.DONE:
            comp_item.expanded = True
            comp_item.checked = True
        else:
            # there is an active document, but this isn't it. collapse and
            # disable this item
            comp_item.expanded = False
            comp_item.checked = False

        for path in render_paths:
            comp_item.set_thumbnail_from_path(path)
            break

        if work_template:
            comp_item.properties["work_template"] = work_template
            self.logger.debug("Work template defined for After Effects collection.")
        return comp_item


