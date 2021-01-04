import logging
import threading
from octoprint.filemanager.analysis import QueueEntry
import re

RE_PRINT_EVENT = re.compile(r'^Print[A-Z]')

_logger = logging.getLogger('octoprint.plugins.thespaghettidetective')


class PrintEventTracker:

    def __init__(self):
        self._mutex = threading.RLock()
        self.current_print_ts = -1    # timestamp as print_ts coming from octoprint
        self.tsd_gcode_file_id = None

    def on_event(self, plugin, event, payload, at):
        with self._mutex:
            if event == 'PrintStarted':
                self.current_print_ts = int(at)  # TODO increase resolution *100?
            elif self.current_print_ts == -1 and RE_PRINT_EVENT.match(event):
                plugin.sentry.captureMessage(
                    'Got Print<Event> before PrintStarted',
                    extra={'event': event, 'payload': payload, 'at': at}
                )

        data = self.octoprint_data(plugin, at)
        data['octoprint_event'] = {
            'event_type': event,
            'data': payload,
        }

        # Unsetting self.current_print_ts should happen after it is captured in payload to make sure last event of a print contains the correct current_print_ts
        with self._mutex:
            if event == 'PrintFailed' or event == 'PrintDone':
                self.current_print_ts = -1
                self.tsd_gcode_file_id = None

        return data

    def octoprint_data(self, plugin, at):
        data = {
            'octoprint_data': plugin._printer.get_current_data(),
            'octoprint_temperatures': plugin._printer.get_current_temperatures(),
            '_at': at,
        }
        data['octoprint_data']['file_metadata'] = self.get_file_metadata(plugin, data)

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

    def get_file_metadata(self, plugin, data):
        try:
            current_file = data.get('octoprint_data', {}).get('job', {}).get('file', {})
            origin = current_file.get('origin')
            path = current_file.get('path')
            if not origin or not path:
                return None

            file_metadata = plugin._file_manager._storage_managers.get(origin).get_metadata(path)
            return {'analysis': {'printingArea': file_metadata.get('analysis', {}).get('printingArea')}}
        except Exception as e:
            _logger.exception(e)
            return None
