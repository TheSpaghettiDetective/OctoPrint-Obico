import re
import logging
import time
import threading
from octoprint.filemanager.analysis import QueueEntry

_logger = logging.getLogger('octoprint.plugins.obico')


class PrintJobTracker:

    def __init__(self):
        self._mutex = threading.RLock()
        self.current_print_ts = -1    # timestamp when current print started, acting as a unique identifier for a print
        self.obico_gcode_file = None
        self._file_metadata_cache = None

    def on_event(self, plugin, event, payload):
        with self._mutex:
            if event == 'PrintStarted':
                self.current_print_ts = int(time.time())
                self._file_metadata_cache = None
                if self.obico_gcode_file['filename'] != payload['name']: # If user starts printing a different file before Obico has done downloading the gcode file
                    self.set_obico_gcode_file(None)

        data = self.status(plugin)
        data['event'] = {
            'event_type': event,
            'data': payload
        }

        # Unsetting self.current_print_ts should happen after it is captured in payload to make sure last event of a print contains the correct current_print_ts
        with self._mutex:
            if event == 'PrintFailed' or event == 'PrintDone':
                self.current_print_ts = -1
                self.set_obico_gcode_file(None)
                self._file_metadata_cache = None

        return data

    def status(self, plugin, status_only=False):
        data = {
            'status': plugin._printer.get_current_data()
        }

        with self._mutex:
            data['current_print_ts'] = self.current_print_ts
            current_file = data.get('status', {}).get('job', {}).get('file')
            if self.get_obico_gcode_file() and current_file:
                current_file['obico_gcode_file_id'] = self.get_obico_gcode_file()['id']

        # Apparently printers like Prusa throws random temperatures here. This should be consistent with OctoPrint, which only keeps r"^(tool\d+|bed|chamber)$"
        temperatures = {}
        for (k,v) in plugin._printer.get_current_temperatures().items():
            if re.search(r'^(tool\d+|bed|chamber)$', k):
                temperatures[k] = v

        data['status']['temperatures'] = temperatures
        data['status']['_ts'] = int(time.time())

        if status_only:
            if self._file_metadata_cache:
                data['status']['file_metadata'] = self._file_metadata_cache
            return data

        data['status']['file_metadata'] = self._file_metadata_cache = self.get_file_metadata(plugin, data)

        octo_settings = plugin.octoprint_settings_updater.as_dict()
        if octo_settings:
            data['settings'] = octo_settings

        return data

    def set_obico_gcode_file(self, obico_gcode_file):
        with self._mutex:
            self.obico_gcode_file = obico_gcode_file

    def get_obico_gcode_file(self):
        with self._mutex:
            return self.obico_gcode_file

    def get_file_metadata(self, plugin, data):
        try:
            current_file = data.get('status', {}).get('job', {}).get('file', {})
            origin = current_file.get('origin')
            path = current_file.get('path')
            if not origin or not path:
                return None

            file_metadata = plugin._file_manager._storage_managers.get(origin).get_metadata(path)
            return {'analysis': {'printingArea': file_metadata.get('analysis', {}).get('printingArea')}} if file_metadata else None
        except Exception as e:
            _logger.exception(e)
            return None
