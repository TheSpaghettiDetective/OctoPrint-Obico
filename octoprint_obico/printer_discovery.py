import time
import logging
import platform
import uuid
import io
import json
import socket
from requests.exceptions import HTTPError
import random
import string
import flask
import octoprint
import ipaddress

try:
    from secrets import token_hex
except ImportError:
    def token_hex(n):
        letters = string.ascii_letters + string.digits
        return "".join([random.choice(letters) for i in range(n)])

from octoprint.util.net import sanitize_address
from octoprint.util.platform import (
    get_os as octoprint_get_os,
    OPERATING_SYSTEM_UNMAPPED
)

from .plugin_apis import verify_code
from .utils import (
    server_request, OctoPrintSettingsUpdater,
    raise_for_status)

_logger = logging.getLogger('octoprint.plugins.obico')

# we count steps instead of tracking timestamps;
# timestamps happened to be unreliable on rpi-s (NTP issue?)
# printer remains discoverable for about 10 minutes, give or take.
POLL_PERIOD = 5
MAX_POLLS = 120
TOTAL_STEPS = POLL_PERIOD * MAX_POLLS

MAX_BACKOFF_SECS = 30


class PrinterDiscovery(object):

    def __init__(self,
                 plugin,
                 ):
        self.plugin = plugin
        self.stopped = False
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
            self.plugin.sentry.captureException()

        _logger.debug('printer_discovery quits')

    def _start(self):
        self.device_secret = token_hex(32)
        steps_remaining = TOTAL_STEPS

        host_or_ip = get_local_ip(self.plugin)

        self.static_info = dict(
            device_id=self.device_id,
            hostname=platform.uname()[1][:253],
            host_or_ip=host_or_ip,
            port=get_port(self.plugin),
            os=get_os()[:253],
            arch=platform.uname()[4][:253],
            rpi_model=read('/proc/device-tree/model')[:253],
            octopi_version=read('/etc/octopi_version')[:253],
            plugin_version=self.plugin._plugin_version,
            agent='Obico for OctoPrint',
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

            steps_remaining -= 1
            if steps_remaining < 0:
                _logger.info('printer_discovery got deadline reached')
                self.stop()
                break

            try:
                if steps_remaining % POLL_PERIOD == 0:
                    self._call()
            except (IOError, OSError) as ex:
                # tyring to catch only network related errors here,
                # all other errors must bubble up.

                # http4xx can be an actionable bug, let it bubble up
                if isinstance(ex, HTTPError):
                    status_code = ex.response.status_code
                    if 400 <= status_code < 500:
                        raise

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

        if (
            self.device_secret and
            is_local_address(
                self.plugin,
                get_remote_address(flask.request)
            ) and
            flask.request.args.get('device_id') == self.device_id
        ):
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
                        'obico_discovery.jinja2',
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
                    extra={'secret': self.device_secret, 'msg': msg}
                )
                self.stop()
                return

            if msg['device_id'] != self.device_id:
                _logger.error('printer_discovery got unmatching device_id')
                self.plugin.sentry.captureMessage(
                    'printer_discovery got unmatching device_id',
                    extra={'device_id': self.device_id, 'msg': msg}
                )
                self.stop()
                return

            code = msg['data']['code']
            result = verify_code(self.plugin, {'code': code})

            if result['succeeded'] is True:
                _logger.info('printer_discovery verified code succesfully')
                self.plugin._plugin_manager.send_plugin_message(
                    self.plugin._identifier, {'printer_autolinked': True})
            else:
                _logger.error('printer_discovery could not verify code')
                self.plugin.sentry.captureMessage(
                    'printer_discovery could not verify code',
                    extra={'code': code})

            self.stop()
            return

        _logger.error('printer_discovery got unexpected message')
        self.plugin.sentry.captureMessage(
            'printer_discovery got unexpected message',
            extra={'msg': msg}
        )

    def _collect_device_info(self):
        info = dict(**self.static_info)
        info['printerprofile'] = self.plugin._printer_profile_manager.get_current_or_default().get('name', '')[:253]
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


def get_port(plugin):
    try:
        discovery_settings = plugin._settings.global_get(
            ['plugins', 'discovery'])
        public_port = discovery_settings.get(
            'publicPort') if discovery_settings else ''
    except Exception:
        public_port = ''

    return public_port or plugin.octoprint_port


def get_local_ip(plugin):
    ip = _get_ip_addr()
    if ip and is_local_address(plugin, ip):
        return ip

    addresses = list(set([
        addr
        for addr in octoprint.util.interface_addresses()
        if is_local_address(plugin, addr)
    ]))

    if addresses:
        return addresses[0]

    return ''


def is_local_address(plugin, address):
    try:
        address = sanitize_address(address)
        ip = ipaddress.ip_address(address)
        return ip.is_private or ip.is_loopback
    except Exception as exc:
        _logger.error(
            'could not determine whether {} is local address ({})'.format(
                address, exc)
        )
        plugin.sentry.captureException()
        return False
