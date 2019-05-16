import logging
import time

_logger = logging.getLogger('octoprint.plugins.thespaghettidetective_beta')

class PrintEventTracker:

    def __init__(self):
        self.current_print_uuid = None

    def on_event(self, plugin, event, payload):

        if not self.current_print_uuid:
            if event != 'PrintStarted':
                _logger.warning('Received event {} when print_uuid is None'.format(event))
                return None

            self.current_print_uuid = int(time.time())

        return {
            'octoprint_event': {
                'print_uuid': self.current_print_uuid,
                'event_type': event,
                'data': payload
                },
            'octoprint_data': plugin.octoprint_data()
            }

