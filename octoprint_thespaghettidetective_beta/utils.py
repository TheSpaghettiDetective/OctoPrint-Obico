# coding=utf-8
from datetime import datetime
import time
import random
import logging
import re

_logger = logging.getLogger(__name__)

class ExpoBackoff:

    def __init__(self, max_seconds):
        self.attempts = -3
        self.max_seconds = max_seconds

    def reset(self):
        self.attempts = -3

    def more(self, e):
        self.attempts += 1
        delay = 2 ** self.attempts
        if delay > self.max_seconds:
            delay = self.max_seconds
        delay *= 0.5 + random.random()

        _logger.error('Backing off %f seconds: %s' % (delay, e))

        time.sleep(delay)


class ConnectionErrorTracker:

    def __init__(self, plugin):
        self.plugin = plugin
        self.attempts = dict()
        self.errors = dict()

    def attempt(self, error_type):
        attempts = self.attempts.get(error_type, 0)
        self.attempts[error_type] = attempts + 1

    def add_connection_error(self, error_type):
        existing = self.errors.get(error_type, [])
        self.errors[error_type] = existing + [datetime.utcnow()]
        self.notify_client_if_needed_for_error(error_type)

    def notify_client_if_needed_for_error(self, error_type):
        attempts = self.attempts.get(error_type, 0)
        errors = self.errors.get(error_type, [])

        if attempts < 8:
            return

        if attempts < 10 and len(errors) < attempts * 0.5:
            return

        if len(errors) < attempts * 0.25:
            return

        self.plugin._plugin_manager.send_plugin_message(self.plugin._identifier, {'new_error': error_type})

    def notify_client_if_needed(self):
        for k in self.errors:
            self.notify_client_if_needed_for_error(k)

    def as_dict(self):
        return self.errors

def pi_version():
    try:
        with open('/sys/firmware/devicetree/base/model', 'r') as firmware_model:
            model = re.search('Raspberry Pi(.*)', firmware_model.read()).group(1)
            if model:
                return "0" if re.search('Zero', model, re.IGNORECASE) else "3"
            else:
                return None
    except:
         return None
