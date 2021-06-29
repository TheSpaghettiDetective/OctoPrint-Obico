from typing import Optional
import time
import logging
import os
import uuid
import io
import json
import socket
from requests.exceptions import HTTPError

import octoprint.server
from octoprint.util.platform import (
    get_os as octoprint_get_os,
    OPERATING_SYSTEM_UNMAPPED
)

from .plugin_apis import verify_code
from .utils import ExpoBackoff, server_request, OctoPrintSettingsUpdater, get_tags, raise_for_status

_logger = logging.getLogger('octoprint.plugins.thespaghettidetective')

POLL_PERIOD_SECS = 5
DEADLINE_SECS = 3600
MAX_BACKOFF_SECS = 300


class PrinterDiscovery(object):

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
            hostname=os.uname()[1][:253],
            os=get_os()[:253],
            arch=os.uname()[4][:253],
            rpi_model=read('/proc/device-tree/model')[:253],
            octopi_version=read('/etc/octopi_version')[:253],
            port=get_port(self.plugin) or 80,
        )

        self.host_or_ip = None

    def get_tags(self):  # type: () -> dict
        return dict(device_id=self.device_id, **get_tags())

    def start(self):
        _logger.info('printer_discovery started, device_id: {}'.format(self.device_id))

        try:
            self._start()
        except Exception:
            self.stop()
            self.plugin.sentry.captureException(tags=self.get_tags())

        _logger.debug('printer_discovery quit')

    def _start(self):
        self.started_at = time.time()
        next_connect_at = 0.0  # -inf
        connect_attempts = 0

        while True:
            if self.stopped:
                break

            if self.plugin.is_configured():
                # if any token is set, let's stop
                break

            if time.time() - self.started_at > self.deadline_secs:
                _logger.info('printer_discovery deadline reached')
                self.stop()
                break

            if time.time() > next_connect_at:
                try:
                    self._call()
                    connect_attempts = 0
                    next_connect_at = time.time() + self.poll_period_secs
                except (IOError, OSError) as ex:
                    # tyring to catch only network related errors here,
                    # all other errors must bubble up.

                    # http4xx can be an actionable bug, let it bubble up
                    if isinstance(ex, HTTPError):
                        status_code = ex.response.status_code
                        if 400 <= status_code < 500:
                            raise

                    # issues with network / ssl / dns / server (http 5xx) ... those might go away
                    backoff_time = ExpoBackoff.get_delay(
                        connect_attempts, self.max_backoff_secs)
                    _logger.debug(
                        'printer_discovery error ({}), will retry after {}s'.format(
                            ex, backoff_time))

                    connect_attempts += 1
                    next_connect_at = time.time() + backoff_time

            time.sleep(2)

    def stop(self):
        self.stopped = True
        _logger.info('printer_discovery is stopping')

    def _call(self):
        _logger.debug('printer_discovery calls server')
        data = self._collect_device_info()

        resp = server_request(
            'POST',
            '/api/v1/octo/unlinked/',
            self.plugin,
            timeout=5,
            data=json.dumps(data),
            headers={'Content-Type': 'application/json'},
            raise_exception=True,
        )

        raise_for_status(resp, with_content=True)
        data = resp.json()
        for msg in data['messages']:
            self._process_message(msg)

    def _process_message(self, msg):
        _logger.info('printer_discovery incoming msg: {}'.format(msg))

        if msg['type'] == 'verify_code':
            # if any token is set, let's stop
            if self.plugin.is_configured():
                return

            if msg['device_id'] != self.device_id:
                _logger.debug('printer_discovery got message for different device_id')
                return

            code = msg['data']['code']
            result = verify_code(self.plugin, {'code': code})

            if result['succeeded'] is True:
                _logger.info('printer_discovery verified code succesfully')
            else:
                _logger.warn('printer_discovery could not verify code')
                self.plugin.sentry.captureMessage(
                    'printer_discovery could not verify code',
                    tags=self.get_tags(),
                    extra={'code': code})

            # stop after first verify attempt
            self.stop()
            return

    def _collect_device_info(self):
        info = dict(**self.static_info)
        info['printerprofile'] = get_printerprofile_name()[:253]

        if not self.host_or_ip:
            self.host_or_ip = get_host_or_ip(self.plugin)
        info['host_or_ip'] = self.host_or_ip

        info['machine_type'] = get_machine_type(self.plugin.octoprint_settings_updater)[:253]

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


def get_ip_addr():  # type () -> str
    primary_ip = ''
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.settimeout(2)
    try:
        s.connect(('10.255.255.255', 1))
        primary_ip = s.getsockname()[0]
        s.close()
    except Exception:
        try:
            s.connect(('8.8.8.8', 53))   # None of these 2 ways are 100%. Double them to maximize the chance
            primary_ip = s.getsockname()[0]
            s.close()
        except Exception:
            pass

    return primary_ip


def get_host_or_ip(plugin):
    try:
        discovery_settings = plugin._settings.global_get(['plugins', 'discovery'])
        return discovery_settings.get('publicHost', get_ip_addr())
    except Exception:
        return ''


def get_port(plugin):
    try:
        discovery_settings = plugin._settings.global_get(['plugins', 'discovery'])
        return discovery_settings.get('publicPort', plugin.octoprint_port)
    except Exception:
        return ''


def get_machine_type(
    octoprint_settings_updater
):  # type: (OctoPrintSettingsUpdater) -> str
    try:
        meta = octoprint_settings_updater.printer_metadata or {}
        return meta.get('MACHINE_TYPE', '')
    except Exception:
        return ''


def get_printerprofile_name():  # type: () -> str
    try:
        printerprofile = octoprint.server.printerProfileManager.get_current()
        return printerprofile.get('name', '') if printerprofile else ''
    except Exception:
        return ''
