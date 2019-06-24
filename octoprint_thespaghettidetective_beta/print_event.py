import logging
import time

_logger = logging.getLogger('octoprint.plugins.thespaghettidetective_beta')

class PrintEventTracker:

    def __init__(self):
        self.current_print_ts = -1    # timestamp as print_ts coming from octoprint

    def on_event(self, plugin, event, payload):
        #Cancel if event is about timelapse (this happends when jpgs are sent to TSD)
        if event == 'CaptureStart' or event == 'CaptureDone':
            return
        
        print_ts = self.current_print_ts

        if event == 'PrintStarted':
            self.current_print_ts = int(time.time())
            print_ts = self.current_print_ts
        elif event == 'PrintFailed' or event == 'PrintDone':
            self.current_print_ts = -1

        return {
            'current_print_ts': print_ts,
            'octoprint_event': {
                'event_type': event,
                'data': payload
                },
            'octoprint_data': plugin.octoprint_data()
            }

    def octoprint_data(self, plugin):
        return {
            'current_print_ts': self.current_print_ts,
            'octoprint_data': plugin.octoprint_data(),
            'octoprint_temps': plugin._printer.get_current_temperatures(),
            'octoprint_settings': plugin.octoprint_settings(),
            }

