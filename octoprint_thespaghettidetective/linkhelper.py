from typing import Optional
import time
import logging
import os
import uuid
import io
import json

import octoprint.server
from octoprint.util.platform import (
    get_os as octoprint_get_os,
    OPERATING_SYSTEM_UNMAPPED
)

from .plugin_apis import verify_code
from .utils import ExpoBackoff, server_request

_logger = logging.getLogger('octoprint.plugins.thespaghettidetective')

POLL_PERIOD_SECS = 5
DEADLINE_SECS = 1800
MAX_BACKOFF_SECS = 300


class LinkHelper(object):

    def __init__(self,
                 plugin,
                 poll_period_secs=POLL_PERIOD_SECS,
                 deadline_secs=DEADLINE_SECS,
                 max_backoff_secs=MAX_BACKOFF_SECS
                 ):
        self.plugin = plugin
        self.poll_period_secs = poll_period_secs  # type: int
        self.deadline_secs = deadline_secs  # type: int
        self.max_backoff_secs = max_backoff_secs  # type: int
        self.stopped = False
        self.started_at = None  # type: Optional[float]

        # device_id is different every time plugin starts
        self.device_id = uuid.uuid4().hex  # type: str
        self.static_info = dict(
            device_id=self.device_id,
            hostname=os.uname()[1],
            os=get_os(),
            arch=os.uname()[4],
            rpi_model=read('/proc/device-tree/model'),
            octopi_version=read('/etc/octopi_version'),
        )

    def start(self):
        _logger.info('linkhelper started, device_id: {}'.format(self.device_id))

        self.started_at = time.time()
        next_connect_at = 0.0  # -inf
        connect_attempts = 0

        while True:
            if self.stopped:
                break

            if self.plugin.is_configured():
                # TODO if any token is set, let's never try to overwrite that
                # for now.
                # Question: what to do when existing token is invalid?
                break

            if time.time() - self.started_at > self.deadline_secs:
                _logger.info('linkhelper deadline reached')
                self.stop()
                break

            if time.time() > next_connect_at:
                try:
                    self._call()
                    connect_attempts = 0
                    next_connect_at = time.time() + self.poll_period_secs
                except Exception as ex:
                    backoff_time = ExpoBackoff.get_delay(
                        connect_attempts, self.max_backoff_secs)
                    _logger.debug(
                        'linkhelper error ({}), will retry after {}s'.format(
                            ex, backoff_time))

                    connect_attempts += 1
                    next_connect_at = time.time() + backoff_time

            time.sleep(2)
        _logger.info('linkhelper stopped')

    def stop(self):
        self.stopped = True

    def _call(self):
        _logger.debug('linkhelper calls server')
        data = self._collect_device_info()

        resp = server_request(
            'POST',
            '/api/v1/octo/unlinked/',
            self.plugin,
            timeout=5,
            data=json.dumps(data),
            headers={'Content-Type': 'application/json'}
        )

        if resp is None:
            raise Exception('network error')

        resp.raise_for_status()
        data = resp.json()
        for msg in data['messages']:
            self._process_message(msg)

    def _process_message(self, msg):
        _logger.info('linkhelper incoming msg: {}'.format(msg))

        if msg['type'] == 'verify_code':
            # TODO if any token is set, let's never try to overwrite that
            # for now.
            # Question: what to do when existing token is invalid?
            if self.plugin.is_configured():
                return

            if msg['device_id'] != self.device_id:
                _logger.debug('linkhelper got message for different device_id')
                return

            result = verify_code(self.plugin, msg['data'])
            if result['succeeded'] is True:
                _logger.info('linkhelper verified code succesfully')
                self.stop()
            else:
                _logger.warn('linkhelper could not verify code')
            return

    def _collect_device_info(self):
        info = dict(**self.static_info)
        if hasattr(octoprint.server, 'printerProfileManager'):
            printerprofile = octoprint.server.printerProfileManager.get_current()
            info['printerprofile'] = printerprofile.get('name', '') if printerprofile else ''
        return info


def get_os():  # type: () -> str
    os_name = octoprint_get_os()
    if os_name == OPERATING_SYSTEM_UNMAPPED:
        os_name = ''
    return os_name or ''


def read(path):  # type: (str) -> str
    try:
        with io.open(path, 'rt', encoding='utf8') as f:
            return f.readline().strip('\0').strip()
    except Exception:
        return ''
