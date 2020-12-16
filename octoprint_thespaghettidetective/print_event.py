import logging
import re
import time
import threading
from octoprint.filemanager.analysis import QueueEntry

_logger = logging.getLogger('octoprint.plugins.thespaghettidetective')

# should match all print related events (having shape of PrintEventname)
RE_PRINT_EVENT = re.compile(r'^Print[A-Z]')


class PrintEventTracker:

    def __init__(self):
        self._mutex = threading.RLock()
        self.current_print_ts = -1    # timestamp as print_ts coming from octoprint
        self.tsd_gcode_file_id = None

    def on_event(self, plugin, event, payload):
        with self._mutex:
            if event == 'PrintStarted':
                self.current_print_ts = int(time.time())
            elif self.current_print_ts == -1 and RE_PRINT_EVENT.match(event):
                # started while printing? lets assign an id
                self.current_print_ts = int(time.time())

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

        return data

    def octoprint_data(self, plugin):
        data = {
            'octoprint_data': plugin._printer.get_current_data(),
            'octoprint_temperatures': plugin._printer.get_current_temperatures(),
        }

        current_file_metadata = None
        current_file = data.get('octoprint_data', {}).get(
            'job', {}).get('file', {})
        if current_file.get('path') and current_file.get('origin'):
            current_file_metadata = self.get_file_metadata(
                plugin, current_file)
        data['octoprint_data']['file_metadata'] = current_file_metadata

        octo_settings = plugin.octoprint_settings_updater.as_dict()
        if octo_settings:
            data['octoprint_settings'] = octo_settings

        with self._mutex:
            data['current_print_ts'] = self.current_print_ts
            if self.tsd_gcode_file_id:
                data['tsd_gcode_file_id'] = self.tsd_gcode_file_id

        return data

    def set_tsd_gcode_file_id(self, tsd_gcode_file_id):
        with self._mutex:
            self.tsd_gcode_file_id = tsd_gcode_file_id

    def get_tsd_gcode_file_id(self):
        with self._mutex:
            return self.tsd_gcode_file_id

    def get_file_metadata(self, plugin, current_file):

        # somehow _storage_managers.get('sdcard') always returns None. It seems to work only with 'local'
        if not current_file.get('origin') == 'local':
            return None

        file_metadata = plugin._file_manager._storage_managers.get(
            current_file.get('origin')).get_metadata(current_file.get('path'))
        if not file_metadata:
            return None

        return {'analysis': {'printingArea': file_metadata.get('analysis', {}).get('printingArea')}}
