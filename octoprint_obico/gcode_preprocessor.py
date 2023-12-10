import logging
import re
import sys
import time
import octoprint


_logger = logging.getLogger('octoprint.plugins.obico')
__python_version__ = 3 if sys.version_info >= (3, 0) else 2


# Credit: Thank you j7126 for your awesome octoprint plugin: https://github.com/j7126/OctoPrint-Dashboard
class GcodePreProcessor(octoprint.filemanager.util.LineProcessorStream):

    LAYER_INDICATOR_PATTERNS = [
            dict(slicer='CURA',
                regx=r'^;LAYER:([0-9]+)'),
            dict(slicer='Simplify3D',
                regx=r'^; layer ([0-9]+)'),
            dict(slicer='Slic3r/PrusaSlicer',
                regx=r'^;BEFORE_LAYER_CHANGE'),
            dict(slicer='Almost Everyone',
                regx=r"^;(( BEGIN_|BEFORE_)+LAYER_(CHANGE|OBJECT)|LAYER:[0-9]+| [<]{0,1}layer [0-9]+[>,]{0,1}).*$")
        ]


    def __init__(self, file_buffered_reader, plugin, file_path):
        super(GcodePreProcessor, self).__init__(file_buffered_reader)
        self.plugin = plugin
        self.file_path = file_path
        self.layer_count = 0

    def process_line(self, line):
        if not len(line):
            return None

        if __python_version__ == 3:
            line = line.decode('utf-8').lstrip()
        else:
            line = line.lstrip()

        for layer_indicator_pattern in self.LAYER_INDICATOR_PATTERNS:

            if re.match(layer_indicator_pattern['regx'], line):
                self.layer_count += 1
                line = line + "M117 OBICO_LAYER_INDICATOR " + str(self.layer_count) + "\r\n"

                break

        line = line.encode('utf-8')

        return line

    def close(self):
        if self.layer_count == 0:
            self.layer_count = None #set None if no layers found - Klipper returns None as well so less checks needed on frontend
        else:
            self.layer_count += 1 #add last layer to count - match dashboard

        self.plugin._file_manager.set_additional_metadata('local', self.file_path, 'obico', {"totalLayerCount": self.layer_count}, overwrite=True)


## A Wrapper so that the preprocessor can access the plugin itself.
class GcodePreProcessorWrapper:

    def __init__(self, plugin):
        self.plugin = plugin

    def gcode_preprocessor(self, path, file_object, blinks=None, printer_profile=None, allow_overwrite=True, *args, **kwargs):
        filename = file_object.filename
        if not octoprint.filemanager.valid_file_type(filename, type="gcode"):
            return file_object
        return octoprint.filemanager.util.StreamWrapper(filename, GcodePreProcessor(file_object.stream(), self.plugin, path))
