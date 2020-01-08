import logging
import time
import threading

_logger = logging.getLogger('octoprint.plugins.thespaghettidetective')

class PrintEventTracker:

    def __init__(self):
        self._mutex = threading.RLock()
        self.current_print_ts = -1    # timestamp as print_ts coming from octoprint
        self.tsd_gcode_file_id = None

    def on_event(self, plugin, event, payload):
        with self._mutex:
            if event == 'PrintStarted':
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
            'octoprint_settings': plugin.octoprint_settings(),
            }

        with self._mutex:
            data['current_print_ts'] = self.current_print_ts
            if self.tsd_gcode_file_id:
                data['tsd_gcode_file_id'] = self.tsd_gcode_file_id

        return data

    def set_tsd_gcode_file_id(self, tsd_gcode_file_id):
        with self._mutex:
            self.tsd_gcode_file_id = tsd_gcode_file_id
