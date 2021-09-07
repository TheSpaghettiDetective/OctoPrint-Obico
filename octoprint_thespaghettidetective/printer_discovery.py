from typing import Optional
import time
import logging
import os
import uuid
import io
import json
import socket
from requests.exceptions import HTTPError
import random
import string
import flask

try:
    from secrets import token_hex
except ImportError:
    def token_hex(n):
        letters = string.ascii_letters + string.digits
        return "".join([random.choice(letters) for i in range(n)])

import octoprint.server
from octoprint.util.net import is_lan_address
from octoprint.util.platform import (
    get_os as octoprint_get_os,
    OPERATING_SYSTEM_UNMAPPED
)

from .plugin_apis import verify_code
from .utils import (
    ExpoBackoff, server_request, OctoPrintSettingsUpdater,
    get_tags, raise_for_status)

_logger = logging.getLogger('octoprint.plugins.thespaghettidetective')

POLL_PERIOD = 5
DEADLINE = 600
MAX_BACKOFF_SECS = 30


class PrinterDiscovery(object):

    def __init__(self,
                 plugin,
                 poll_period=POLL_PERIOD,
                 deadline=DEADLINE,
                 max_backoff_secs=MAX_BACKOFF_SECS
                 ):
        self.plugin = plugin
        self.poll_period = poll_period  # type: int
        self.deadline = deadline  # type: int
        self.max_backoff_secs = max_backoff_secs  # type: int
        self.stopped = False
        self.cur_step = None  # type: Optional[int]
        self.device_secret = None
        self.static_info = {}

        # device_id is different every time plugin starts
        self.device_id = uuid.uuid4().hex  # type: str

    def start_and_block(self):
        _logger.info(
            'printer_discovery started, device_id: {}'.format(self.device_id))

        try:
            self._start()
        except Exception:
            self.stop()
            self.plugin.sentry.captureException(tags=get_tags())

        _logger.debug('printer_discovery quits')

    def _start(self):
        self.device_secret = token_hex(32)
        self.cur_step = 0
        next_connect_at = 0
        connect_attempts = 0

        host_or_ip = get_local_ip_or_host()

        self.static_info = dict(
            device_id=self.device_id,
            hostname=os.uname()[1][:253],
            host_or_ip=host_or_ip,
            port=get_port(self.plugin),
            os=get_os()[:253],
            arch=os.uname()[4][:253],
            rpi_model=read('/proc/device-tree/model')[:253],
            octopi_version=read('/etc/octopi_version')[:253],
            plugin_version=self.plugin._plugin_version,
        )

        if not host_or_ip:
            _logger.info('printer_discovery could not find out local ip')
            self.stop()
            return

        while not self.stopped:

            if self.plugin.is_configured():
                _logger.info('printer_discovery detected a configuration')
                self.stop()
                break

            if self.cur_step > self.deadline:
                _logger.info('printer_discovery got deadline reached')
                self.stop()
                break

            if self.cur_step >= next_connect_at:
                try:
                    self._call()
                    connect_attempts = 0
                    next_connect_at = self.cur_step + self.poll_period
                except (IOError, OSError) as ex:
                    # tyring to catch only network related errors here,
                    # all other errors must bubble up.

                    # http4xx can be an actionable bug, let it bubble up
                    if isinstance(ex, HTTPError):
                        status_code = ex.response.status_code
                        if 400 <= status_code < 500:
                            raise

                    # issues with network / ssl / dns / server (http 5xx)
                    # ... those might go away
                    backoff_time = ExpoBackoff.get_delay(
                        connect_attempts, self.max_backoff_secs)
                    _logger.debug(
                        'printer_discovery got an error ({}), '
                        'will retry after {}s'.format(
                            ex, backoff_time))

                    connect_attempts += 1
                    next_connect_at = self.cur_step + max(1, int(backoff_time))

            self.cur_step += 1
            time.sleep(1)

    def stop(self):
        self.stopped = True
        _logger.info('printer_discovery is stopping')

    def id_for_secret(self):

        def get_remote_address(request):
            forwardedFor = request.headers.get('X-Forwarded-For')
            if forwardedFor:
                return forwardedFor.split(',')[0]
            return request.remote_addr

        if self.device_secret \
            and is_lan_address(get_remote_address(flask.request)) \
            and flask.request.args.get('device_id') == self.device_id:

            accept = flask.request.headers.get('Accept', '')
            if 'application/json' in accept:
                resp = flask.Response(
                    json.dumps(
                        {'device_secret': self.device_secret}
                    ),
                    mimetype='application/json'
                )
            else:
                resp = flask.Response(
                    flask.render_template(
                        'thespaghettidetective_discovery.jinja2',
                        device_secret=self.device_secret
                    )
                )
            resp.headers['Access-Control-Allow-Origin'] = '*'
            resp.headers['Access-Control-Allow-Methods'] =\
                'GET, HEAD, OPTIONS'
            return resp

        return flask.abort(403)

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
        # Stops after first verify attempt
        _logger.info('printer_discovery got incoming msg: {}'.format(msg))

        if msg['type'] == 'verify_code':
            # if any token is set, let's stop
            if self.plugin.is_configured():
                self.stop()
                return

            if (
                not self.device_secret or
                'secret' not in msg['data'] or
                msg['data']['secret'] != self.device_secret
            ):
                _logger.error('printer_discovery got unmatching secret')
                self.plugin.sentry.captureMessage(
                    'printer_discovery got unmatching secret',
                    tags=get_tags(),
                    extra={'secret': self.device_secret, 'msg': msg}
                )
                self.stop()
                return

            if msg['device_id'] != self.device_id:
                _logger.error('printer_discovery got unmatching device_id')
                self.plugin.sentry.captureMessage(
                    'printer_discovery got unmatching device_id',
                    tags=get_tags(),
                    extra={'device_id': self.device_id, 'msg': msg}
                )
                self.stop()
                return

            code = msg['data']['code']
            result = verify_code(self.plugin, {'code': code})

            if result['succeeded'] is True:
                _logger.info('printer_discovery verified code succesfully')
            else:
                _logger.error('printer_discovery could not verify code')
                self.plugin.sentry.captureMessage(
                    'printer_discovery could not verify code',
                    tags=get_tags(),
                    extra={'code': code})

            self.stop()
            return

        _logger.error('printer_discovery got unexpected message')
        self.plugin.sentry.captureMessage(
            'printer_discovery got unexpected message',
            tags=get_tags(),
            extra={'msg': msg}
        )

    def _collect_device_info(self):
        info = dict(**self.static_info)
        info['printerprofile'] = get_printerprofile_name()[:253]
        info['machine_type'] = get_machine_type(
            self.plugin.octoprint_settings_updater)[:253]
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


def _get_ip_addr():  # type () -> str
    primary_ip = ''
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.settimeout(2)
    try:
        s.connect(('10.255.255.255', 1))
        primary_ip = s.getsockname()[0]
        s.close()
    except Exception:
        try:
            # None of these 2 ways are 100%. Double them to maximize the chance
            s.connect(('8.8.8.8', 53))
            primary_ip = s.getsockname()[0]
            s.close()
        except Exception:
            pass

    return primary_ip


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


def get_port(plugin):
    try:
        discovery_settings = plugin._settings.global_get(
            ['plugins', 'discovery'])
        return discovery_settings.get('publicPort') or plugin.octoprint_port
    except Exception:
        return ''


def get_local_ip_or_host():  # type () -> str
    ip = _get_ip_addr()
    if ip and is_lan_address(ip):
        return ip

    addresses = list(set([
        addr
        for addr in octoprint.util.interface_addresses()
        if is_lan_address(addr)
    ]))

    if addresses:
        return addresses[0]

    hostname = os.uname()[1][:253]
    if '.' in hostname:
        return hostname
    # let's try...
    return hostname + '.local'
