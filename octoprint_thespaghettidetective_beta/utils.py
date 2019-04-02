# coding=utf-8
from datetime import datetime
import time
import random
import logging

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

    def __init__(self):
        self.errors = dict()

    def add_connection_error(self, error_type):
        existing = self.errors.get(error_type, [])
        self.errors[error_type] = existing + [datetime.utcnow()]

    def as_dict(self):
        return self.errors
