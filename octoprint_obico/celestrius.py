import logging
import time
from octoprint_obico.utils import server_request

from octoprint_obico.webcam_capture import capture_jpeg

_logger = logging.getLogger('obico.celestrius')

class Celestrius:

    def __init__(self, plugin):
        self.plugin = plugin
        self.on_first_layer = False
        self.snapshot_count = 0 # index  / key attribute to give image a unique filename

    def start(self):
        #TODO block users with no nozzle cam config
        while True:
            if self.on_first_layer == True:
                try:
                    self.send_celestrius_jpeg(capture_jpeg(self.plugin)) #TODO replace argument with nozzle cam config
                    _logger.debug('Celestrius Jpeg captured & sent')
                except Exception as e:
                    _logger.warning('Failed to capture jpeg - ' + str(e))
            time.sleep(0.2) #TODO how many photos do we want?


    def send_celestrius_jpeg(self, snapshot):
        if snapshot: #TODO update with new endpoint & 
            try:
                files = {'pic': snapshot}
                server_request('POST', '/api/v1/octo/printer_events/', self.plugin, timeout=60, raise_exception=True, files=files, data=None, headers=self.plugin.auth_headers())
            except Exception as e:
                _logger.warning('Failed to post jpeg - ' + str(e))

    def notify_server_celestrius_complete(self):
        self.on_first_layer = False
        try:
            data = {'celestrius_status': 'complete' }
            #TODO update with new endpoint & data
            server_request('POST', '/api/v1/octo/printer_events/', self.plugin, timeout=60, raise_exception=True, files=None, data=data, headers=self.plugin.auth_headers())
        except Exception as e:
            _logger.warning('Failed to notify celestrius completed' + str(e))
