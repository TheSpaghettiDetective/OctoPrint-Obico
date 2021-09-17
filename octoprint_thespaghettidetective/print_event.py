import logging
import time
import threading
from octoprint.filemanager.analysis import QueueEntry

_logger = logging.getLogger('octoprint.plugins.thespaghettidetective')


class PrintEventTracker:

    def __init__(self):
        self._mutex = threading.RLock()
        self.current_print_ts = -1    # timestamp as print_ts coming from octoprint
        self.tsd_gcode_file_id = None
        self._file_metadata_cache = None

    def on_event(self, plugin, event, payload):
        with self._mutex:
            if event == 'PrintStarted':
                self.current_print_ts = int(time.time())
                self._file_metadata_cache = None

        data = self.octoprint_data(plugin)
        data['octoprint_event'] = {
            'event_type': event,
            'data': payload
        }

        # Unsetting self.current_print_ts should happen after it is captured in payload to make sure last event of a print contains the correct current_print_ts
        with self._mutex:
            if event == 'PrintFailed' or event == 'PrintDone':
                self.current_print_ts = -1
                self.tsd_gcode_file_id = None
                self._file_metadata_cache = None

        return data

    def octoprint_data(self, plugin, status_only=False):
        data = {
            'octoprint_data': plugin._printer.get_current_data()
        }

        with self._mutex:
            data['current_print_ts'] = self.current_print_ts
            if self.tsd_gcode_file_id:
                data['tsd_gcode_file_id'] = self.tsd_gcode_file_id

        data['octoprint_data']['temperatures'] = plugin._printer.get_current_temperatures()
        data['octoprint_data']['_ts'] = int(time.time())

        if status_only:
            if self._file_metadata_cache:
                data['octoprint_data']['file_metadata'] = self._file_metadata_cache
            return data

        data['octoprint_data']['file_metadata'] = self._file_metadata_cache = self.get_file_metadata(plugin, data)

        octo_settings = plugin.octoprint_settings_updater.as_dict()
        if octo_settings:
            data['octoprint_settings'] = octo_settings

        return data

    def set_tsd_gcode_file_id(self, tsd_gcode_file_id):
        with self._mutex:
            self.tsd_gcode_file_id = tsd_gcode_file_id

    def get_tsd_gcode_file_id(self):
        with self._mutex:
            return self.tsd_gcode_file_id

    def get_file_metadata(self, plugin, data):
        try:
            current_file = data.get('octoprint_data', {}).get('job', {}).get('file', {})
            origin = current_file.get('origin')
            path = current_file.get('path')
            if not origin or not path:
                return None

            file_metadata = plugin._file_manager._storage_managers.get(origin).get_metadata(path)
            return {'analysis': {'printingArea': file_metadata.get('analysis', {}).get('printingArea')}} if file_metadata else None
        except Exception as e:
            _logger.exception(e)
            return None
