import re
import logging
import time
import threading
import os
from octoprint.filemanager.analysis import QueueEntry

from .utils import server_request
_logger = logging.getLogger('octoprint.plugins.obico')


MAX_GCODE_DOWNLOAD_SECONDS = 30 * 60

class PrintJobTracker:

    def __init__(self):
        self._mutex = threading.RLock()
        self.current_print_ts = -1    # timestamp when current print started, acting as a unique identifier for a print
        self.obico_g_code_file_id = None
        self._file_metadata_cache = None
        self.current_layer_height = None
        self.gcode_downloading_started = None

    def on_event(self, plugin, event, payload):

        def find_obico_g_code_file_id(payload):
            md5_hash = plugin._file_manager.get_metadata(path=payload['path'], destination=payload['origin']).get('hash')
            if not md5_hash:
                return None

            g_code_data = dict(
                filename=payload['name'],
                safe_filename=os.path.basename(payload['path']),
                num_bytes=payload['size'],
                agent_signature='md5:{}'.format(md5_hash),
                url = payload['path']
                )
            resp = server_request('POST', '/api/v1/octo/g_code_files/', plugin, timeout=60, data=g_code_data, headers=plugin.auth_headers())

            return resp.json()['id'] if resp else None

        if event == 'PrintStarted':
            with self._mutex:
                self.current_print_ts = int(time.time())
                self._file_metadata_cache = None

            self.set_obico_g_code_file_id(find_obico_g_code_file_id(payload))

        data = self.status(plugin)
        data['event'] = {
            'event_type': event,
            'data': payload
        }

        # Unsetting self.current_print_ts should happen after it is captured in payload to make sure last event of a print contains the correct current_print_ts
        with self._mutex:
            if event == 'PrintFailed' or event == 'PrintDone':
                self.current_print_ts = -1
                self.set_obico_g_code_file_id(None)
                self._file_metadata_cache = None
                self.current_layer_height = None

                # First layer AI
                plugin.nozzlecam.on_first_layer = False # catch-all to make sure /nozzle_cam/first_layer_done/ is called in case such as canceled mid first layer.

        return data

    def status(self, plugin, status_only=False):
        data = {
            'status': plugin._printer.get_current_data()
        }

        with self._mutex:
            data['current_print_ts'] = self.current_print_ts
            current_file = data.get('status', {}).get('job', {}).get('file')
            if self.get_obico_g_code_file_id() and current_file:
                current_file['obico_g_code_file_id'] = self.get_obico_g_code_file_id()

            # Injecting a 'G-Code Downloading' state so that the client side can treat it as a transition state
            if self.gcode_downloading_started is not None:
                if data.get('status', {}).get('state', {}).get('text') != 'Operational': # It is in an unexpected state. Something has gone wrong
                    self.set_gcode_downloading_started(None)
                elif time.time() - self.gcode_downloading_started > MAX_GCODE_DOWNLOAD_SECONDS: # For the edge case that the download thread died without an exception
                    self.set_gcode_downloading_started(None)
                else:
                    data['status']['state']['text'] = 'G-Code Downloading'
                    data['status']['state']['flags']['operational'] = False

        # Apparently printers like Prusa throws random temperatures here. This should be consistent with OctoPrint, which only keeps r"^(tool\d+|bed|chamber)$"
        temperatures = {}
        for (k,v) in plugin._printer.get_current_temperatures().items():
            if re.search(r'^(tool\d+|bed|chamber)$', k):
                temperatures[k] = v

        data['status']['temperatures'] = temperatures
        data['status']['_ts'] = int(time.time())
        data['status']['currentLayerHeight'] = self.current_layer_height # use camel-case to be consistent with the existing convention

        if status_only:
            if self._file_metadata_cache:
                data['status']['file_metadata'] = self._file_metadata_cache
            return data

        data['status']['file_metadata'] = self._file_metadata_cache = self.get_file_metadata(plugin, data)

        octo_settings = plugin.octoprint_settings_updater.as_dict()
        if octo_settings:
            data['settings'] = octo_settings

        return data

    def increment_layer_height(self, val):
        with self._mutex:
            self.current_layer_height = val

    def set_obico_g_code_file_id(self, obico_g_code_file_id):
        with self._mutex:
            self.obico_g_code_file_id = obico_g_code_file_id

    def get_obico_g_code_file_id(self):
        with self._mutex:
            return self.obico_g_code_file_id

    def set_gcode_downloading_started(self, timestamp):
        with self._mutex:
            self.gcode_downloading_started = timestamp

    def get_file_metadata(self, plugin, data):
        try:
            current_file = data.get('status', {}).get('job', {}).get('file', {})
            origin = current_file.get('origin')
            path = current_file.get('path')
            if not origin or not path:
                return None

            return plugin._file_manager._storage_managers.get(origin).get_metadata(path) or {}
        except Exception as e:
            _logger.exception(e)
            return None
