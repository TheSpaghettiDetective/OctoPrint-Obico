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
            else:
                self.notify_server_nozzlecam_complete() # edge case of single layer print - no 2nd layer to stop snapshots
                return

            time.sleep(1)

    def inject_cmds_and_initiate_scan(self):
        if not self.on_first_layer:
            self.on_first_layer = True

            job_info = self.plugin._printer.get_current_job() #store current job info
            job_temps = self.plugin._printer.get_current_temperatures() #store current temps for job
            
            self.plugin._printer.extrude(-10) #replace with saved val TODO
            self.plugin._printer.set_temperature('tool0', 170) #how many tools? TODO

            self.plugin._printer.jog([0,0,10]) #move up 10m relative to current TODO -> replace with kenneth calculation

            #SCAN DIMENSIONS OF PRINT - for loop using job values
                #move back left of print
                #start photos
                    # run_in_thread(self.plugin.nozzlecam.start)
                #for loop using jog command - SLOW SPEED  

                #stop photos / thread & send to server

            #HEAT BACK UP TO JOB_TEMPS
            #WHEN DONE, CONTINUE TO FINAL CMDS

            self.plugin._printer.extrude(10) #replace with saved val TODO
            octoprint.set_job_on_hold(False) #resume print

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
            info = server_request('GET', f'/ent/api/printers/{printer_id}/ext/', self.plugin, timeout=60, headers=self.plugin.auth_headers())
            ext_info = info.json().get('ext')
            # get retract info from API
            # retract_value = ext_info.get('retract_value', 10) # default to 10mm TODO
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
