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
import sys

import sgtk
import sgtk.platform.framework
from sgtk.platform import SoftwareLauncher, SoftwareVersion, LaunchInformation


class EngineConfigurationError(sgtk.TankError):
    pass


class AfterEffectsLauncher(SoftwareLauncher):
    """
    Handles the launching of After Effects. Contains the logic for
    scanning for installed versions of the software and
    how to correctly set up a launch environment for the tk-aftereffects
    engine.
    """

    # Named regex strings to insert into the executable template paths when
    # matching against supplied versions and products. Similar to the glob
    # strings, these allow us to alter the regex matching for any of the
    # variable components of the path in one place
    COMPONENT_REGEX_LOOKUP = {
        "version": "[\d.]+",
        "version_back": "[\d.]+",  # backreference to ensure same version
    }

    # This dictionary defines a list of executable template strings for each
    # of the supported operating systems. The templates are used for both
    # globbing and regex matches by replacing the named format placeholders
    # with an appropriate glob or regex string. As Adobe adds modifies the
    # install path on a given OS for a new release, a new template will need
    # to be added here.
    EXECUTABLE_MATCH_TEMPLATES = {
        # /Applications/Adobe After Effects CC 2017/After Effects CC 2017.app
        "darwin": "/Applications/Adobe After Effects CC {version}/Adobe After Effects CC {version_back}.app",
        # C:\program files\Adobe\Adobe After Effects CC 2017\AfterFX.exe
        "win32": "C:/Program Files/Adobe/Adobe After Effects CC {version}/Support Files/AfterFX.exe"
    }

    @property
    def minimum_supported_version(self):
        """
        The minimum software version that is supported by the launcher.
        """
        return "2015.5"

    def prepare_launch(self, exec_path, args, file_to_open=None):
        """
        Prepares an environment to launch After Effects so that will automatically
        load Toolkit after startup.

        :param str exec_path: Path to Maya executable to launch.
        :param str args: Command line arguments as strings.
        :param str file_to_open: (optional) Full path name of a file to open on launch.
        :returns: :class:`LaunchInformation` instance
        """

        # determine all environment variables
        required_env = self.compute_environment()

        # Add std context and site info to the env
        std_env = self.get_standard_plugin_environment()
        required_env.update(std_env)

        return LaunchInformation(exec_path, args, required_env)

    def scan_software(self):
        """
        Scan the filesystem for all After Effects executables.

        :return: A list of :class:`SoftwareVersion` objects.
        """

        self.logger.debug("Scanning for After Effects executables...")

        # use the bundled icon
        icon_path = os.path.join(
            self.disk_location,
            "icon_256.png"
        )
        self.logger.debug("Using icon path: %s" % (icon_path,))

        if sys.platform not in self.EXECUTABLE_MATCH_TEMPLATES:
            self.logger.debug("After Effects not supported on this platform.")
            return []

        all_sw_versions = []

        for executable_path, tokens in self._glob_and_match(
                self.EXECUTABLE_MATCH_TEMPLATES[sys.platform],
                self.COMPONENT_REGEX_LOOKUP):
            self.logger.debug(
                "Processing %s with tokens %s",
                executable_path,
                tokens
            )
            # extract the components (default to None if not included). but
            # version is in all templates, so should be there.
            executable_version = tokens.get("version")

            sw_version = SoftwareVersion(
                executable_version,
                "After Effects CC",
                executable_path,
                icon_path
            )
            supported, reason = self._is_supported(sw_version)
            if supported:
                all_sw_versions.append(sw_version)
            else:
                self.logger.debug(reason)

        return all_sw_versions

    def compute_environment(self):
        """
        Return the env vars needed to launch the After Effects plugin.

        This will generate a dictionary of environment variables
        needed in order to launch the After Effects plugin.

        :returns: dictionary of env var string key/value pairs.
        """
        env = {}

        framework_location = self.__get_adobe_framework_location()
        if framework_location is None:
            raise EngineConfigurationError(
                ("The tk-framework-adobe "
                 "could not be found in the current environment. "
                 "Please check the log for more information.")
            )

        self.__ensure_framework_is_installed(framework_location)

        # set the interpreter with which to launch the CC integration
        env["SHOTGUN_ADOBE_PYTHON"] = sys.executable
        env["SHOTGUN_ADOBE_FRAMEWORK_LOCATION"] = framework_location
        env["SHOTGUN_ENGINE"] = "tk-aftereffects"

        # We're going to append all of this Python process's sys.path to the
        # PYTHONPATH environment variable. This will ensure that we have access
        # to all libraries available in this process in subprocesses like the
        # Python process that is spawned by the Shotgun CEP extension on launch
        # of an Adobe host application. We're appending instead of setting because
        # we don't want to stomp on any PYTHONPATH that might already exist that
        # we want to persist when the Python subprocess is spawned.
        sgtk.util.append_path_to_env_var(
            "PYTHONPATH",
            os.pathsep.join(sys.path),
        )
        env["PYTHONPATH"] = os.environ["PYTHONPATH"]

        return env

    def __get_adobe_framework_location(self):
        """
        This helper method will query the current disc-location for the configured
        tk-adobe-framework.

        This is necessary, as the the framework relies on an environment variable
        to be set by the parent engine and also the CEP panel to be installed.

        TODO: When the following logic was implemented, there was no way of
            accessing the engine's frameworks at launch time. Once this is
            possible, this logic should be replaced.

        Returns (str or None): The tk-adobe-framework disc-location directory path
            configured under the tk-multi-launchapp
        """

        engine = sgtk.platform.current_engine()
        env_name = engine.environment.get("name")

        env = engine.tank.pipeline_configuration.get_environment(env_name)
        engine_desc = env.get_engine_descriptor("tk-aftereffects")
        if env_name is None:
            self.logger.warn(
                ("The current environment {!r} "
                 "is not configured to run the tk-aftereffects "
                 "engine. Please add the engine to your env-file: "
                 "{!r}").format(env, env.disk_location))
            return

        framework_name = None
        for req_framework in engine_desc.get_required_frameworks():
            if req_framework.get("name") == "tk-framework-adobe":
                name_parts = [req_framework["name"]]
                if "version" in req_framework:
                    name_parts.append(req_framework["version"])
                framework_name = "_".join(name_parts)
                break
        else:
            self.logger.warn(
                ("The engine tk-aftereffects must have "
                 "the tk-framework-adobe configured in order to run"))
            return

        desc = env.get_framework_descriptor(framework_name)
        return desc.get_path()

    def __ensure_framework_is_installed(self, framework_location):
        """
        This method calls the frameworks CEP extension installation
        logic.
        """

        # TODO: The following import should be replaced with
        # a more a call like import_framework, once one has
        # access to the configured frameworks at engine start.
        bootstrap_python_path = os.path.join(framework_location, "python")

        sys.path.insert(0, bootstrap_python_path)
        import tk_framework_adobe_utils.startup as startup_utils
        sys.path.remove(bootstrap_python_path)

        # installing the CEP extension.
        startup_utils.ensure_extension_up_to_date(self.logger)
