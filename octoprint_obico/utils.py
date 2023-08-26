# coding=utf-8
import time
import random
import logging
import sentry_sdk
from sentry_sdk.integrations.threading import ThreadingIntegration
from sentry_sdk.integrations.logging import LoggingIntegration
from sentry_sdk.integrations.flask import FlaskIntegration
import re
import os
import platform
from sarge import run, Capture
import tempfile
from io import BytesIO
import struct
import threading
import socket
from contextlib import closing
import backoff
import octoprint
import requests

from .lib.error_stats import error_stats
from .lib import curlify

PRINTER_SETTINGS_UPDATE_INTERVAL = 60*30.0  # Update printer settings at max 30 minutes interval, as they are relatively static.

_logger = logging.getLogger('octoprint.plugins.obico')


class ExpoBackoff:

    def __init__(self, max_seconds, max_attempts=0):
        self.attempts = 0
        self.max_seconds = max_seconds
        self.max_attempts = max_attempts

    def reset(self):
        self.attempts = 0

    def more(self, e):
        self.attempts += 1
        if self.max_attempts > 0 and self.attempts > self.max_attempts:
            _logger.error('Giving up after %d attempts on error: %s' % (self.attempts, e))
            raise e
        else:
            delay = 2 ** (self.attempts-3)
            if delay > self.max_seconds:
                delay = self.max_seconds
            delay *= 0.5 + random.random()
            _logger.error('Attempt %d - backing off %f seconds: %s' % (self.attempts, delay, e))

            time.sleep(delay)


class OctoPrintSettingsUpdater:

    def __init__(self, plugin):
        self._mutex = threading.RLock()
        self.plugin = plugin
        self.last_asked = 0    # The timestamp when any caller asked for the setting data, presumably to send to the server.
        self.printer_metadata = None

    def update_settings(self):
        with self._mutex:
            self.last_asked = 0  # Settings changed. Reset last_asked so that the next call will guarantee to be sent to the server

    def update_firmware(self, payload):
        with self._mutex:
            self.printer_metadata = payload['data']
            self.last_asked = 0  # Firmware changed. Reset last_asked so that the next call will guarantee to be sent to the server

    def as_dict(self):
        with self._mutex:
            if self.last_asked > time.time() - PRINTER_SETTINGS_UPDATE_INTERVAL:
                return None

        webcam_dict = dict((k, v) for k, v in octoprint_webcam_settings(self.plugin._settings).items() if k in ('flipV', 'flipH', 'rotate90', 'streamRatio'))
        webcam_dict['rotation'] = 0
        if 'rotate90' in webcam_dict:
            webcam_dict['rotation'] = 270 if webcam_dict['rotate90'] else 0 # 270 = 90 degrees counterclockwise
            del webcam_dict['rotate90']

        data = dict(
            webcam=webcam_dict,
            temperature=self.plugin._settings.settings.effective.get('temperature', {}),
            agent=dict(name='octoprint_obico', version=self.plugin._plugin_version),
            octoprint_version=octoprint.util.version.get_octoprint_version_string(),
            platform_uname=list(platform.uname()),
            installed_plugins=[p.key for p in list(self.plugin._plugin_manager.enabled_plugins.values()) if not p.bundled],
        )
        if self.printer_metadata:
            data['printer_metadata'] = self.printer_metadata

        try:
            with open('/proc/device-tree/model', 'r') as file:
                model = file.read().strip()
            data['platform_uname'].append(model)
        except:
            data['platform_uname'].append('')

        with self._mutex:
            self.last_asked = time.time()

        return data


class SentryWrapper:

    def __init__(self, plugin):
        self.plugin = plugin
        self._enabled = plugin._settings.get(["sentry_opt"]) != 'out' \
            and ( plugin.canonical_endpoint_prefix() is None or plugin.canonical_endpoint_prefix().endswith('app.obico.io') )

        if not self._enabled:
            return

        # https://github.com/getsentry/sentry-python/issues/149
        def before_send(event, hint):
            if 'exc_info' in hint:
                exc_type, exc_value, tb = hint['exc_info']
                errors_to_ignore = (requests.exceptions.RequestException,)
                if isinstance(exc_value, errors_to_ignore):
                    return None
            return event


        sentry_sdk.init(
            dsn='https://f0356e1461124e69909600a64c361b71@sentry.obico.io/4',
            default_integrations=False,
            integrations=[
                ThreadingIntegration(propagate_hub=True), # Make sure context are propagated to sub-threads.
                LoggingIntegration(
                    level=logging.INFO, # Capture info and above as breadcrumbs
                    event_level=None  # Send logs as events above a logging level, disabled it
                ),
                FlaskIntegration(),
            ],
            before_send=before_send,

            # If you wish to associate users to errors (assuming you are using
            # django.contrib.auth) you may enable sending PII data.
            send_default_pii=True,

            release='octoprint-obico@'+plugin._plugin_version,
        )

    def enabled(self):
        return self._enabled

    def init_context(self):
        if self.enabled():
            sentry_sdk.set_user({'id': self.plugin.auth_token()})
            for (k, v) in self.get_tags().items():
                sentry_sdk.set_tag(k, v)

    def captureException(self, *args, **kwargs):
        _logger.exception("Exception")
        if self.enabled():
            sentry_sdk.capture_exception(*args, **kwargs)

    def captureMessage(self, *args, **kwargs):
        if self.enabled():
            sentry_sdk.capture_message(*args, **kwargs)

    def get_tags(self):
        (os, _, ver, _, arch, _) = platform.uname()
        tags = dict(os=os, os_ver=ver, arch=arch)
        try:
            distro = run("cat /etc/os-release | grep PRETTY_NAME | sed s/PRETTY_NAME=//", stdout=Capture())
            distro_out = ''.join(distro.stdout.text).replace('"', '').replace('\n', '')
            if distro_out:
                tags['distro'] = distro_out
        except:
            pass

        try:
            long_bit = run("getconf LONG_BIT", stdout=Capture())
            long_bit_out = ''.join(long_bit.stdout.text).replace('\n', '')
            if long_bit_out:
                tags['long_bit'] = long_bit_out
        except:
            pass

        return tags


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


