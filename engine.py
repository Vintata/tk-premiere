# Copyright (c) 2019 Shotgun Software Inc.
#
# CONFIDENTIAL AND PROPRIETARY
#
# This work is provided "AS IS" and subject to the Shotgun Pipeline Toolkit
# Source Code License included in this distribution package. See LICENSE.
# By accessing, using, copying or modifying this work you indicate your
# agreement to the Shotgun Pipeline Toolkit Source Code License. All rights
# not expressly granted therein are reserved by Shotgun Software Inc.

import logging
import os
import re
import glob
import math
import subprocess
import sys
import threading


from contextlib import contextmanager


import sgtk
from sgtk.util.filesystem import ensure_folder_exists


class AfterEffectsEngine(sgtk.platform.Engine):
    """
    An After Effects CC engine for Shotgun Toolkit.
    """

    # the maximum size for a generated thumbnail
    MAX_THUMB_SIZE = 512

    SHOTGUN_ADOBE_HEARTBEAT_INTERVAL = 1.0
    SHOTGUN_ADOBE_HEARTBEAT_TOLERANCE = 2
    SHOTGUN_ADOBE_NETWORK_DEBUG = ("SHOTGUN_ADOBE_NETWORK_DEBUG" in os.environ)

    TEST_SCRIPT_BASENAME = "run_tests.py"

    PY_TO_JS_LOG_LEVEL_MAPPING = {
        "CRITICAL": "error",
        "ERROR": "error",
        "WARNING": "warn",
        "INFO": "info",
        "DEBUG": "debug"
    }

    _SHOTGUN_ADOBE_PORT = os.environ.get("SHOTGUN_ADOBE_PORT")
    _SHOTGUN_ADOBE_APPID = os.environ.get("SHOTGUN_ADOBE_APPID")

    _COMMAND_UID_COUNTER = 0
    _LOCK = threading.Lock()
    _FAILED_PINGS = 0
    _CONTEXT_CACHE = dict()
    _CHECK_CONNECTION_TIMER = None
    _CONTEXT_CHANGES_DISABLED = False
    _DIALOG_PARENT = None
    _WIN32_AFTEREFFECTS_MAIN_HWND = None
    _PROXY_WIN_HWND = None
    _HEARTBEAT_DISABLED = False
    _PROJECT_CONTEXT = None
    _AFX_PID = None
    _POPUP_CACHE = None
    _AFX_WIN32_DIALOG_WINDOW_CLASS = "#32770" # the windows window class name used by After Effects for modal dialogs
    __WIN32_GW_CHILD = 5
    _CONTEXT_CACHE_KEY = "aftereffects_context_cache"

    _HAS_CHECKED_CONTEXT_POST_LAUNCH = False

    __CC_VERSION_MAPPING = {
        12: "2015",
        13: "2016",
        14: "2017",
        15: "2018",
        16: "2019"
    }

    __IS_SEQUENCE_REGEX = re.compile(u"[\[]?([#@]+|[%]0\dd)[\]]?")

    ############################################################################
    # context changing

    def post_context_change(self, old_context, new_context):
        """
        Runs after a context change has occurred. This will trigger the
        new state to be sent to the Adobe CC host application.

        :param old_context: The previous context.
        :param new_context: The current context.
        """

        # keep track of schema load for the current project to make sure we
        # aren't trying to use sg globals prior to load
        self.__schema_loaded = False

        # get the project id to supply to sg globals
        if new_context.project:
            project_id = new_context.project["id"]
        else:
            project_id = None

        # callback to set the schema loaded flag
        def _on_schema_loaded():
            self.__schema_loaded = True

        # tell sg globals to load the schema for the current project. sg globals
        # will run the callback immediately if already cached so this is likely
        # very quick.
        self.__shotgun_globals.run_on_schema_loaded(
            _on_schema_loaded,
            project_id=project_id
        )

        # go ahead and start the process of sending the current state back to js
        self.__send_state()

        # If the context is set in the environment, then we'll update it with
        # the new one. This will mean that a CEP extension restart will come
        # back up with the same context that it went down with. We have to set
        # this in ExtendScript, because it's the parent process of any CEP and
        # Python processes that get spawned. By setting it at the top, it'll be
        # propagated down to any of After Effects's subprocesses.
        if "TANK_CONTEXT" in os.environ:
            self.adobe.dollar.setenv("TANK_CONTEXT", new_context.serialize())

    ############################################################################
    # engine initialization

    def pre_app_init(self):
        """
        Sets up the engine into an operational state. This method called before
        any apps are loaded.
        """
        # import and keep a handle on the bundled python module
        self.__tk_aftereffects = self.import_module("tk_aftereffects")

        # constant command uid lookups for these special commands
        self.__jump_to_sg_command_id = self.__get_command_uid()
        self.__jump_to_fs_command_id = self.__get_command_uid()

        # get the adobe instance. it may have been initialized already by a
        # previous instance of the engine. if not, initialize a new one.
        self._adobe = self.__tk_aftereffects.AdobeBridge.get_or_create(
            identifier=self.instance_name,
            port=self._SHOTGUN_ADOBE_PORT,
            logger=self.logger,
            network_debug=self.SHOTGUN_ADOBE_NETWORK_DEBUG,
        )

        self.logger.debug(
            "Network debug logging is %s" % self._adobe.network_debug)

        self.logger.debug("%s: Initializing..." % (self,))

        # connect to all the adobe bridge signals
        self.adobe.logging_received.connect(self._handle_logging)
        self.adobe.command_received.connect(self._handle_command)
        self.adobe.active_document_changed.connect(self._handle_active_document_change)
        self.adobe.run_tests_request_received.connect(self._run_tests)
        self.adobe.state_requested.connect(self.__send_state)

        # in order to use frameworks, they have to be imported via
        # import_module. so they're exposed in the bundled python.
        shotgun_data = self.__tk_aftereffects.shotgun_data
        settings = self.__tk_aftereffects.shotgun_settings
        # keep a handle for shotgun globals as they are needed in other
        # functions as well
        self.__shotgun_globals = self.__tk_aftereffects.shotgun_globals

        # import here since the engine is responsible for defining Qt.
        from sgtk.platform.qt import QtCore

        # create a data retriever for async querying of sg data
        self.__sg_data = shotgun_data.ShotgunDataRetriever(
            QtCore.QCoreApplication.instance()
        )

        # get outselves a settings manager where we can store metadata.
        self.__settings_manager = settings.UserSettings(self)

        # connect the retriever signals
        self.__sg_data.work_completed.connect(self.__on_worker_signal)
        self.__sg_data.work_failure.connect(self.__on_worker_failure)

        # context request uids. we keep track of these to make sure we're only
        # processing the current requests.
        self.__context_find_uid = None
        self.__context_thumb_uid = None

        # keep track if sg global schema has been cached
        self.__schema_loaded = False

        # start the retriever thread
        self.__sg_data.start()

        # keep a list of handles on the launched dialogs
        self.__qt_dialogs = []

    def post_app_init(self):
        """
        Runs after all apps have been initialized.
        """
        if not self.adobe.event_processor:
            try:
                from sgtk.platform.qt import QtGui
                self.adobe.event_processor = QtGui.QApplication.processEvents
            except ImportError:
                pass

        self.__setup_connection_timer()
        self.__send_state()

        # forward the log file path back to the js side. this is used to direct
        # clients to the file in the event of an error
        log_file = sgtk.LogManager().base_file_handler.baseFilename
        self.adobe.send_log_file_path(log_file)

        # If there are fewer than 2 documents open, we don't need the stored
        # cache, regardless of whether this is a restart situation or a fresh
        # launch of AE. In that case, we take the opportunity to clear anything
        # that might exist in the stored cache, as it's data we don't need.
        self.logger.debug("Single document found, clearing stored context cache.")

        self.__settings_manager.store(
            self._CONTEXT_CACHE_KEY,
            dict(),
        )

    def destroy_engine(self):
        """
        Called when the engine should tear down itself and all its apps.
        """
        self.logger.debug("Destroying engine...")

        # Set our parent widget back to being owned by the window manager
        # instead of After Effects's application window.
        if self._PROXY_WIN_HWND and sys.platform == "win32":
            self.__tk_aftereffects.win_32_api.SetParent(self._PROXY_WIN_HWND, 0)

        # No longer poll for new messages from this engine.
        if self._CHECK_CONNECTION_TIMER:
            self._CHECK_CONNECTION_TIMER.stop()

        # We're going to hide and force the garbage collection of any dialogs
        # that we know about. This will stop memory leaks, and is also prudent
        # since we're severing the socket.io connection that will allow them
        # to function properly.
        for dialog in self.__qt_dialogs:
            dialog.hide()
            dialog.setParent(None)
            dialog.deleteLater()

        # Gracefully stop our data retriever. This call will block until the
        # currently-processing request has completed.
        self.__sg_data.stop()

        # Disconnect from the server.
        self.adobe.disconnect()

        # Disconnect the signals in case there are references to this engine
        # out there. without disconnecting, it will still respond to signals
        # from the adobe bridge.
        self.adobe.logging_received.disconnect(self._handle_logging)
        self.adobe.command_received.disconnect(self._handle_command)
        self.adobe.active_document_changed.disconnect(self._handle_active_document_change)
        self.adobe.run_tests_request_received.disconnect(self._run_tests)
        self.adobe.state_requested.disconnect(self.__send_state)

    def post_qt_init(self):
        """
        Called externally once a ``QApplication`` has been created and completes
        the engine setup process.
        """
        # We need to have the RPC API call processEvents during its response
        # wait loop. This will keep that loop from blocking the UI thread.
        from sgtk.platform.qt import QtGui
        self.adobe.event_processor = QtGui.QApplication.processEvents

        # Since this is running in our own Qt event loop, we'll use the bundled
        # dark look and feel. breaking encapsulation to do so.
        self.logger.debug("Initializing default styling...")
        self._initialize_dark_look_and_feel()

        # Sets up the heartbeat timer to run asynchronously.
        self.__setup_connection_timer(force=True)

    def register_command(self, name, callback, properties=None):
        """
        Registers a new command with the engine. For Adobe RPC purposes,
        a "uid" property is added to the command's properties.
        """
        properties = properties or dict()
        properties["uid"] = self.__get_command_uid()
        return super(AfterEffectsEngine, self).register_command(
            name,
            callback,
            properties,
        )

    @property
    def host_info(self):
        """
        Returns information about the application hosting this engine.

        :returns: A {"name": application name, "version": application version}
                  dictionary. eg: {"name": "AfterFX", "version": "2017.1.1"}
        """
        if not self.adobe:
            # Don't error out if the bridge was not yet started
            return {"name": "AfterFX", "version": "unknown"}

        version = self.adobe.app.version
        # app.aftereffects.AfterEffectsVersion just returns 18.1.1 which is not what users see in the UI
        # extract a more meaningful version from the systemInformation property
        # which gives something like:
        # Adobe After Effects Version: 2017.1.1 20170425.r.252 2017/04/25:23:00:00 CL 1113967  x64\rNumber of .....
        # and use it instead if available.
        m = re.search("([0-9]+[\.]?[0-9]*)", unicode(version))
        if m:
            cc_version = self.__CC_VERSION_MAPPING.get(math.floor(float(m.group(1))), version)
        return {
            "name": "AfterFX",
            "version": cc_version,
        }

    ############################################################################
    # engine host interaction methods

    @property
    def project_path(self):
        """
        Get the active project filepath.

        :returns: The current project path or an empty string
        :rtype: str
        """
        doc_obj = self.adobe.app.project.file
        doc_path = ""
        # doc_obj will always be a ProxyWrapper instance so we cannot
        # use the `doc_obj is not None` comparison
        if doc_obj != None:
            doc_path = doc_obj.fsName
        return doc_path

    def save(self, path=None):
        """
        Save the project in place or to the given file-path

        :param str path: The target file path. optional.
        """

        with self.context_changes_disabled():

            if path is None:
                self.adobe.app.project.save()
            else:
                # After Effects won't ensure that the folder is created when saving, so we must make sure it exists
                ensure_folder_exists(os.path.dirname(path))
                self.adobe.app.project.save(self.adobe.File(path))
            new_path = self.project_path
            self.logger.info("Saved file to to {!r}".format(new_path))

    def save_as(self):
        """
        Launch a Qt file browser to select a file, then save the supplied
        project to that path.
        """

        from sgtk.platform.qt import QtGui

        doc_path = self.project_path

        # After Effects doesn't appear to have a "save as" dialog accessible via
        # python. so open our own Qt file dialog.
        file_dialog = QtGui.QFileDialog(
            parent=self._get_dialog_parent(),
            caption="Save As",
            directory=doc_path,
            filter="After Effects Documents (*.aep, *.aepx)"
        )
        file_dialog.setLabelText(QtGui.QFileDialog.Accept, "Save")
        file_dialog.setLabelText(QtGui.QFileDialog.Reject, "Cancel")
        file_dialog.setOption(QtGui.QFileDialog.DontResolveSymlinks)
        file_dialog.setOption(QtGui.QFileDialog.DontUseNativeDialog)
        if not file_dialog.exec_():
            return
        path = file_dialog.selectedFiles()[0]

        if path:
            self.save(path)

    @property
    def AdobeItemTypes(self):
        """
        This returns a constant class that maps constants against
        adobe aftereffects internal class names.

        :returns: AdobeItemTypes constants object. see :class:`~python.tk_aftereffects.AdobeItemTypes`
        :rtype: AdobeItemTypes
        """
        afx_module = self.import_module("tk_aftereffects")
        return afx_module.AdobeItemTypes

    def is_item_of_type(self, item, adobe_type):
        """
        Indicates whether the given item is of the given AdobeItemType.

        :param item: item to be checked.
        :type item: `adobe.ItemObject`_
        :param str adobe_type: AdobeItemType-constant. One of the constants held by :class:`~python.tk_aftereffects.AdobeItemTypes`
        :returns: Indicating if the given item is of the given type
        :rtype: bool
        """
        return item.data.get("instanceof", "") == adobe_type

    @property
    def selected_item(self):
        """
        Helper getting the currently selected item in the after effects project

        :returns: The active item
        :rtype: `adobe.ItemObject`_
        """
        return self.adobe.app.project.activeItem

    def is_adobe_sequence(self, path):
        """
        Helper to query if an adobe-style render path is
        describing a sequence.

        The sequencepath must contain one of the following patterns in
        order to be recognized::

            ###
            @@@
            [###]
            %0xd

        :param str path: filepath to check
        :returns: True if the path describes a sequence
        :rtype: bool
        """
        if re.search(self.__IS_SEQUENCE_REGEX, path):
            return True
        return False

    def check_sequence(self, path, queue_item):
        """
        Helper to query if all render files of a given queue item
        are actually existing.

        :param str path: filepath to check
        :param queue_item: an after effects render queue item
        :type queue_item: `adobe.RenderQueueItemObject`_
        :returns: True if the path describes a sequence
        :rtype: bool
        """
        for file_path, _ in self.get_render_files(path, queue_item):
            if not os.path.exists(file_path):
                return False
        return True

    def iter_collection(self, collection_item):
        """
        Helper to iter safely through an adobe-collection item
        as its index starts at 1, not 0.

        One may use this method like this::

            item_collection = engine.adobe.project.items
            for item in engine.iter_collection(item_collection):
                print item.name

        :param collection_item: the after-effects collection item to iter
        :type collection_item: `adobe.ItemCollectionObject`_
        :yields: the next child item of the collection
        :rtype: `adobe.ItemObject`_
        """
        for i in xrange(1, collection_item.length + 1):
            yield collection_item[i]

    def get_render_files(self, path, queue_item):
        """
        Yields all render-files and its frame number of a given
        after effects render queue item.

        The path is needed to uniquely identify the correct output_module

        :param str path: filepath to iter
        :param queue_item: an after effects render queue item
        :type queue_item: `adobe.RenderQueueItemObject`_
        :yields: 2-item-tuple where the firstitem is the resolved path (str)
                of the render file and the second item the frame-number or
                None if the path is not an image-sequence.
        :rtype: tuple
        """
        # is the given render-path a sequence?
        match = re.search(self.__IS_SEQUENCE_REGEX, path)
        if not match:
            # if not, we just check if the file exists
            yield path, None
            raise StopIteration()

        # if yes, we check the existence of each frame
        frame_time = queue_item.comp.frameDuration
        start_time = int(round(queue_item.timeSpanStart / frame_time, 3))
        frame_numbers = int(round(queue_item.timeSpanDuration / frame_time, 3))
        skip_frames = queue_item.skipFrames + 1
        padding = len(match.group(1))

        test_path = path.replace(match.group(0), "%%0%dd" % padding)
        for frame_no in xrange(start_time, start_time + frame_numbers, skip_frames):
            yield test_path % frame_no, frame_no

    def import_filepath(self, path):
        """
        Helper method to import footage into the current comp.
        This method contains logic to determine as what the given file-path
        may be imported (PROJECT, FOOTAGE, COMP etc.)
        To influence this logic you can implement the "import_footage_hook"
        for this engine.
        It will also collect all new items and return those in case
        the import leads to more than one (1) Item-objects to be imported.

        :param str path: filepath to be imported. Sequences should give either the
                first frame or the frames replaced by hashes (#), at (@) or the
                percent formatting (%04d) syntax.
        :returns: All new items that were imported
        :rtype: list-of-`adobe.CompItemObject`_
        """
        file_obj = self.adobe.File(path)
        import_options = self.adobe.ImportOptions()
        import_options.file = file_obj
        import_options.sequence = False

        self.execute_hook_method(
            "import_footage_hook",
            "set_import_options",
            import_options=import_options
        )

        return self.__import_file(import_options)

    def add_items_to_comp(self, item_collection, comp_item):
        """
        Helper method that adds the "best-matching" items from a given
        adobe.CollectionItem into a given CompItem. Where "best-matching"
        means, that the first CompItem found is added alternatively the first
        Footage item is added.
        In case the collection contains only FolderItems, this method will
        recurse into the folders until one ItemObject was found.

        :param item_collection: object to be analyzed
        :type item_collection: `adobe.ItemCollectionObject`_
        :param comp_item: comp that should receive the items from item_collection
        :type comp_item: `adobe.CompItemObject`_
        :returns: Indicating if an item was added or not.
        :rtype: bool
        """

        # first we generate a list of items within
        # the given collection (FolderItem), because
        # we have different include-priorities per
        # type
        folders = []
        comps = []
        footage = []
        for item in self.iter_collection(item_collection):
            if self.is_item_of_type(item, self.AdobeItemTypes.FOLDER_ITEM):
                folders.append(item)
            elif self.is_item_of_type(item, self.AdobeItemTypes.COMP_ITEM):
                comps.append(item)
            elif self.is_item_of_type(item, self.AdobeItemTypes.FOOTAGE_ITEM):
                footage.append(item)

        # if there is a comp in the given folder,
        # then we prefer to add this to the comp,
        # as it is likely to include the other layers
        added = False
        comp_and_footage = comps + footage
        while not added and comp_and_footage:
            comp_item = comp_item.layers.add(comp_and_footage.pop(0))
            added = True

        # in case no footage was added, we recurse into
        # the folders until we find something or return None
        while not added and folders:
            folder = folders.pop(0)
            added = self.add_items_to_comp(folder.items, comp_item)
        return added

    def render_queue_item(self, queue_item):
        """
        renders a given queue_item, by disabling all other queued items
        and only enabling the given item. After rendering the state of the
        render-queue is reverted.

        :param queue_item: The render queue item to be rendered
        :type queue_item: `adobe.RenderQueueItemObject`_
        :returns: Indicating successful rendering or not
        :rtype: bool
        """

        # save the queue state for all unrendered items
        queue_item_state_cache = [(queue_item, queue_item.render)]
        for item in self.iter_collection(self.adobe.app.project.renderQueue.items):
            # one cannot change the status on
            if item.status != self.adobe.RQItemStatus.QUEUED:
                continue
            queue_item_state_cache.append((item, item.render))
            item.render = False

        success = False
        queue_item.render = True
        self.logger.debug("Start rendering..")
        try:
            self.adobe.app.project.renderQueue.render()
        except Exception as e:
            # This catches errors thrown during the AfterEffects Render process.
            # Which may return various different errors. This situation never
            # occured during development.
            self.logger.error(
                ("Skipping item due to an error "
                    "while rendering: {}").format(e)
            )
        finally:
            acceptable_states = [
                self.adobe.RQItemStatus.DONE,
                self.adobe.RQItemStatus.ERR_STOPPED,
                self.adobe.RQItemStatus.RENDERING
            ]
            # reverting the original queued state for all
            # unprocessed items
            while queue_item_state_cache:
                item, status = queue_item_state_cache.pop(0)
                if item.status not in acceptable_states:
                    item.render = status

        # we check for success if the render queue item status
        # has changed to DONE
        success = (queue_item.status == self.adobe.RQItemStatus.DONE)
        return success

    def find_sequence_range(self, path):
        """
        Parses the file name in an attempt to determine the first and last
        frame number of a sequence. This assumes some sort of common convention
        for the file names, where the frame number is an integer at the end of
        the basename, just ahead of the file extension, such as
        ``file.0001.jpg``, or ``file_001.jpg``. We also check for input file names with
        abstracted frame number tokens, such as ``file.####.jpg`` or ``file.%04d.jpg``.

        :param str path: The file path to parse.

        :returns: None if no range could be determined, otherwise (min, max)
        :rtype: tuple or None
        """
        # This pattern will match the following at the end of a string and
        # retain the frame number or frame token as group(1) in the resulting
        # match object:
        #
        # 0001
        # ####
        # @@@@
        # [####]
        # %04d
        #
        # The number of digits or hashes does not matter; we match as many as
        # exist.
        frame_pattern = re.compile(r"(^|[\.\_\- ])[\[]?([0-9#@]+|[%]0\d+d)[\]]?$")
        root, ext = os.path.splitext(path)
        match = re.search(frame_pattern, root)

        # If we did not match, we don't know how to parse the file name, or there
        # is no frame number to extract.
        if not match:
            return None

        # We need to get all files that match the pattern from disk so that we
        # can determine what the min and max frame number is.
        glob_path = "%s%s" % (
            re.sub(frame_pattern, r"\1*", root),
            ext,
        )
        files = glob.glob(glob_path)

        # Our pattern from above matches against the file root, so we need
        # to chop off the extension at the end.
        file_roots = [os.path.splitext(f)[0] for f in files]

        # We know that the search will result in a match at this point, otherwise
        # the glob wouldn't have found the file. We can search and pull group 1
        # to get the integer frame number from the file root name.
        frames = [int(re.search(frame_pattern, f).group(2)) for f in file_roots]
        return (min(frames), max(frames))

    ############################################################################
    # RPC

    def _check_connection(self):
        """Make sure we are still connected to the adobe cc product."""
        # If we're in a disabled state, then we don't do anything here. This
        # is controlled by the heartbeat_disabled context manager provided
        # by this engine.
        if self._HEARTBEAT_DISABLED:
            return

        try:
            self.adobe.ping()
        except Exception:
            if self._FAILED_PINGS >= self.SHOTGUN_ADOBE_HEARTBEAT_TOLERANCE:
                from sgtk.platform.qt import QtCore
                QtCore.QCoreApplication.instance().quit()
            else:
                self._FAILED_PINGS += 1
        else:
            self._FAILED_PINGS = 0

            # Will allow queued up messages (like logging calls)
            # to be handled on the Python end.
            self.adobe.process_new_messages()

        # We also have a one-time check we need to make after the timer is
        # started. In the event that the user opened a document before the
        # integration completed its initialization, we need to make sure
        # that the context is correct. Since we rely of After Effects sending
        # an event on active document change to control our context, if that
        # occurred before we were listening then we likely missed it and
        # need to make sure we're not in a stale state.
        #
        # Note: This is occurring in the timer here because the context
        # change can only occur once the engine initialization process has
        # completed. As such, we can rely on the timer to delay its execution
        # until we're in a state where the context change will succeed.
        if not self._HAS_CHECKED_CONTEXT_POST_LAUNCH:
            if sgtk.platform.current_engine():
                self.logger.debug(
                    "Engine initialization complete -- checking active "
                    "document context..."
                )

                active_document_path = self.project_path

                if active_document_path:
                    self.logger.debug(
                        "There is an active document. Checking to see if a "
                        "context change is required."
                    )
                    self._handle_active_document_change(active_document_path)
                else:
                    self.logger.debug(
                        "There is no active document, so there is no need to change context."
                    )

                self._HAS_CHECKED_CONTEXT_POST_LAUNCH = True
            else:
                self.logger.debug(
                    "Engine initialization has not completed -- waiting to "
                    "check the active document context..."
                )

    def _emit_log_message(self, handler, record):
        """
        Called by the engine whenever a new log message is available.

        All log messages from the toolkit logging namespace will be passed to
        this method.

        :param handler: Log handler that this message was dispatched from
        :type handler: :class:`~python.logging.LogHandler`
        :param record: Std python logging record
        :type record: :class:`~python.logging.LogRecord`
        """

        # If the _adobe attribute is set, then we can forward logging calls
        # back to the js process via rpc.
        if hasattr(self, "_adobe"):

            level = self.PY_TO_JS_LOG_LEVEL_MAPPING[record.levelname]

            # log the message back to js via rpc
            self.adobe.log_message(level, record.message)

        # prior to the _adobe attribute being set, we rely on the js process
        # handling stdout and logging it.
        else:

            # we don't use the handler's format method here because the adobe
            # side expects a certain format.
            msg_str = "[%s]: %s" % (
                record.levelname,
                record.message
            )

            sys.stdout.write(msg_str)
            sys.stdout.flush()

    def _handle_active_document_change(self, active_document_path):
        """
        Gets the active document from the host application, determines which
        context it belongs to, and changes to that context.

        :param str active_document_path: The path to the new active document.

        :returns: True if the context changed, False if it did not.
        """
        # If the config says to not change context on active document change, then
        # we don't do anything here.
        if not self.get_setting("automatic_context_switch"):
            self.logger.debug(
                "Engine setting automatic_context_switch is false. Not changing context."
            )
            return

        # Make sure we have a properly-encoded string for the path. We can
        # possibly get a file path/name that contains unicode, and we don't
        # want to deal with that later on.
        if isinstance(active_document_path, unicode):
            active_document_path = active_document_path.encode("utf-8")

        # This will be True if the context_changes_disabled context manager is
        # used. We're just in a temporary state of not allowing context changes,
        # which is useful when an app is doing a lot of After Effects work that
        # might be triggering active document changes that we don't want to
        # result in SGTK context changes.
        with self.heartbeat_disabled():
            if self._CONTEXT_CHANGES_DISABLED:
                self.logger.debug(
                    "Engine is in 'no context changes' mode. Not changing context."
                )
                return False

            if active_document_path:
                self.logger.debug("New active document is %s" % active_document_path)

            cached_context = self.__get_from_context_cache(active_document_path)

            if cached_context:
                context = cached_context
                self.logger.debug("Document found in context cache: %r" % context)
            else:
                try:
                    context = self.tank.context_from_path(
                        active_document_path,
                        previous_context=self.context,
                    )
                    self.__add_to_context_cache(active_document_path, context)
                except Exception:
                    self.logger.debug(
                        "Unable to determine context from path. Setting the Project context."
                    )
                    # clear the context finding task ids so that any tasks that
                    # finish won't send data to js.
                    self.__context_find_uid = None
                    self.__context_thumb_uid = None

                    # We go to the project context if this is a file outside of
                    # SGTK control.
                    if self._PROJECT_CONTEXT is None:
                        self._PROJECT_CONTEXT = sgtk.Context(
                            tk=self.context.sgtk,
                            project=self.context.project,
                        )

                    context = self._PROJECT_CONTEXT

            if not context.project and not self.context.project:
                self.logger.debug(
                    "New context doesn't have a Project entity. Not changing "
                    "context."
                )
                return False
            elif not context.project:
                context = self.tank.context_from_entity(
                    self.context.project["type"],
                    self.context.project["id"]
                )

            if context and context != self.context:
                self.adobe.context_about_to_change()
                sgtk.platform.change_context(context)
                return True

            return False

    def _handle_command(self, uid):
        """
        Handles an RPC engine command execution request.

        :param int uid: The unique id of the engine command to run.
        """

        self.logger.debug("Handling command request for uid: %s" % (uid,))

        with self.heartbeat_disabled():
            from sgtk.platform.qt import QtGui

            if uid == self.__jump_to_fs_command_id:
                # jump to fs special command triggered
                self._jump_to_fs()
            elif uid == self.__jump_to_sg_command_id:
                # jump to sg special command triggered
                self._jump_to_sg()
            else:
                # a registered command was triggered
                for command in self.commands.values():
                    if command.get("properties", dict()).get("uid") == uid:
                        self.logger.debug(
                            "Executing callback for command: %s" % (command,))
                        result = command["callback"]()
                        if isinstance(result, QtGui.QWidget):
                            # if the callback returns a widget, keep a handle on it
                            self.__qt_dialogs.append(result)

    def _handle_logging(self, level, message):
        """
        Handles an RPC logging request.

        :param str level: One of "debug", "info", "warning", or "error".
        :param str message: The log message.
        """

        # manually create a record to log to the standard file handler.
        # we format it to match the regular logs, but tack on the '.js' to
        # indicate that it came from javascript.
        record = logging.makeLogRecord({
            "levelname": level.upper(),
            "name": "%s.js" % (self.logger.name,),
            "msg": message,
        })

        # forward this message to the base file handler so that it is logged
        # appropriately.
        if sgtk.LogManager().base_file_handler:
            sgtk.LogManager().base_file_handler.handle(record)

    def _run_tests(self):
        """
        Runs the test suite for the tk-aftereffects bundle.
        """
        # If we don't know what the tests root directory path is
        # via the environment, then we shouldn't be here.
        try:
            tests_root = os.environ["SHOTGUN_ADOBE_TESTS_ROOT"]
        except KeyError:
            self.logger.error(
                "The SHOTGUN_ADOBE_TESTS_ROOT environment variable "
                "must be set to the root directory of the tests to be "
                "run. Not running tests!"
            )
            return
        else:
            # Make sure we can find the run_tests.py file within the root
            # that was specified in the environment.
            self.logger.debug("Test root path found. Looking for run_tests.py.")
            test_module = os.path.join(tests_root, self.TEST_SCRIPT_BASENAME)

            if not os.path.exists(test_module):
                self.logger.error(
                    "Unable to find run_tests.py in the directory "
                    "specified by the SHOTGUN_ADOBE_TESTS_ROOT "
                    "environment variable. Not running tests!"
                )
                return

        self.logger.debug("Found run_tests.py. Importing to run tests.")

        try:
            # We need to prepend to sys.path. We'll set it back to
            # what it was before once we're done running the tests.
            original_sys_path = sys.path
            python_root = os.path.abspath(
                os.path.join(os.path.dirname(__file__), "python")
            )

            sys.path = [tests_root, python_root] + sys.path
            import run_tests

            # The run_tests.py module should make available a run_tests
            # function. We need to run that, giving it the engine pointer
            # so that it can use that for logging purposes.
            run_tests.run_tests(self)
        except Exception as exc:
            # If we got an unhandled exception, then something went very
            # wrong in the test suite. We'll just trap that and print it
            # as an error without letting it bubble up any farther.
            import traceback
            self.logger.error(
                "Tests raised the following:\n%s" % traceback.format_exc(exc)
            )
        finally:
            # Reset sys.path back to what it was before we started.
            sys.path = original_sys_path

    ############################################################################
    # properties

    @property
    def adobe(self):
        """
        The handle to the Adobe RPC API.
        """
        return self._adobe

    @property
    def app_id(self):
        """
        The runtime app id. This will be a string -- something like
        PHSP for After Effects, or AEFT for After Effect.
        """
        return self._SHOTGUN_ADOBE_APPID

    @property
    def context_change_allowed(self):
        """
        Specifies that context changes are allowed by the engine.
        """
        return True

    ############################################################################
    # context manager

    @contextmanager
    def context_changes_disabled(self):
        """
        A context manager that disables context changes on enter, and enables
        them on exit. This is useful in apps that might be performing operations
        that require changes in the active document that don't want to trigger
        a context change.
        """
        self._CONTEXT_CHANGES_DISABLED = True
        yield
        self._CONTEXT_CHANGES_DISABLED = False

    @contextmanager
    def heartbeat_disabled(self):
        """
        A context manager that disables the heartbeat and message processing
        timer on enter, and restarts it on exit.
        """
        try:
            self.logger.debug("Pausing heartbeat...")
            self._HEARTBEAT_DISABLED = True
        except Exception, e:
            self.logger.debug("Unable to pause heartbeat as requested.")
            self.logger.error(str(e))
        else:
            self.logger.debug("Heartbeat paused.")

        yield

        self._HEARTBEAT_DISABLED = False
        self.logger.debug("Heartbeat restarted.")

    ############################################################################
    # UI

    def _define_qt_base(self):
        """
        This will be called at initialisation time and will allow
        a user to control various aspects of how QT is being used
        by Toolkit. The method should return a dictionary with a number
        of specific keys, outlined below.

        * qt_core - the QtCore module to use
        * qt_gui - the QtGui module to use
        * dialog_base - base class for to use for Toolkit's dialog factory

        :returns: dict
        """
        # Just call the base implementation and monkey patch QMessageBox.
        base = super(AfterEffectsEngine, self)._define_qt_base()
        if not base:
            raise ImportError("Unable to find a QT Python module")

        QtCore = base["qt_core"]
        QtGui = base["qt_gui"]

        # tell QT4 to interpret C strings as utf-8
        # note: this will be ignored on QT5 via our shim
        utf8 = QtCore.QTextCodec.codecForName("utf-8")
        QtCore.QTextCodec.setCodecForCStrings(utf8)

        # override message boxes
        self._override_qmessagebox(QtGui.QMessageBox)
        return base

    def _override_qmessagebox(self, q_message_box):
        """
        Redefine the method calls for QMessageBox static methods.

        These are often called from within apps and because QT is running in a
        separate process, they will pop up behind the After Effects window. Wrap
        each of these calls in a raise method to activate the QT process.

        :param q_message_box: The QMessageBox class to patch.
        """

        info_fn = q_message_box.information
        critical_fn = q_message_box.critical
        question_fn = q_message_box.question
        warning_fn = q_message_box.warning

        @staticmethod
        def _info_wrapper(*args, **kwargs):
            self.__activate_python()
            return info_fn(*args, **kwargs)

        @staticmethod
        def _critical_wrapper(*args, **kwargs):
            self.__activate_python()
            return critical_fn(*args, **kwargs)

        @staticmethod
        def _question_wrapper(*args, **kwargs):
            self.__activate_python()
            return question_fn(*args, **kwargs)

        @staticmethod
        def _warning_wrapper(*args, **kwargs):
            self.__activate_python()
            return warning_fn(*args, **kwargs)

        q_message_box.information = _info_wrapper
        q_message_box.critical = _critical_wrapper
        q_message_box.question = _question_wrapper
        q_message_box.warning = _warning_wrapper

    def __check_for_popups(self):
        """
        Method will check if a popup dialog from aftereffects was openend
        and if so, it will raise the window to the front.

        NOTE:
            This is only done in windows.
            TODO: Implement an equivalent method on mac
        """
        if sys.platform == "win32":

            # to get all dialogs from After Effects, we have to query the
            # After Effects process id first. As this code runs inside a
            # separate python process, we use a subprocess for this.
            if self._AFX_PID == -1:
                return
            elif self._AFX_PID is None:
                # the windows tasklist command will provide the process id
                # of the After Effects executable. As there is always only one
                # instance of After Effects, this should be safe to determine the
                # process id.
                pid_query_process = subprocess.Popen(
                    ['tasklist', '/FI', 'ImageName eq AfterFX.exe', '/FO', 'CSV', '/NH'],
                    stdout=subprocess.PIPE
                )
                out_string, _ = pid_query_process.communicate()

                # The out_string will look like:
                # "AfterFX.ext","1234","SessionName","SessionNum","MemoryUsage"
                # where 1234 describes the pid of After Effects
                match = re.match("[^0-9]+([0-9]+).*", out_string, re.DOTALL)
                if not match:
                    self._AFX_PID = -1
                self._AFX_PID = int(match.group(1))

            # with the process id of After Effects, we can get all HWNDS that point to
            # dialog classes.
            hwnds = self.__tk_aftereffects.win_32_api.find_windows(
                                process_id=self._AFX_PID,
                                class_name=self._AFX_WIN32_DIALOG_WINDOW_CLASS,
                                stop_if_found=True,
                            )

            # To avoid raising a dialog, that we raised before,
            # we will compare the list of visible dialog-hwnds with the list
            # that we already raised.
            # As we cannot compare the hwnd-pointers directly, we need to compare
            # the integer value (hwnd long) of the hwnd pointers.

            # we build a dict that maps the hwnd longs to the current hwnd pointer
            all_hwnds = {}
            GetWindow = self.__tk_aftereffects.win_32_api.ctypes.windll.user32.GetWindow
            for hwnd in hwnds:
                # GetWindow with GW_CHILD is used to get the hwnd long that identifies the child window
                # at the top of the Z order, of the specified parent window pointer.
                all_hwnds[GetWindow(hwnd, self.__WIN32_GW_CHILD)] = hwnd

            # by comparing the cached hwnd longs with the current list of hwnd longs,
            # we find out which hwnds are actually pointing to new (unraised) dialogs.
            new_hwnds = set(all_hwnds.keys()) - (self._POPUP_CACHE or set([]))

            # in case there is a new dialog, we bring it to front and update the cache
            for hwnd_long in new_hwnds:
                self.__tk_aftereffects.win_32_api.bring_to_front(all_hwnds[hwnd_long])
                self._POPUP_CACHE = set(all_hwnds.keys())
                return

            # in case there are open dialogs, but we already raised them,
            # we do nothing.
            if all_hwnds:

                return
            # In case there is no open dialog, we will reset the cache to None
            self._POPUP_CACHE = None

    def _win32_get_aftereffects_main_hwnd(self):
        """
        Windows specific method to find the main After Effects window
        handle (HWND)
        """
        if not self._WIN32_AFTEREFFECTS_MAIN_HWND:
            for major in sorted(self.__CC_VERSION_MAPPING.keys()):
                for minor in xrange(10):
                    found_hwnds = self.__tk_aftereffects.win_32_api.find_windows(
                        class_name="AE_CApplication_{}.{}".format(major, minor),
                        stop_if_found=True,
                    )

                    if found_hwnds:
                        self._WIN32_AFTEREFFECTS_MAIN_HWND = found_hwnds[0]
                        return self._WIN32_AFTEREFFECTS_MAIN_HWND

        return self._WIN32_AFTEREFFECTS_MAIN_HWND

    def _win32_get_proxy_window(self):
        """
        Windows-specific method to get the proxy window that will 'own' all
        Toolkit dialogs.  This will be parented to the main After Effects
        application.

        :returns: A QWidget that has been parented to After Effects's window.
        """
        # Get the main After Effects window:
        ps_hwnd = self._win32_get_aftereffects_main_hwnd()
        win32_proxy_win = None
        proxy_win_hwnd = None

        if ps_hwnd:
            from tank.platform.qt import QtGui, QtCore

            # Create the proxy QWidget.
            win32_proxy_win = QtGui.QWidget()
            window_title = "Shotgun Toolkit Parent Widget"
            win32_proxy_win.setWindowTitle(window_title)

            # We have to take different approaches depending on whether
            # we're using Qt4 (PySide) or Qt5 (PySide2). The functionality
            # needed to turn a Qt5 WId into an HWND is not exposed in PySide2,
            # so we can't do what we did below for Qt4.
            if QtCore.__version__.startswith("4."):
                proxy_win_hwnd = self.__tk_aftereffects.win_32_api.qwidget_winid_to_hwnd(
                    win32_proxy_win.winId(),
                )
            else:
                # With PySide2, we're required to look up our proxy parent
                # widget's HWND the hard way, following the same logic used
                # to find After Effects's main window. To do that, we actually have
                # to show our widget so that Windows knows about it. We can make
                # it effectively invisible if we zero out its size, so we do that,
                # show the widget, and then look up its HWND by window title before
                # hiding it.
                win32_proxy_win.setGeometry(0, 0, 0, 0)
                win32_proxy_win.show()

                try:
                    proxy_win_hwnd_found = self.__tk_aftereffects.win_32_api.find_windows(
                        stop_if_found=True,
                        class_name="Qt5QWindowIcon",
                        process_id=os.getpid(),
                    )
                finally:
                    win32_proxy_win.hide()

                if proxy_win_hwnd_found:
                    proxy_win_hwnd = proxy_win_hwnd_found[0]
        else:
            self.logger.debug(
                "Unable to determine the HWND of After Effects itself. This means "
                "that we can't properly setup window parenting for Toolkit apps."
            )

        # Parent to the After Effects application window if we found everything
        # we needed. If we didn't find our proxy window for some reason, we
        # will return None below. In that case, we'll just end up with no
        # window parenting, but apps will still launch.
        if proxy_win_hwnd is None:
            self.logger.warning(
                "Unable setup window parenting properly. Dialogs shown will "
                "not be parented to After Effects, but they will still function "
                "properly otherwise."
            )
        else:
            # Set the window style/flags. We don't need or want our Python
            # dialogs to notify the After Effects application window when they're
            # opened or closed, so we'll disable that behavior.
            win_ex_style = self.__tk_aftereffects.win_32_api.GetWindowLong(
                proxy_win_hwnd,
                self.__tk_aftereffects.win_32_api.GWL_EXSTYLE,
            )

            self.__tk_aftereffects.win_32_api.SetWindowLong(
                proxy_win_hwnd,
                self.__tk_aftereffects.win_32_api.GWL_EXSTYLE,
                win_ex_style | self.__tk_aftereffects.win_32_api.WS_EX_NOPARENTNOTIFY,
            )
            self.__tk_aftereffects.win_32_api.SetParent(proxy_win_hwnd, ps_hwnd)
            self._PROXY_WIN_HWND = proxy_win_hwnd

        return win32_proxy_win

    def _get_dialog_parent(self):
        """
        Get the QWidget parent for all dialogs created through
        show_dialog & show_modal.
        """

        """
        Get the QWidget parent for all dialogs created through
        show_dialog & show_modal.
        """
        # determine the parent widget to use:
        from tank.platform.qt import QtGui

        if not self._DIALOG_PARENT:
            if sys.platform == "win32":
                # for windows, we create a proxy window parented to the
                # main application window that we can then set as the owner
                # for all Toolkit dialogs
                self._DIALOG_PARENT = self._win32_get_proxy_window()
            else:
                self._DIALOG_PARENT = QtGui.QApplication.activeWindow()

        return self._DIALOG_PARENT

    def show_dialog(self, title, bundle, widget_class, *args, **kwargs):
        """
        Shows a non-modal dialog window in a way suitable for this engine.
        The engine will attempt to parent the dialog nicely to the host
        application.

        :param title: The title of the window
        :param bundle: The app, engine or framework object that is associated
            with this window
        :param widget_class: The class of the UI to be constructed. This must
            derive from QWidget.

        Additional parameters specified will be passed through to the
        widget_class constructor.

        :returns: the created widget_class instance
        """
        if not self.has_ui:
            self.logger.error(
                "Sorry, this environment does not support UI display! Cannot "
                "show the requested window '%s'." % title
            )
            return None

        # create the dialog:
        dialog, widget = self._create_dialog_with_widget(
            title,
            bundle,
            widget_class,
            *args, **kwargs
        )

        # Note - the base engine implementation will try to clean up
        # dialogs and widgets after they've been closed.  However this
        # can cause a crash in After Effects as the system may try to send
        # an event after the dialog has been deleted.
        # Keeping track of all dialogs will ensure this doesn't happen
        self.__qt_dialogs.append(dialog)

        # make python active if possible
        self.__activate_python()

        # make sure the window raised so it doesn't
        # appear behind the main After Effects window
        self.logger.debug("Showing dialog: %s" % (title,))
        dialog.show()
        dialog.raise_()
        dialog.activateWindow()

        return widget

    def show_modal(self, title, bundle, widget_class, *args, **kwargs):
        """
        Shows a modal dialog window in a way suitable for this engine. The
        engine will attempt to integrate it as seamlessly as possible into the
        host application. This call is blocking
        until the user closes the dialog.

        :param title: The title of the window
        :param bundle: The app, engine or framework object that is associated
            with this window
        :param widget_class: The class of the UI to be constructed. This must
            derive from QWidget. Additional parameters specified will be passed
            through to the widget_class constructor.
        :returns: (a standard QT dialog status return code, the created
            widget_class instance)
        """
        if not self.has_ui:
            self.logger.error(
                "Sorry, this environment does not support UI display! Cannot "
                "show the requested window '%s'." % title)
            return

        # create the dialog:
        dialog, widget = self._create_dialog_with_widget(
            title,
            bundle,
            widget_class,
            *args,
            **kwargs
        )

        # Note - the base engine implementation will try to clean up
        # dialogs and widgets after they've been closed.  However this
        # can cause a crash in After Effects as the system may try to send
        # an event after the dialog has been deleted.
        # Keeping track of all dialogs will ensure this doesn't happen
        self.__qt_dialogs.append(dialog)

        # make python active if possible
        self.__activate_python()

        self.logger.debug("Showing modal: %s" % (title,))
        dialog.raise_()
        dialog.activateWindow()
        status = dialog.exec_()

        return status, widget

    ############################################################################
    # internal methods

    def __get_command_uid(self):
        """
        Returns a guaranteed unique command id.
        """
        with self._LOCK:
            self._COMMAND_UID_COUNTER += 1
            return self._COMMAND_UID_COUNTER

    def __get_icon_path(self, properties):
        """
        Processes the command properties dictionary to find the most appropriate
        icon path.

        This code looks for an `icons` dictionary of the following form::

            {
                "dark": {
                    "png": "/path/to/dark_icon.png",
                    "svg": "/path/to/dark_icon.svg"
                },
                "light": {
                    "png": "/path/to/light_icon.png",
                    "svg": "/path/to/light_icon.svg"
                }
            }

        For Adobe, the preference is dark png then light png

        If neither of these is found, fall back to the standard
        `properties["icon"]`.
        """

        icon_path = None
        icons = properties.get("icons")

        if icons:
            dark = icons.get("dark")
            light = icons.get("light")

            # check for dark icon
            if dark:
                icon_path = dark.get("png", icon_path)

            # if no dark icon, check the light:
            if not icon_path and light:
                icon_path = dark.get("png", icon_path)

        # still no icon path, fall back to regular icon
        if not icon_path:
            icon_path = properties.get("icon")

        return icon_path

    def __send_state(self):
        """
        Sends information back to javascript representing the current context.
        """
        # alert js that the state is about to change. this allows the panel to
        # clear its current state and display a loading message.
        self.adobe.context_about_to_change()

        # ---- process the context for display

        # clear existing context requests to prevent unnecessary processing
        self.__context_find_uid = None
        self.__context_thumb_uid = None
        self.__sg_data.clear()

        # determine the best entity to show for the current context
        context_entity = self.__get_context_entity()

        # this will inspect the context and do any additional queries for fields
        # that are required to show it
        self.__request_context_display(context_entity)

        # ---- the engine already has access to all the commands that need to
        #      be display for the current context. so go ahead and process those
        #      and send them back separately

        # first, we'll process the menu favorites
        fav_lookup = {}
        fav_index = 0

        # create a lookup of the combined app instance name with the display
        # name. that should be unique and provide an easy lookup to match
        # against. we'll remember the order processed in order to sort our
        # favorites list once all the registered commands are processed
        for fav_command in self.get_setting("shelf_favorites"):
            app_instance_name = fav_command["app_instance"]
            display_name = fav_command["name"]

            # build unique lookup for this combo of app instance and command
            fav_id = app_instance_name + display_name
            fav_lookup[fav_id] = fav_index

            # give it an index so that we can sort and maintain order later
            fav_index += 1

        # keep a list of each type of command since they'll be displayed
        # differently on the adobe side.
        favorites = []
        context_menu_cmds = []
        commands = []

        # iterate over all the registered commands and gather the necessary info
        # to display them in adobe
        for (command_name, command_info) in self.commands.iteritems():

            # commands come with a dict of properties that may or may not
            # contain certain data.
            properties = command_info.get("properties", {})

            # ---- determine the app's instance name

            app_instance = properties.get("app", None)
            app_name = None

            # check this command's app against the engine's apps.
            if app_instance:
                for (app_instance_name, app_instance_obj) in self.apps.items():
                    if app_instance_obj == app_instance:
                        app_name = app_instance_name

            cmd_type = properties.get("type", "default")

            # create the command dict to hand over to adobe
            command = dict(
                uid=properties.get("uid"),
                display_name=command_name,
                icon_path=self.__get_icon_path(properties),
                description=properties.get("description"),
                type=properties.get("type", "default"),
            )

            # build the lookup string to see if this app is a favorite
            fav_name = str(app_name) + command_name

            if cmd_type == "context_menu":
                # these commands will show up in the panel flyout menu
                context_menu_cmds.append(command)
            elif fav_name in fav_lookup:
                # add the fav index to the command so that we can sort after
                # all favorites are identified.
                command["fav_index"] = fav_lookup[fav_name]
                favorites.append(command)
            else:
                commands.append(command)

        # ---- include the "jump to" commands that are common to all engines

        jump_commands = []

        # the icon to use for the command. bundled with the engine
        sg_icon = os.path.join(
            self.disk_location,
            "resources",
            "shotgun_logo.png"
        )

        jump_commands.append(
            dict(
                uid=self.__jump_to_sg_command_id,
                display_name="Jump to Shotgun",
                icon_path=sg_icon,
                description="Open the current context in a web browser.",
                type="context_menu",
            )
        )

        if self.context.filesystem_locations:

            # the icon to use for the command. bundled with the engine
            fs_icon = os.path.join(
                self.disk_location,
                "resources",
                "shotgun_folder.png"
            )

            jump_commands.append(
                dict(
                    uid=self.__jump_to_fs_command_id,
                    display_name="Jump to File System",
                    icon_path=fs_icon,
                    description="Open the current context in a file browser.",
                    type="context_menu",
                )
            )

        # sort the favorites based on their index
        favorites = sorted(favorites, key=lambda d: d["fav_index"])

        # sort the other commands alphabetically by display name
        commands = sorted(commands, key=lambda d: d["display_name"])

        # sort the context menu commands alphabetically by display name. We
        # force the Jump to Shotgun and Jump to Filesystem commands onto the
        # front of the list to match other integrations.
        context_menu_cmds = jump_commands + sorted(
            context_menu_cmds,
            key=lambda d: d["display_name"],
        )

        # ---- populate the state structure to hand over to adobe

        all_commands = {
            "favorites": favorites,
            "commands": commands,
            "context_menu_cmds": context_menu_cmds,
        }

        # send the commands back to adobe
        self.adobe.send_commands(all_commands)

    def __setup_connection_timer(self, force=False):
        """
        Sets up the connection timer that handles monitoring of the live
        connection, as well as the triggering of message processing.

        :param bool force: Forces the creation of a new connection timer,
                           even if one already exists. When this occurs,
                           if a timer already exists, it will be stopped.
        """
        if self._CHECK_CONNECTION_TIMER is None or force:
            self.log_debug("Creating connection timer...")

            if self._CHECK_CONNECTION_TIMER:
                self.log_debug(
                    "Connection timer already exists, so it will be stopped."
                )

                try:
                    self._CHECK_CONNECTION_TIMER.stop()
                except Exception:
                    # No reason to be alarmed here. Just let it go and it'll
                    # garbage collected when appropriate.
                    pass

            from sgtk.platform.qt import QtCore

            timer = QtCore.QTimer(
                parent=QtCore.QCoreApplication.instance(),
            )

            timer.timeout.connect(self._check_connection)
            timer.timeout.connect(self.__check_for_popups)

            # The class variable is in seconds, so multiply to get milliseconds.
            timer.start(
                self.SHOTGUN_ADOBE_HEARTBEAT_INTERVAL * 1000.0,
            )

            self._CHECK_CONNECTION_TIMER = timer
            self.log_debug("Connection timer created and started.")

    def _jump_to_sg(self):
        """
        Jump to shotgun, launch web browser
        """
        from sgtk.platform.qt import QtGui, QtCore
        url = self.context.shotgun_url
        QtGui.QDesktopServices.openUrl(QtCore.QUrl(url))

    def _jump_to_fs(self):
        """
        Jump from context to FS
        """
        # launch one window for each location on disk
        paths = self.context.filesystem_locations
        self.logger.debug("FS paths: %s" % (str(paths),))
        for disk_location in paths:

            # get the setting
            system = sys.platform

            # run the app
            if system == "linux2":
                cmd = "xdg-open \"%s\"" % disk_location
            elif system == "darwin":
                cmd = "open \"%s\"" % disk_location
            elif system == "win32":
                cmd = "cmd.exe /C start \"Folder\" \"%s\"" % disk_location
            else:
                raise Exception("Platform '%s' is not supported." % system)

            exit_code = os.system(cmd)
            if exit_code != 0:
                self.logger.error("Failed to launch '%s'!" % cmd)

    ##########################################################################################
    # context data methods

    def __add_to_context_cache(self, path, context):
        """
        Adds the given active document path to the context cache, associating
        it with the given context object. This will trigger the storing of a
        serialized cache as a user setting for use during panel extension
        restarts.

        :param str path: The document path to add to the cache.
        :param context: The context object to associate with the document.
        """
        if path not in self._CONTEXT_CACHE:
            # We're storing the context cache in a sgtk user setting at the project
            # level. This will ensure that when we read the cache back, we'll only
            # be getting contexts in our current project. Anything outside of that
            # scope would be unusable, as we don't allow context changing across
            # project boundaries.
            self._CONTEXT_CACHE[path] = context

            serial_cache = dict()
            for k, v in self._CONTEXT_CACHE.iteritems():
                serial_cache[k] = v.serialize()

            self.logger.debug("Storing context cache: %s" % serial_cache)
            self.__settings_manager.store(
                self._CONTEXT_CACHE_KEY,
                serial_cache,
                self.__settings_manager.SCOPE_PROJECT,
            )

    def __get_from_context_cache(self, path):
        """
        Gets the document path's associated context object, if one has been cached.

        :returns: Context object, or None
        """
        return self._CONTEXT_CACHE.get(path)

    def __request_context_display(self, entity):
        """
        Request fields to show in the context header for the given entity.

        Always includes the image url to display a thumbnail.
        """

        if not entity:
            # no entity. this will retrieve the html to display for the site.
            fields_html = self.execute_hook_method(
                "context_fields_display_hook",
                "get_context_html",
                entity=None,
                sg_globals=self.__shotgun_globals,
            )
            self.adobe.send_context_display(fields_html)

            # go ahead and forward the site thumbnail back to js
            data = dict(
                thumb_path="../images/default_Site_thumb_dark.png",
                url=self.sgtk.shotgun_url,
            )
            self.adobe.send_context_thumbnail(data)
            return

        # get the fields to query from the hook
        fields = self.execute_hook_method(
            "context_fields_display_hook",
            "get_entity_fields",
            entity_type=entity["type"]
        )

        # always try to query the image for the entity
        if "image" not in fields:
            fields.append("image")

        entity_type = entity["type"]
        entity_id = entity["id"]

        # kick off an async request to query the necessary fields
        self.__context_find_uid = self.__sg_data.execute_find_one(
            entity_type, [["id", "is", entity_id]], fields)

    def __on_worker_failure(self, uid, msg):
        """
        Asynchronous callback - the worker thread errored.
        """

        # log a message if the worker failed to retrieve the necessary info.
        if uid == self.__context_find_uid:

            # clear the find id since we are now processing it
            self.__context_find_uid = None

            # send an error message back to the context header.
            self.adobe.send_context_display(
                """
                There was an error retrieving fields for this context. Please
                see the logs for the specific error message. If this is a
                recurring error and you need further assistance, please
                send an email to <a href='mailto:support@shotgunsoftware.com'>
                support@shotgunsoftware.com</a>.
                """
            )
            self.logger.error("Failed to query context fields: %s" % (msg,))

        elif uid == self.__context_thumb_uid:

            # clear the thumb id since we are now processing it
            self.__context_thumb_uid = None

            # log this. the panel will display a default thumbnail, so this
            # should be sufficient
            self.logger.error("Failed to query context thumbnail: %s" % (msg,))

    def __on_worker_signal(self, uid, request_type, data):
        """
        Signaled whenever the worker completes something.
        """

        self.logger.debug("Worker signal: %s" % (data,))

        # the find query for the context entity with the specified fields
        if uid == self.__context_find_uid:

            # clear the find id since we are now processing it
            self.__context_find_uid = None

            context_entity = data["sg"]

            # should have an image url now. submit a request to download the
            # entity's thumbnail.
            if "image" in context_entity and context_entity["image"]:
                self.__context_thumb_uid = self.__sg_data.request_thumbnail(
                    context_entity["image"],
                    context_entity["type"],
                    context_entity["id"],
                    "image",
                    load_image=False,
                )
            # no image, use a default image based on the entity type
            else:
                if context_entity["type"] in ["Asset", "Project", "Shot", "Task"]:
                    thumb_path = "../images/default_%s_thumb_dark.png" % (
                        context_entity["type"])
                    data["thumb_path"] = thumb_path
                else:
                    thumb_path = "../images/default_Entity_thumb_dark.png"

                data = dict(
                    thumb_path=thumb_path,
                    url=self.get_entity_url(context_entity),
                )
                self.adobe.send_context_thumbnail(data)

            # now that we have all the field values, go back to the hook and
            # build the html to display them.
            fields_html = self.execute_hook_method(
                "context_fields_display_hook",
                "get_context_html",
                entity=context_entity,
                sg_globals=self.__shotgun_globals,
            )

            # forward the display html back to the js panel
            self.adobe.send_context_display(fields_html)

        # thumbnail download. forward the path and a url back to js
        elif uid == self.__context_thumb_uid:

            # clear the thumb id since we already processed it
            self.__context_thumb_uid = None

            context_entity = self.__get_context_entity()

            # add a url to allow the panel to make the thumbnail clickable
            data["url"] = self.get_entity_url(context_entity)

            self.adobe.send_context_thumbnail(data)

    def __get_project_id(self):
        """Helper method to return the project id for the current context."""

        if self.context.project:
            return self.context.project["id"]
        else:
            return None

    def __get_context_entity(self):
        """Helper method to return an entity to display for current context."""

        context = self.context

        # determine the best entity for displaying the thumbnail. just return
        # the first of task, entity, project that is defined
        for entity in [context.task, context.entity, context.project]:
            if not entity:
                continue
            return entity

    def get_entity_url(self, entity):
        """Helper method to return a SG url for the supplied entity."""
        return "%s/detail/%s/%d" % (
            self.sgtk.shotgun_url, entity["type"], entity["id"])

    def get_panel_link(self, url, text):
        """
        Helper method to return an html link to display in the panel and
        will launch the supplied url in the default browser.
        """

        return \
            """
            <a
              href='#'
              class='sg_value_link'
              onclick='sg_panel.Panel.open_external_url("{url}")'
            >{text}</a>
            """.format(
                url=url,
                text=text,
            )

    def __activate_python(self):
        """
        Do the Os-specific thing to show this process above all others.
        """

        if sys.platform == "darwin":
            # a little action script to activate the given python process.
            osx_activate_script = \
                """
                tell application "System Events"
                  set frontmost of the first process whose unix id is {pid} to true
                end tell
                """.format(pid=os.getpid())

            # force this python process to the front
            cmd = ["osascript", "-e", osx_activate_script]
            status = subprocess.call(cmd)
            if status:
                self.logger.error("Could not activate python.")
        elif sys.platform == "win32":
            pass

    def __import_file(self, import_options):
        """
        Does the import and gets all new items, by caching the list of existing
        items before importing and comparing them to the list of existing items
        after importing.

        :param import_options: Import options object that should be imported
        :type import_options: `adobe.ImportOptionsObject`_
        :returns: All new items that were imported
        :rtype: list-of-`adobe.CompItemObject`_
        """

        # as the return value of importFile will not
        # reliably return the new items, one
        # has to track the changes
        item_cache = []
        for item in self.iter_collection(self.adobe.app.project.rootFolder.items):
            item_cache.append(item)

        # do the import
        self.adobe.app.project.importFile(import_options)

        new_items = []
        for item in self.iter_collection(self.adobe.app.project.rootFolder.items):
            if item not in item_cache:
                new_items.append(item)

        return new_items


