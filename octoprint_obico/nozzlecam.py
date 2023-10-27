import logging
import time
import octoprint
import re
from octoprint_obico.utils import server_request
from octoprint_obico.webcam_capture import capture_jpeg
from .utils import run_in_thread

_logger = logging.getLogger('octoprint.plugins.obico')

class NozzleCam:

    def __init__(self, plugin):
        self.plugin = plugin
        self.on_first_layer = False
        self.nozzle_config = None
        self.first_layer_scan_enabled = True

    def start(self):
        while True:
            if self.on_first_layer == True:
                try:
                    self.send_nozzlecam_jpeg(capture_jpeg(self.nozzle_config, use_nozzle_config=True))
                except Exception:
                    _logger.error('Failed to capture and send nozzle cam jpeg', exc_info=True)

            time.sleep(1)

    def inject_cmds_and_initiate_scan(self):
        if not self.on_first_layer and self.nozzle_config and self.first_layer_scan_enabled:
            self.on_first_layer = True
            
            #get job info
            job_info = self.plugin._printer.get_current_job()
            filepath = job_info.get('file', {}).get('path', None)
            file_metadata = self.plugin._file_manager.get_metadata(path=filepath, destination='local')
            maxX = file_metadata.get('analysis', {}).get('printingArea', {}).get('maxX', None)
            minX = file_metadata.get('analysis', {}).get('printingArea', {}).get('minX', None)
            maxY = file_metadata.get('analysis', {}).get('printingArea', {}).get('maxY', None)
            minY = file_metadata.get('analysis', {}).get('printingArea', {}).get('minY', None)

            #fallback to bed size if print size is unavailable
            if not maxX or not minX or not maxY or not minY:
                printer_profile = self.plugin._printer_profile_manager.get_current_or_default()
                maxX = printer_profile['volume']['width']
                maxX = printer_profile['volume']['depth']
                minX = 0
                minY = 0

            retract_on_pause = self.plugin.linked_printer.get('retract_on_pause', 10)
            self.plugin._printer.extrude(retract_on_pause * -1)

            #get job temperature info & set to 170
            job_temps = self.plugin._printer.get_current_temperatures()
            for key, value in job_temps.items():
                if re.match(r'tool\d', key):
                    self.plugin._printer.set_temperature(key, 170)

            #move to corner of print & start photos
            self.plugin._printer.jog({'z':1}, False,)
            self.plugin._printer.jog({'x':minX, 'y':minY }, False)
            # run_in_thread(self.start)

            #scan bed
            for i in range(round(minY), round(maxY), 10):
                self.plugin._printer.jog({'y':i }, False, 300)
                self.plugin._printer.jog({'x':maxX }, False, 300)
                self.plugin._printer.jog({'x':minX }, False, 300)

            #stop photos & notify to server
            self.on_first_layer = False
            self.notify_server_nozzlecam_complete()

            #heat back up to job temps
            for key, value in job_temps.items():
                if re.match(r'tool\d', key):
                    self.plugin._printer.set_temperature(key, value.get('target', 220))

            self.plugin._printer.extrude(retract_on_pause)
            self.plugin._printer.set_job_on_hold(False) 
        else:
            self.plugin._printer.set_job_on_hold(False)

    def send_nozzlecam_jpeg(self, snapshot):
        if snapshot:
            files = {'pic': snapshot}
            resp = server_request('POST', '/ent/api/nozzle_cam/pic/', self.plugin, timeout=60, files=files, skip_debug_logging=True, headers=self.plugin.auth_headers())
            _logger.debug('nozzle cam jpeg posted to server - {0}'.format(resp))

    def notify_server_nozzlecam_complete(self):
        if self.nozzle_config is None:
            return
        try:
            data = {'nozzlecam_status': 'complete'}
            server_request('POST', '/ent/api/nozzle_cam/first_layer_done/', self.plugin, timeout=60, data=data, headers=self.plugin.auth_headers())
            _logger.debug('server notified 1st layer is done')
        except Exception:
            _logger.error('Failed to notify 1st layer completed', exc_info=True)

    def create_nozzlecam_config(self):
        try:
            printer_id = self.plugin.linked_printer.get('id')
            raw_ext_info = server_request('GET', f'/ent/api/printers/{printer_id}/ext/', self.plugin, timeout=60, headers=self.plugin.auth_headers())
            ext_info = raw_ext_info.json().get('ext')
            nozzle_url = ext_info.get('nozzlecam_url', None)
            self.first_layer_scan_enabled = ext_info.get('first_layer_scan_enabled', True)
            _logger.debug('Printer ext info: {}'.format(ext_info))
            if not nozzle_url or len(nozzle_url) == 0:
                _logger.warning('No nozzlecam config found')
                return
            else:
                self.nozzle_config = {
                    'snapshot': nozzle_url,
                    'snapshotSslValidation': False,
                }
        except Exception:
            _logger.error('Failed to build nozzle config', exc_info=True)
            return
