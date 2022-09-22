import threading
from datetime import datetime

from . import alert_queue

class ErrorStats:

    def __init__(self):
        self._mutex = threading.RLock()
        self.stats = dict()

    def attempt(self, error_type):
        with self._mutex:
            stat = self.get_stat(error_type)
            stat['attempts'] += 1

    def add_connection_error(self, error_type, plugin):
        with self._mutex:
            stat = self.get_stat(error_type)
            stat['error_count'] += 1
            stat['last'] = datetime.utcnow()
            if not stat['first']:
                stat['first'] = datetime.utcnow()
        self.notify_client_if_needed_for_error(error_type, plugin)

    def notify_client_if_needed_for_error(self, error_type, plugin):
        with self._mutex:
            stat = self.get_stat(error_type)
            attempts = stat['attempts']
            error_count = stat['error_count']

            if attempts < 8:
                return

            if attempts < 10 and error_count < attempts * 0.5:
                return

            if error_count < attempts * 0.25:
                return

        post_to_server = False
        title = None
        if error_type == 'server':
            info_url = 'https://www.obico.io/docs/user-guides/troubleshoot-server-connection-issues/'
            text = 'Obico failed to connect to the server. Please make sure OctoPrint has a reliable internet connection.'

        if error_type == 'webcam':
            text = 'Obico plugin failed to connect to the webcam. Please go to "Settings" -> "Webcam & Timelapse" and make sure the stream URL and snapshot URL are set correctly.'
            info_url = 'https://www.obico.io/docs/user-guides/warnings/webcam-connection-error-popup/'
            title = 'Failed to Snapshot Webcam'
            post_to_server = True

        alert_queue.add_alert({
            'level': 'error',
            'cause': 'connect_' + error_type,
            'title': title,
            'text': text,
            'info_url': info_url,
            'buttons': ['more_info', 'diagnose', 'never', 'ok']
        }, plugin, post_to_server=post_to_server)

    def get_stat(self, error_type):
        with self._mutex:
            return self.stats.setdefault(error_type, dict(attempts=0, error_count=0, last=None, first=None))

    def as_dict(self):
        with self._mutex:
            return self.stats

# Poor-man's singleton
error_stats = ErrorStats()
