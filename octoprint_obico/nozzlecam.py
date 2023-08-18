import logging
import time
from octoprint_obico.utils import server_request
from octoprint_obico.webcam_capture import capture_jpeg
_logger = logging.getLogger('obico.nozzlecam')

class NozzleCam:

    def __init__(self, plugin):
        self.plugin = plugin
        self.on_first_layer = False
        self.nozzle_config = None

    def start(self):
        while not self.plugin.linked_printer.get('id', None): #main loop waits for auth token -  need to guarantee we have the printerID before calling DB
            time.sleep(1)
        self.nozzle_config = self.create_nozzlecam_config()

        while True:
            if not self.nozzle_config: # cut out loop if nozzlecam not set up
                return
            if self.on_first_layer == True:
                try:
                    self.send_nozzlecam_jpeg(capture_jpeg(self.nozzle_config, use_nozzle_config=True))
                    _logger.debug('Nozzle cam Jpeg captured & sent')
                except Exception as e:
                    _logger.warning('Failed to capture jpeg - ' + str(e))
            time.sleep(0.2)

    def send_nozzlecam_jpeg(self, snapshot):
        if snapshot:
            try:
                files = {'pic': snapshot}
                data = {'viewing_boost': 'true'}
                server_request('POST', '/ent/api/nozzle_cam/pic/', self.plugin, timeout=60, files=files, data=data, skip_debug_logging=True, headers=self.plugin.auth_headers())
            except Exception as e:
                _logger.warning('Failed to post jpeg - ' + str(e))

    def notify_server_nozzlecam_complete(self):
        self.on_first_layer = False
        try:
            data = {'nozzlecam_status': 'complete'}
            server_request('POST', '/ent/api/nozzle_cam/first_layer_done/', self.plugin, timeout=60, files={}, data=data, skip_debug_logging=True, headers=self.plugin.auth_headers())
            _logger.debug('server notified 1st layer is done')
        except Exception as e:
            _logger.warning('Failed to notify 1st layer completed' + str(e))

    def create_nozzlecam_config(self):
        try:
            printer_id = self.plugin.linked_printer.get('id')
            info = server_request('GET', f'/ent/api/printers/{printer_id}/ext/', self.plugin, timeout=60, files={}, data={}, skip_debug_logging=True, headers=self.plugin.auth_headers())
            ext_info = info.json().get('ext')
            nozzle_url = ext_info.get('nozzlecam_url')
            if len(nozzle_url) == 0:
                return None
            else:
                return {
                    'snapshot': nozzle_url,
                    'snapshotSslValidation': False
                }
        except Exception as e:
            _logger.warning('Failed to build nozzle config - ' + str(e))
            return None
