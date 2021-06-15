from typing import Optional
import time
import json
import logging
import os
import uuid

import octoprint.server
from websocket import WebSocketException

from .plugin_apis import verify_code
from .ws import WebSocketClient, WebSocketConnectionException
from .utils import ExpoBackoff

_logger = logging.getLogger('octoprint.plugins.thespaghettidetective')

DEADLINE_SECS = 1800
MAX_BACKOFF_SECS = 60


class LinkHelper(object):

    def __init__(self, plugin, deadline_secs=DEADLINE_SECS, max_backoff_secs=MAX_BACKOFF_SECS):
        self.plugin = plugin
        self.deadline_secs = deadline_secs  # type: int
        self.max_backoff_secs = max_backoff_secs  # type: int
        self.stopped = False
        self.started_at = None  # type: Optional[float]
        self.ws = None  # type: Optional[WebSocketClient]

        # device_id is different every time plugin starts
        self.device_id = uuid.uuid4().hex  # type: str

    def start(self) -> None:
        _logger.info(f'linkhelper started, device_id: {self.device_id}')
        self.started_at = time.time()

        url = self.plugin.canonical_ws_prefix() + '/ws/unlinked-dev/'

        next_reconnect_at = 0.0  # -inf
        reconnect_attempts = 0

        while True:
            if self.stopped:
                break

            if self.plugin.is_configured():
                # TODO if any token is set, let's never try to overwrite that for now
                # question: what to do when existing token is invalid?
                break

            if time.time() - self.started_at > self.deadline_secs:
                _logger.info('linkhelper deadline reached')
                self.stop()
                break

            if self.ws is None or not self.ws.connected():
                if time.time() > next_reconnect_at:
                    try:
                        self.ws = WebSocketClient(
                            f'{url}?device_id={self.device_id}',
                            on_ws_msg=self._process_server_msg,
                            wait_secs=5)
                        reconnect_attempts, next_reconnect_at = 0, 0.0
                    except (OSError, WebSocketException, WebSocketConnectionException) as ex:
                        reconnect_attempts += 1
                        backoff_time = ExpoBackoff.get_delay(reconnect_attempts, self.max_backoff_secs)
                        next_reconnect_at = time.time() + backoff_time
                        _logger.debug('linkhelper could not open ws connection ({}), will retry after {}s'.format(ex, backoff_time))

            time.sleep(1)
        _logger.info('linkhelper stopped')

    def stop(self):
        self.stopped = True
        if self.ws:
            try:
                self.ws.close()
            except (OSError, WebSocketException) as ex:
                _logger.debug('linkhelper could not close ws connection ({})'.format(ex))

    def _process_server_msg(self, ws, raw):
        _logger.debug("linkhelper incoming msg: {}".format(raw))
        try:
            (msg, ref, data) = json.loads(raw)
        except (TypeError, json.JSONDecodeError):
            _logger.exception("linkhelper could not decode msg")
            return

        if msg == 'query':
            info = self._collect_device_info()
            reply = json.dumps(('query_response', ref, info))
            _logger.debug("linkhelper query reply: {}".format(reply))
            self.ws.send(reply)
            return

        if msg == 'verify_code':
            # TODO if any token is set, let's never try to overwrite that for now
            # question: what to do when existing token is invalid?
            if self.plugin.is_configured():
                return

            if data["device_id"] != self.device_id:
                _logger.debug("linkhelper got message for different device_id")
                return

            result = verify_code(self.plugin, data)
            if result['succeeded'] is True:
                _logger.info('linkhelper verified code succesfully')
                self.stop()
            else:
                _logger.warn('linkhelper could not verify code')
            return

    def _process_ws_close(self, ws):
        if self.ws != ws:
            return

    def _collect_device_info(self):
        uname = os.uname()  # tuple in case of py2
        printerprofile = {}
        if octoprint.server.printerProfileManager:
            printerprofile = octoprint.server.printerProfileManager.get_current() or {}

        return {
            'printerprofile': printerprofile.get('name', ''),
            # 'sysname': uname[0],
            'hostname': uname[1],
            # 'release': uname[2],
            # 'version': uname[3],
            # 'machine': uname[4],
        }
