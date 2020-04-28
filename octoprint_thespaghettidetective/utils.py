# coding=utf-8
from datetime import datetime
import time
import random
import logging
import raven
import re
import os
import platform
from sarge import run, Capture
import tempfile

CAM_EXCLUSIVE_USE = os.path.join(tempfile.gettempdir(), '.using_picam')

_logger = logging.getLogger('octoprint.plugins.thespaghettidetective')

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

    def as_dict(self):
        return self.errors

class SentryWrapper:

    def __init__(self, plugin):
        self.sentryClient = raven.Client(
            'https://f0356e1461124e69909600a64c361b71:bdf215f6e71b48dc90d28fb89a4f8238@sentry.thespaghettidetective.com/4?verify_ssl=0',
            release=plugin._plugin_version
            )
        self._settings = plugin._settings

    def captureException(self, *args, **kwargs):
        _logger.exception()
        if self._settings.get(["sentry_opt"]) == 'out':
            self.sentryClient.captureException(*args, **kwargs)

    def user_contex(self, *args, **kwargs):
        if self._settings.get(["sentry_opt"]) == 'out':
            self.sentryClient.user_contex(*args, **kwargs)

    def captureMessage(self, *args, **kwargs):
        if self._settings.get(["sentry_opt"]) == 'out':
            self.sentryClient.captureMessage(*args, **kwargs)


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

system_tags = None

def get_tags():
    global system_tags
    if system_tags:
        return system_tags

    (os, _, ver, _, arch, _) = platform.uname()
    tags = dict(os=os, os_ver=ver, arch=arch)
    try:
        v4l2 = run('v4l2-ctl --list-devices',stdout=Capture())
        v4l2_out = ''.join(re.compile(r"^([^\t]+)", re.MULTILINE).findall(v4l2.stdout.text)).replace('\n', '')
        if v4l2_out:
            tags['v4l2'] = v4l2_out
    except:
        pass

    try:
        usb = run("lsusb | cut -d ' ' -f 7- | grep -vE ' hub| Hub' | grep -v 'Standard Microsystems Corp'",stdout=Capture())
        usb_out = ''.join(usb.stdout.text).replace('\n', '')
        if usb_out:
            tags['usb'] = usb_out
    except:
        pass

    system_tags = tags
    return system_tags

def not_using_pi_camera():
    try:
        os.remove(CAM_EXCLUSIVE_USE)
    except:
        pass

def using_pi_camera():
    open(CAM_EXCLUSIVE_USE, 'a').close()  # touch CAM_EXCLUSIVE_USE to indicate the intention of exclusive use of pi camera
