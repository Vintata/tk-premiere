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
import sys
import shutil


import sgtk
from sgtk.util.filesystem import ensure_folder_exists


HookBaseClass = sgtk.get_hook_baseclass()


class AfterEffectsCopyRenderPlugin(HookBaseClass):
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
            "copy.png"
        )

    @property
    def description(self):
        """
        Verbose, multi-line description of what the plugin does. This can
        contain simple html for formatting.
        """

        return """
        This Publish plugin will take care of copying the render output files generated
        by the associated render queue item to the configured publish location.

        When configuring this plugin please be aware of the following:

        1. You need to set default output-module templates for movies and sequences.
            These output modules should exists at the artists machine.
        2. The publish-path-templates for sequences and movies should match the given output-module-templates.
            This means, that you cannot configue an output-module-template, which renders a '*.mov' file
            while your publish-path-template defines an extension like '*.avi'
        3. You may enforce the use of your configured output module templates.
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
            super(AfterEffectsCopyRenderPlugin, self).settings or {}

        # settings specific to this class
        aftereffects_publish_settings = {
            "Publish Sequence Template": {
                "type": "template",
                "default": None,
                "description": "Template path for published work files. Should"
                               "correspond to a template defined in "
                               "templates.yml. Please note, that the file "
                               "extension will be controlled by the option "
                               "'Default Output Module', so the template given "
                               "here should match the configured output-module.",
            },
            "Default Sequence Output Module": {
                "type": "str",
                "default": None,
                "description": "The output module to be chosen "
                               "in case no output module has "
                               "been set. This will control the "
                               "rendersettings.",
            },
            "Publish Movie Template": {
                "type": "template",
                "default": None,
                "description": "Template path for published work files. Should"
                               "correspond to a template defined in "
                               "templates.yml. Please note, that the file "
                               "extension will be controlled by the option "
                               "'Default Output Module', so the template given "
                               "here should match the configured output-module.",
            },
            "Default Movie Output Module": {
                "type": "str",
                "default": None,
                "description": "The output module to be chosen "
                               "in case no output module has "
                               "been set. This will control the "
                               "rendersettings.",
            },
            "Check Output Module": {
                "type": "bool",
                "default": True,
                "description": "Indicates, wether to check the "
                               "output module name of the given "
                               "render queue item. If 'Force "
                               "Output Module' is not set, don't "
                               "check the item.",
            },
            "Force Output Module": {
                "type": "bool",
                "default": True,
                "description": "Indicates, wether the configured "
                               "output module should be enforced, "
                               "in case the output module check "
                               "failed.",
            },
        }

        # update the base settings
        base_settings.update(aftereffects_publish_settings)

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
        return super(AfterEffectsCopyRenderPlugin, self).validate(
            settings, item)

    def publish(self, settings, item):
        """
        Executes the publish logic for the given item and settings.

        :param settings: Dictionary of Settings. The keys are strings, matching
            the keys returned in the settings property. The values are `Setting`
            instances.
        :param item: Item to process
        """
        render_paths = list(item.properties.get("renderpaths", []))

        # in case we have templates

        # we get the neccessary settings
        queue_item = item.properties.get("queue_item")
        queue_item_index = item.properties.get("queue_item_index", "")
        work_template = item.properties.get("work_template")

        render_seq_path_template_str = settings.get('Publish Sequence Template').value or ''
        render_seq_path_template = self.parent.engine.tank.templates.get(render_seq_path_template_str)

        render_mov_path_template_str = settings.get('Publish Movie Template').value or ''
        render_mov_path_template = self.parent.engine.tank.templates.get(render_mov_path_template_str)

        new_renderpaths = []
        for each_path in self.__iter_publishable_paths(
                queue_item,
                queue_item_index,
                render_paths,
                work_template,
                render_mov_path_template,
                render_seq_path_template):

            new_renderpaths.append(each_path)

        item.properties["renderpaths"] = new_renderpaths

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
        work_template = item.properties.get("work_template")
        project_path = sgtk.util.ShotgunPath.normalize(self.parent.engine.project_path)

        default_seq_output_module = settings.get('Default Sequence Output Module').value
        default_mov_output_module = settings.get('Default Movie Output Module').value
        render_seq_path_template_str = settings.get('Publish Sequence Template').value or ''
        render_mov_path_template_str = settings.get('Publish Movie Template').value or ''
        check_output_module = settings.get('Check Output Module').value
        force_output_module = settings.get('Force Output Module').value

        render_seq_path_template = self.parent.engine.tank.templates.get(render_seq_path_template_str)
        render_mov_path_template = self.parent.engine.tank.templates.get(render_mov_path_template_str)

        # set the item path to some temporary value
        for each_path in render_paths:
            item.properties["path"] = re.sub('\[#\+]', '#', each_path)
            break

        if queue_item is None:
            self.logger.warn(("No queue_item was set. This is most likely due to "
                              "a mismatch of the collector and this publish-plugin."))
            return self.REJECTED

        if not project_path:
            self.logger.warn(
                "Project has to be saved in order to allow publishing renderings",
                extra=self.__get_save_as_action())
            return self.REJECTED

        # check if the current configuration has templates assigned
        if not work_template:
            self.logger.warn(("Copy the render-files to the publish location"
                              "will only work in case templates are enabled"))
            return self.REJECTED

        # we now know, that we have templates available, so we can do extended checking

        # check output module configuration
        om_state = self.__output_modules_acceptable(
            item,
            queue_item,
            default_mov_output_module,
            default_seq_output_module,
            check_output_module,
            force_output_module
        )
        if om_state != self.FULLY_ACCEPTED:
            return om_state

        # TODO: This is a hack to support multiple different extensions
        # per operating system ("avi" on windows and "mov" on mac)
        # It should go away with issue #8
        if default_mov_output_module == "Lossless with Alpha" and "extension" in render_mov_path_template.keys:
            render_mov_path_template.keys["extension"].default = "avi" if sys.platform == "win32" else "mov"

        # check template configuration
        t_state = self.__templates_acceptable(
            work_template,
            render_seq_path_template,
            render_mov_path_template,
            project_path
        )
        if t_state != self.FULLY_ACCEPTED:
            return t_state

        # in case we will render before publishing we
        # have to check if the templates are matching
        ext_state = self.__template_extension_match_render_paths(
            render_paths,
            render_seq_path_template,
            render_mov_path_template,
        )
        if ext_state != self.FULLY_ACCEPTED:
            return ext_state

        return self.FULLY_ACCEPTED

    def __template_extension_match_render_paths(
            self, render_paths, seq_template, mov_template):
        """
        Helper method to verify that the template extensions are matching the
        extensions of the render paths. This helper is called during verification
        and acceptance checking.

        :param render_paths: list of strings describing after-effects style render files. Sequences are marked like [####]
        :param seq_template: publish template for image-sequences
        :param mov_template: publish template for movie-clips
        """

        for each_path in render_paths:
            path_template = mov_template
            if self.parent.engine.is_adobe_sequence(each_path):
                path_template = seq_template

            # TODO: This is a hack to support multiple different extensions
            # per operating system ("avi" on windows and "mov" on mac)
            # It should go away with issue #8
            template_ext = None
            if "extension" in path_template.keys and path_template.keys["extension"].default:
                template_ext = re.sub("^[\.]*", ".", path_template.keys["extension"].default)
            if template_ext is None:
                _, template_ext = os.path.splitext(path_template.definition)
            _, path_ext = os.path.splitext(each_path)

            if path_ext != template_ext:
                self.logger.error(("Configuration Error: The template extension {} is not matching"
                                  "the render output path extension {} for "
                                  "path {!r}").format(template_ext, path_ext, each_path))
                return self.PARTIALLY_ACCEPTED
        return self.FULLY_ACCEPTED

    def __templates_acceptable(
            self, work_template, seq_template,
            mov_template, project_path):
        """
        Helper method to verify that the configured templates are valid.
        To do this, this method checks for the missing keys when initial fields
        were calculated from the current work-file. If the number of keys doesn't
        exceed the expected number the test passes.
        This helper is called during verification and acceptance checking.

        :param work_template: template matching the current work scene
        :param seq_template: publish template for image-sequences
        :param mov_template: publish template for movie-clips
        :param project_path: str file path to the current work file
        """
        expected_missing_keys = ['comp', 'width', 'height']
        msg = ("The file-path of this project must resolve "
               "most all template fields of the 'Publish {} Template'. "
               "The following keys can be ignored: {}.\nStill missing are: "
               "{!r}\nPlease change the template or save to a different "
               "context.")

        fields_from_work_template = work_template.get_fields(project_path)

        missing_seq_keys = [e for e in seq_template.missing_keys(fields_from_work_template) if seq_template.keys[e].default is None]
        missing_mov_keys = [e for e in mov_template.missing_keys(fields_from_work_template) if mov_template.keys[e].default is None]

        if set(missing_seq_keys) - set(['SEQ'] + expected_missing_keys):
            self.logger.warn(msg.format('Sequence', ['SEQ'] + expected_missing_keys, missing_seq_keys))
            return self.REJECTED

        if set(missing_mov_keys) - set(expected_missing_keys):
            self.logger.warn(msg.format('Movie', expected_missing_keys, missing_mov_keys))
            return self.REJECTED

        return self.FULLY_ACCEPTED

    def __output_modules_acceptable(
            self, publish_item, queue_item, mov_template,
            seq_template, check=True, force=True):
        """
        Helper that verifies, that all the output modules are configured correctly.
        It will perform extended checking if check is True. This means, that
        each output-module will be compared with the configured output module templates.
        In case force is not set verification will fail if the latter check fails, if
        force is set, the output-module will be set to the configured template.

        :param publish_item: the current publisher item
        :param queue_item: an after effects render-queue-item
        :param mov_template: str name of the output module template for movie-clips
        :param seq_template: str name of the output module template for image-sequences
        :param check: bool indicating if extended checking should be performed (see above)
        :param force: bool indicating that a fix should be applied in case extended checking fails
        """
        adobe = self.parent.engine.adobe
        # check configuration
        output_module_names = []
        for i, output_module in enumerate(self.parent.engine.iter_collection(queue_item.outputModules)):

            # first we check if the configured templates are actually existing
            # in after effects or not.
            if not i:
                output_module_names = list(output_module.templates)
                msg = ("The configured output module has to exist in After Effects. "
                       "Please configure one of: {!r}\nYou configured: {!r}")
                if seq_template not in output_module_names:
                    self.logger.warn(msg.format(output_module_names, seq_template))
                    return self.REJECTED
                if mov_template not in output_module_names:
                    self.logger.warn(msg.format(output_module_names, mov_template))
                    return self.REJECTED

            # for extra security, we check, wether the output module
            # is pointing to a valid file. This should only fail in
            # race conditions
            if output_module.file == None:
                self.logger.warn(("There render queue item contains an "
                                  "output module, that has no output file set."
                                  "Please set a file to the output module no {}").format(i))
                return self.REJECTED

            # getting the template to use for this output module.
            template_name = mov_template
            if self.parent.engine.is_adobe_sequence(output_module.file.fsName):
                template_name = seq_template

            # if we don't check or the check is OK, we can continue
            if not check or output_module.name == template_name:
                continue

            acceptable_states = [
                adobe.RQItemStatus.DONE,
                adobe.RQItemStatus.ERR_STOPPED,
                adobe.RQItemStatus.RENDERING
            ]

            # if the fix output module is configured, we can apply the fix
            # and continue

            def fix_output_module(
                    output_module=output_module, template=template_name, queue_item=queue_item,
                    item=publish_item, engine=self.parent.engine):
                """
                local method to change the output module template of the item and update the renderpaths
                """
                output_module.applyTemplate(template)
                renderpaths = []
                for output_module in engine.iter_collection(queue_item.outputModules):
                    renderpaths.append(output_module.file.fsName)
                item.properties["renderpaths"] = renderpaths

            if force and queue_item.status not in acceptable_states:
                self.logger.info("Forcing Output Module to follow template {!r}".format(template_name))
                fix_output_module()
                continue

            extra = None
            if queue_item.status not in acceptable_states:
                extra = {
                    "action_button": {
                        "label": "Force Output Module...",
                        "tooltip": "Sets the template on the output module.",
                        "callback": fix_output_module,
                    }
                }

            self.logger.warn(
                ("Configuration Error: Output Module template {!r} doesn't "
                 "match the configured one {!r}.").format(
                    output_module.name, template_name),
                extra=extra
            )
            return self.PARTIALLY_ACCEPTED
        return self.FULLY_ACCEPTED

    def __iter_publishable_paths(
            self, queue_item, queue_item_idx, render_paths,
            work_template, mov_template, seq_template):
        """
        Helper method to copy and iter all renderfiles to the configured publish location

        :param queue_item: the render queue item
        :param queue_item_idx: integer, that describes the number of the queue_item in the after effects render queue. 
        :param render_paths: list of strings describing after-effects style render files. Sequences are marked like [####]
        :param work_template: the template for the current work-file 
        :param mov_template: the publish template for movie-clips
        :param seq_template: the publish template for image-sequences
        :yields: an abstract render-file-path (str) that has a format expression (like %04d) at the frame numbers position 
        """

        # get the neccessary template fields from the..
        # ..work-template
        project_path = self.parent.engine.project_path
        fields_from_work_template = work_template.get_fields(
            sgtk.util.ShotgunPath.normalize(project_path))

        # ..and from the queue_item.
        comp_name = "{}rq{}".format(queue_item.comp.name, queue_item_idx)
        fields_from_work_template.update({
            "comp": re.sub("[^0-9a-zA-Z]", "", comp_name),
            "width": queue_item.comp.width,
            "height": queue_item.comp.height,
        })

        for each_path in render_paths:
            # get the path in a normalized state. no trailing separator, separators
            # are appropriate for current os, no double separators, etc.
            each_path = sgtk.util.ShotgunPath.normalize(each_path)

            # check whether the given path points to a sequence
            is_sequence = self.parent.engine.is_adobe_sequence(each_path)

            # get the template to use depending if
            # the rendering is an image sequence or
            # a movie-container
            template = mov_template
            if is_sequence:
                template = seq_template
                fields_from_work_template['SEQ'] = '%{}d'.format(template.keys['SEQ'].format_spec)

            # build the target file path with formattable frame numbers
            abstract_target_path = template.apply_fields(fields_from_work_template)
            ensure_folder_exists(os.path.dirname(abstract_target_path))

            # copy the files to the publish location
            target_path = None
            for file_path, frame_no in self.parent.engine.get_render_files(each_path, queue_item):
                target_path = abstract_target_path
                if is_sequence:
                    target_path = abstract_target_path % frame_no
                shutil.copy2(file_path, target_path)

            # in case no file was copied, we skip
            # registering this publish path
            if target_path is None:
                continue

            # in case at least one file was copied,
            # we build an abstract target_path and
            # register that.
            yield abstract_target_path

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


