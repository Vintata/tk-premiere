import os

import sgtk


HookBaseClass = sgtk.get_hook_baseclass()


class ImportFootage(HookBaseClass):
    """
    Used to control the way the current context fields are displayed.
    """

    __DEFAULT_IMPORT_TYPES = None

    @property
    def _default_import_types(self):
        if self.__DEFAULT_IMPORT_TYPES is None:
            # the following dict allows to set default values
            # for specific file types
            self.__DEFAULT_IMPORT_TYPES = {
                ".mov": self.adobe.ImportAsType.FOOTAGE,
                ".avi": self.adobe.ImportAsType.FOOTAGE
            }
        return self.__DEFAULT_IMPORT_TYPES

    def __get_import_type(self, import_options):
        """
        Helper to determine, what import type the given import candidate
        needs. Assuming an *.aep file was given this method will return a
        PROJECT, whereas if an *.jpg was given it will return FOOTAGE

        Note::

        It is possible to overwrite the default behavior by editing this method.
        For example would *.mov normally return PROJECT but this method will return
        FOOTAGE instead.

        :param import_options: adobe.ImportOptions object that should be imported
        :returns: int or None. None indicates, that the current object cannot be imported
            an integer will be the adobe.ImportAsType-constant that should be used,
            when importing
        """
        _, ext = os.path.splitext(import_options.file.fsName)
        if ext in self._default_import_types:
            return self._default_import_types[ext]

        # find out what type of footage we try to import
        # Note: this order is important as we skip as soon as we can
        #       import a piece of footage in a certain way
        import_types = [
            self.adobe.ImportAsType.PROJECT,  # aep
            self.adobe.ImportAsType.COMP,  # psd, aep
            self.adobe.ImportAsType.COMP_CROPPED_LAYERS,  # aep, psd fallback
            self.adobe.ImportAsType.FOOTAGE,  # jpg
        ]

        for each_type in import_types:
            if import_options.canImportAs(each_type):
                return each_type
        return None

    def set_import_options(self, import_options):
        """
        This method is called in case the engines' import_filepath
        method is called. It is used to set the correct parameters to the
        given `adobe.ImportOptionsObject`_.

        This method should modify the incoming object with the correct
        settings for the given filepath.

        The filepath can be accesssed by doing ``import_options.file.fsName``.

        :param import_options: The import options as set by After Effects
        :type import_options: `adobe.ImportOptionsObject`_.
        """
        self.adobe = self.parent.adobe
        path = import_options.file.fsName
        import_type = self.__get_import_type(import_options)
        if import_type is None:
            self.logger.warn("Filepath {!r} cannot be imported.".format(path))
            return []
        import_options.importAs = import_type

        sequence_range = self.parent.find_sequence_range(path)
        if sequence_range and sequence_range[0] != sequence_range[1]:
            import_options.sequence = True
            import_options.forceAlphabetical = True