def get_image_info(data):
    data_bytes = data
    if not isinstance(data, str):
        data = data.decode('iso-8859-1')
    size = len(data)
    height = -1
    width = -1
    content_type = ''

    # handle GIFs
    if (size >= 10) and data[:6] in ('GIF87a', 'GIF89a'):
        # Check to see if content_type is correct
        content_type = 'image/gif'
        w, h = struct.unpack("<HH", data[6:10])
        width = int(w)
        height = int(h)

    # See PNG 2. Edition spec (http://www.w3.org/TR/PNG/)
    # Bytes 0-7 are below, 4-byte chunk length, then 'IHDR'
    # and finally the 4-byte width, height
    elif ((size >= 24) and data.startswith('\211PNG\r\n\032\n')
          and (data[12:16] == 'IHDR')):
        content_type = 'image/png'
        w, h = struct.unpack(">LL", data[16:24])
        width = int(w)
        height = int(h)

    # Maybe this is for an older PNG version.
    elif (size >= 16) and data.startswith('\211PNG\r\n\032\n'):
        # Check to see if we have the right content type
        content_type = 'image/png'
        w, h = struct.unpack(">LL", data[8:16])
        width = int(w)
        height = int(h)

    # handle JPEGs
    elif (size >= 2) and data.startswith('\377\330'):
        content_type = 'image/jpeg'
        jpeg = BytesIO(data_bytes)
        jpeg.read(2)
        b = jpeg.read(1)
        try:
            while (b and ord(b) != 0xDA):
                while (ord(b) != 0xFF):
                    b = jpeg.read(1)
                while (ord(b) == 0xFF):
                    b = jpeg.read(1)
                if (ord(b) >= 0xC0 and ord(b) <= 0xC3):
                    jpeg.read(3)
                    h, w = struct.unpack(">HH", jpeg.read(4))
                    break
                else:
                    jpeg.read(int(struct.unpack(">H", jpeg.read(2))[0])-2)
                b = jpeg.read(1)
            width = int(w)
            height = int(h)
        except struct.error:
            pass
        except ValueError:
            pass

    return content_type, width, height


def is_port_open(host, port):
    _logger.debug(f'Testing TCP port {port} on {host}')
    with closing(socket.socket(socket.AF_INET, socket.SOCK_STREAM)) as sock:
        return sock.connect_ex((host, port)) == 0


@backoff.on_exception(backoff.expo, Exception, max_tries=3, jitter=None)
@backoff.on_predicate(backoff.expo, max_tries=3, jitter=None)
def wait_for_port(host, port):
    return is_port_open(host, port)


def wait_for_port_to_close(host, port):
    for i in range(10):   # Wait for up to 5s
        with closing(socket.socket(socket.AF_INET, socket.SOCK_STREAM)) as sock:
            if sock.connect_ex((host, port)) != 0:  # Port is not open
                return
            time.sleep(0.5)


def server_request(method, uri, plugin, timeout=30, raise_exception=False, skip_debug_logging=False, **kwargs):
    '''
    Return: A requests response object if it reaches the server. Otherwise None. Connections errors are printed to console but NOT raised
    '''

    endpoint = plugin.canonical_endpoint_prefix() + uri
    try:
        error_stats.attempt('server')
        resp = requests.request(method, endpoint, timeout=timeout, **kwargs)

        if not skip_debug_logging:
            _logger.debug(curlify.to_curl(resp.request))

        if not resp.ok and not resp.status_code == 401:
            error_stats.add_connection_error('server', plugin)

        return resp
    except Exception:
        error_stats.add_connection_error('server', plugin)
        _logger.exception("{}: {}".format(method, endpoint))
        if raise_exception:
            raise


def raise_for_status(resp, with_content=False, **kwargs):
    # puts reponse content into exception
    if with_content:
        try:
            resp.raise_for_status()
        except Exception as exc:
            args = exc.args
            if not args:
                arg0 = ''
            else:
                arg0 = args[0]
            arg0 = "{} {}".format(arg0, resp.text)
            exc.args = (arg0, ) + args[1:]
            exc.kwargs = kwargs

            raise
    resp.raise_for_status()

# TODO: remove once all TSD users have migrated
def migrate_tsd_settings(plugin):
    if plugin.is_configured():
        return
    if plugin._settings.get(['tsd_migrated']):
        return
    tsd_settings = plugin._settings.settings.get(['plugins', ]).get('thespaghettidetective')
    if tsd_settings:
        for k in tsd_settings.keys():
            if k == 'endpoint_prefix' and tsd_settings.get(k) == 'https://app.thespaghettidetective.com':
                continue
            plugin._settings.set([k],tsd_settings.get(k), force=True)

        plugin._settings.set(["tsd_migrated"], 'yes', force=True)
        plugin._settings.save(force=True)

# Provide compatibility for OctoPrint 1.9+ and the older versions
def octoprint_webcam_settings(octoprint_settings):
    return octoprint_settings.global_get(["plugins", "classicwebcam"]) or octoprint_settings.global_get(["webcam"]) or {}


def run_in_thread(long_running_func, *args, **kwargs):
    daemon_thread = threading.Thread(target=long_running_func,  args=args, kwargs=kwargs)
    daemon_thread.daemon = True  # Setting the thread as daemon
    daemon_thread.start()
    return daemon_thread