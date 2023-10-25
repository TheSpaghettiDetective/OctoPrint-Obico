import logging
import time
import octoprint
from octoprint_obico.utils import server_request
from octoprint_obico.webcam_capture import capture_jpeg
from .utils import run_in_thread

_logger = logging.getLogger('octoprint.plugins.obico')

class NozzleCam:

    def __init__(self, plugin):
        self.plugin = plugin
        self.on_first_layer = False
        self.nozzle_config = None

    def start(self):
        if not self.nozzle_config: # cut out loop if nozzlecam not set up
            self.on_first_layer = False
            return

        while True:
            if self.on_first_layer == True:
                try:
                    self.send_nozzlecam_jpeg(capture_jpeg(self.nozzle_config, use_nozzle_config=True))
                except Exception:
                    _logger.error('Failed to capture and send nozzle cam jpeg', exc_info=True)

            time.sleep(1)

    def inject_cmds_and_initiate_scan(self):
        if not self.on_first_layer:
            self.on_first_layer = True
            
            #get job info
            job_info = self.plugin._printer.get_current_job()
            filepath = job_info.get('file', {}).get('path', None)
            file_metadata = self.plugin._file_manager.get_metadata(path=filepath, destination='local')
            maxX = file_metadata.get('analysis', {}).get('printingArea', {}).get('maxX', None)
            minX = file_metadata.get('analysis', {}).get('printingArea', {}).get('maxX', None)
            maxY = file_metadata.get('analysis', {}).get('printingArea', {}).get('maxY', None)
            minY = file_metadata.get('analysis', {}).get('printingArea', {}).get('maxY', None)

            #get temperature info
            job_temps = self.plugin._printer.get_current_temperatures()
            tool0_temp = job_temps.get('tool0', {}).get('actual', 220)

            #prepare for scan
            self.plugin._printer.extrude(-10) #replace with saved val TODO
            self.plugin._printer.set_temperature('tool0', 170) #how many tools? TODO
            self.plugin._printer.jog({'z':10}, True,) #move up 10m relative to current TODO -> replace with kenneth calculation
            #move to corner of print & start photos
            self.plugin._printer.jog({'x':minX, 'y':minY }, False)
            # run_in_thread(self.start)


            #scan bed
            for i in range(round(minY), round(maxY), 10):
                _logger.debug('Y = {0}'.format(i))
                self.plugin._printer.jog({'y':i }, False, 600)
                for k in range(round(minX), round(maxX), 10):
                    self.plugin._printer.jog({'y':i }, False, 600)
                    _logger.debug('Y = {0}'.format(k))
            # import pdb; pdb.set_trace()

            
            #stop photos & notify to server
            self.on_first_layer = False
            #wait & heat back up to job temps
            self.plugin._printer.set_temperature('tool0', tool0_temp) #how many tools? TODO
            while self.plugin._printer.get_current_temperatures().get('tool0', {}).get('actual', 0) < tool0_temp:
                time.sleep(1)
            self.plugin._printer.extrude(10) #replace with saved val TODO

            #resume print
            self.notify_server_nozzlecam_complete()
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
            # general_printer_info = server_request('GET', f'/api/v1/printers/{printer_id}/', self.plugin, timeout=60, headers=self.plugin.auth_headers())
            ext_info = raw_ext_info.json().get('ext')
            # import pdb; pdb.set_trace()
            # get retract info from API
            # retract_value = general_printer_info.get('retract_value', 10) # default to 10mm TODO
            _logger.debug('Printer ext info: {}'.format(ext_info))
            nozzle_url = ext_info.get('nozzlecam_url', None)
            if not nozzle_url or len(nozzle_url) == 0:
                _logger.warning('No nozzlecam config found')
                return
            else:
                self.nozzle_config = {
                    'snapshot': nozzle_url,
                    'snapshotSslValidation': False,
                    # 'retract_value': retract_value,
                }
        except Exception:
            _logger.error('Failed to build nozzle config', exc_info=True)
            return
