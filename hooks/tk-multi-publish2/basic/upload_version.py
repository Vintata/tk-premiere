# Copyright (c) 2019 Shotgun Software Inc.
# 
# CONFIDENTIAL AND PROPRIETARY
# 
# This work is provided "AS IS" and subject to the Shotgun Pipeline Toolkit 
# Source Code License included in this distribution package. See LICENSE.
# By accessing, using, copying or modifying this work you indicate your 
# agreement to the Shotgun Pipeline Toolkit Source Code License. All rights 
# not expressly granted therein are reserved by Shotgun Software Inc.
import re
import os
import pprint
import tempfile
import sys


import sgtk


HookBaseClass = sgtk.get_hook_baseclass()


class ProjectUnsavedError(Exception):
    pass


class RenderingFailed(Exception):
    pass


class AfterEffectsUploadVersionPlugin(HookBaseClass):
    """
    Plugin for sending aftereffects documents to shotgun for review.
    """

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
            "review.png"
        )

    @property
    def name(self):
        """
        One line display name describing the plugin
        """
        return "Upload for review"

    @property
    def description(self):
        """
        Verbose, multi-line description of what the plugin does. This can
        contain simple html for formatting.
        """
        publisher = self.parent

        shotgun_url = publisher.sgtk.shotgun_url

        media_page_url = "%s/page/media_center" % (shotgun_url,)
        review_url = "https://www.shotgunsoftware.com/features/#review"

        return """
        Upload the file to Shotgun for review.<br><br>

        A <b>Version</b> entry will be created in Shotgun and a transcoded
        copy of the file will be attached to it. The file can then be reviewed
        via the project's <a href='%s'>Media</a> page, <a href='%s'>RV</a>, or
        the <a href='%s'>Shotgun Review</a> mobile app.
        """ % (media_page_url, review_url, review_url)

    @property
    def settings(self):
        """
        Dictionary defining the settings that this plugin expects to recieve
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
        return {
            "Movie Output Module": {
                "type": "str",
                "default": "Lossless with Alpha",
                "description": "The output module to be chosen "
                               "in case no output module has "
                               "been set. This will control the "
                               "rendersettings.",
            }
        }

    @property
    def item_filters(self):
        """
        List of item types that this plugin is interested in.

        Only items matching entries in this list will be presented to the
        accept() method. Strings can contain glob patters such as *, for example
        ["maya.*", "file.maya"]
        """

        # we use "video" since that's the mimetype category.
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

        path = sgtk.util.ShotgunPath.normalize(self.parent.engine.project_path)

        if not path:
            # the project has not been saved before (no path determined).
            # provide a save button. the project will need to be saved before
            # validation will succeed.
            self.logger.warn(
                "The After Effects project has not been saved.",
                extra=self.__get_save_as_action()
            )

        self.logger.info(
            "After Effects '%s' plugin accepted." %
            (self.name,)
        )
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

        path = sgtk.util.ShotgunPath.normalize(self.parent.engine.project_path)

        if not path:
            # the project still requires saving. provide a save button.
            # validation fails.
            error_msg = "The After Effects project has not been saved."
            self.logger.error(
                error_msg,
                extra=self.__get_save_as_action()
            )
            raise ProjectUnsavedError(error_msg)

        return True

    def publish(self, settings, item):
        """
        Executes the publish logic for the given item and settings.

        :param settings: Dictionary of Settings. The keys are strings, matching
            the keys returned in the settings property. The values are `Setting`
            instances.
        :param item: Item to process
        """

        publisher = self.parent

        queue_item = item.properties.get("queue_item")
        render_paths = list(item.properties.get("renderpaths"))

        upload_path = None
        path_to_movie = None
        path_to_frames = None
        while render_paths and (not all([upload_path, path_to_movie, path_to_frames])):
            each_path = render_paths.pop(0)
            if not self.parent.engine.is_adobe_sequence(each_path):
                path_to_movie = each_path
                upload_path = each_path
            else:
                path_to_frames = each_path

        mov_output_module_template = settings.get('Movie Output Module').value
        if path_to_movie is None and path_to_frames is not None:
            self.logger.info("About to render movie...")
            upload_path = self.__render_movie_from_sequence(path_to_frames, queue_item, mov_output_module_template)
            if not upload_path:
                raise RenderingFailed("Rendering a movie failed. Cannot upload a version of this item.")
        elif path_to_movie and not os.path.exists(path_to_movie):
            self.logger.info("About to render movie...")
            temp_queue_item = queue_item.duplicate()
            upload_path = self.__render_to_temp_location(temp_queue_item, mov_output_module_template)
            temp_queue_item.remove()
            if not upload_path:
                raise RenderingFailed("Rendering a movie failed. Cannot upload a version of this item.")

        if upload_path is None:
            self.logger.error("No render path found")
            return

        # if we got a sequence, we need to set additional information
        additional_version_data = self.__get_additional_version_data(queue_item, path_to_frames)

        # use the path's filename as the publish name
        path_components = publisher.util.get_file_path_components(path_to_movie or path_to_frames)
        publish_name = path_components["filename"]

        # populate the version data to send to SG
        self.logger.info("Creating Version...")
        version_data = {
            "project": item.context.project,
            "code": publish_name,
            "description": item.description,
            "entity": self._get_version_entity(item),
            "sg_task": item.context.task,
            "sg_path_to_frames": path_to_frames,
            "sg_path_to_movie": path_to_movie,
        }
        version_data.update(additional_version_data)

        publish_data = item.properties.get("sg_publish_data")
        rendering_data = item.properties.get("published_renderings", [])

        # if the file was published, add the publish data to the version
        version_data["published_files"] = []
        if publish_data:
            version_data["published_files"].append(publish_data)
        version_data["published_files"].extend(rendering_data)

        # log the version data for debugging
        self.logger.debug(
            "Populated Version data...",
            extra={
                "action_show_more_info": {
                    "label": "Version Data",
                    "tooltip": "Show the complete Version data dictionary",
                    "text": "<pre>%s</pre>" % (
                        pprint.pformat(version_data),)
                }
            }
        )

        # create the version
        self.logger.info("Creating version for review...")
        version = self.parent.shotgun.create("Version", version_data)

        # stash the version info in the item just in case
        item.properties["sg_version_data"] = version

        # on windows, ensure the path is utf-8 encoded to avoid issues with
        # the shotgun api
        if sys.platform.startswith("win"):
            upload_path = upload_path.decode("utf-8")

        # upload the file to SG
        self.logger.info("Uploading content...")
        self.parent.shotgun.upload(
            "Version",
            version["id"],
            upload_path,
            "sg_uploaded_movie"
        )
        self.logger.info("Upload complete!")

        item.properties["upload_path"] = upload_path

    def finalize(self, settings, item):
        """
        Execute the finalization pass. This pass executes once all the publish
        tasks have completed, and can for example be used to version up files.

        :param settings: Dictionary of Settings. The keys are strings, matching
            the keys returned in the settings property. The values are `Setting`
            instances.
        :param item: Item to process
        """

        version = item.properties["sg_version_data"]

        self.logger.info(
            "Version uploaded for After Effects document",
            extra={
                "action_show_in_shotgun": {
                    "label": "Show Version",
                    "tooltip": "Reveal the version in Shotgun.",
                    "entity": version
                }
            }
        )

        upload_path = item.properties["upload_path"]

        # remove the tmp file
        if item.properties.get("remove_upload", False):
            try:
                os.remove(upload_path)
            except Exception:
                self.logger.warn(
                    "Unable to remove temp file: %s" % (upload_path,))
                pass

    def __render_movie_from_sequence(self, sequence_path, queue_item, mov_output_module_template):

        for first_frame, _ in self.parent.engine.get_render_files(sequence_path, queue_item):
            break

        # import the footage and add it to the render queue
        new_items = self.parent.engine.import_filepath(first_frame)
        for new_item in new_items:
            break
        else:
            return ''
        new_cmp_item = self.parent.engine.adobe.app.project.items.addComp(
            new_item.name,
            new_item.width,
            new_item.height,
            new_item.pixelAspect,
            new_item.duration,
            new_item.frameRate or 25
        )

        for new_item in new_items:
            new_cmp_item.layers.add(new_item)

        temp_item = self.parent.engine.adobe.app.project.renderQueue.items.add(new_cmp_item)
        output_path = self.__render_to_temp_location(temp_item, mov_output_module_template)

        # clean up temporary items
        temp_item.remove()
        new_cmp_item.remove()
        while new_items:
            new_items.pop().remove()
        return output_path

    def __render_to_temp_location(self, temporary_queue_item, mov_output_module_template):
        # set the output module
        output_module = temporary_queue_item.outputModules[1]
        output_module.applyTemplate(mov_output_module_template)

        # set the filepath to a temp location
        _, ext = os.path.splitext(output_module.file.fsName)

        allocate_file = tempfile.NamedTemporaryFile(suffix=ext)
        allocate_file.close()

        render_file = self.parent.engine.adobe.File(allocate_file.name)
        output_module.file = render_file

        # render
        render_state = self.parent.engine.render_queue_item(temporary_queue_item)

        # return the render file path or an empty string
        if render_state:
            return allocate_file.name
        return ''

    def __get_additional_version_data(self, queue_item, path_to_frames):
        if path_to_frames is None:
            return {}

        out_dict = {}
        frame_numbers = []
        for _, fn in self.parent.engine.get_render_files(path_to_frames, queue_item):
            frame_numbers.append(fn)
        out_dict['sg_first_frame'] = min(frame_numbers)
        out_dict['sg_last_frame'] = max(frame_numbers)
        out_dict['frame_range'] = "{}-{}".format(min(frame_numbers), max(frame_numbers))
        out_dict['frame_count'] = len(frame_numbers)
        match = re.search('[\[]?([#@]+)[\]]?', path_to_frames)
        if match:
            path_to_frames = path_to_frames.replace(match.group(0), '%0{}d'.format(len(match.group(1))))
        out_dict["sg_path_to_frames"] = path_to_frames

        # use the path's filename as the publish name
        path_components = self.parent.util.get_file_path_components(path_to_frames)
        out_dict["code"] = path_components["filename"]
        return out_dict

    def __check_rendered_item(self, item):
        queue_item = item.properties.get("queue_item")
        idx = item.properties.get("queue_item_index", '0')

        # as this plugin can only process rendered items,
        # we'll have to check if the given item is already
        # rendered. If not, we'll provide a render button.
        if queue_item.status != self.parent.engine.adobe.RQItemStatus.DONE:
            self.logger.warn(
                "Render item is not Done yet. Please render it first.",
                extra={
                    "action_button": {
                        "label": "Render Item {}".format(idx),
                        "tooltip": ("Render the queue item {} as"
                                    "movie, so it can be uploaded.").format(idx),
                        "callback": lambda qi=queue_item: self.parent.engine.render_queue_item(qi)
                    }
                }
            )
            return False
        return True

    def __check_renderings(self, item):
        queue_item = item.properties.get("queue_item")
        render_paths = item.properties.get("renderpaths")
        has_incomplete_renderings = False
        for each_path in render_paths:
            if not self.parent.engine.check_sequence(each_path, queue_item):
                has_incomplete_renderings = True

        if has_incomplete_renderings:
            self.logger.warn("Render Queue item has incomplete renderings, "
                             "please rerender this or duisable the queue item.")
            return False
        return True

    def _get_version_entity(self, item):
        """
        Returns the best entity to link the version to.
        """

        if item.context.entity:
            return item.context.entity
        elif item.context.project:
            return item.context.project
        else:
            return None

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


