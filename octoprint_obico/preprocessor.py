import re
import sys
import octoprint.filemanager



__python_version__ = 3 if sys.version_info >= (3, 0) else 2




class GcodePreProcessor(octoprint.filemanager.util.LineProcessorStream):

    def __init__(self, fileBufferedReader, layer_indicator_patterns ):
        super(GcodePreProcessor, self).__init__(fileBufferedReader)
        self.layer_indicator_patterns = layer_indicator_patterns
        self.python_version = __python_version__
        self.layer_count = 0

    def process_line(self, line):
        if not len(line):
            return None

        if self.python_version == 3:
            line = line.decode('utf-8').lstrip()
        else:
            line = line.lstrip()

        for layer_indicator_pattern in self.layer_indicator_patterns:

            if re.match(layer_indicator_pattern['regx'], line):
                self.layer_count += 1
                line = line + "M117 DASHBOARD_LAYER_INDICATOR " + str(self.layer_count) + "\r\n"

                break

        line = line.encode('utf-8')

        return line