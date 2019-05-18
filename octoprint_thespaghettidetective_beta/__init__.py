# coding=utf-8
from __future__ import absolute_import
import logging
import threading
import flask
import json
import re
import os, sys, time
import requests
import raven

from .webcam_capture import capture_jpeg
from .ws import ServerSocket
from .commander import Commander
from .utils import ExpoBackoff, ConnectionErrorTracker
from .print_event import PrintEventTracker

### (Don't forget to remove me)
# This is a basic skeleton for your plugin's __init__.py. You probably want to adjust the class name of your plugin
# as well as the plugin mixins it's subclassing from. This is really just a basic skeleton to get you started,
# defining your plugin as a template plugin, settings and asset plugin. Feel free to add or remove mixins
# as necessary.
#
# Take a look at the documentation on what other plugin mixins are available.

import octoprint.plugin

_logger = logging.getLogger('octoprint.plugins.thespaghettidetective_beta')

POST_PIC_INTERVAL_SECONDS = 50.0
POST_STATUS_INTERVAL_SECONDS = 15.0

if os.environ.get('DEBUG'):
    POST_PIC_INTERVAL_SECONDS = 5.0
    POST_STATUS_INTERVAL_SECONDS = 15.0

class TheSpaghettiDetectivePlugin(
            octoprint.plugin.SettingsPlugin,
            octoprint.plugin.StartupPlugin,
            octoprint.plugin.EventHandlerPlugin,
            octoprint.plugin.AssetPlugin,
            octoprint.plugin.SimpleApiPlugin,
            octoprint.plugin.WizardPlugin,
            octoprint.plugin.TemplatePlugin,):

    def __init__(self):
        self.ss = None
        self.last_pic = 0
        self.last_status = 0
        self.commander = Commander()
        self.error_tracker = ConnectionErrorTracker(self)
        self.print_event_tracker = PrintEventTracker()


	##~~ Wizard plugin mix

    def is_wizard_required(self):
        alpha_settings = self._settings.effective.get('plugins', {}).get('thespaghettidetective')
        if alpha_settings:  # Alpha testers
            alpha_migrated = os.path.join(self.get_plugin_data_folder(), '.alpah_migrated')
            if not os.path.isfile(alpha_migrated):
                with open(alpha_migrated, 'a'):  # touch alpha_migrated
                    pass
                if alpha_settings.get('auth_token'):
                    self._settings.set(["auth_token"],alpha_settings.get('auth_token'), force=True)
                if alpha_settings.get('endpoint_prefix'):
                    self._settings.set(["endpoint_prefix"],alpha_settings.get('endpoint_prefix'), force=True)
                self._settings.save(force=True)

        return not self._settings.get(["auth_token"])

    def get_wizard_version(self):
        return 1

    ##~~ SettingsPlugin mixin

    def get_settings_defaults(self):
        # Initialize sentry the first opportunity when `self._plugin_version` is available. Is there a better place for it?
        self.sentry = raven.Client(
            'https://45064d46913d4a9e98e7155ecb18321c:054f538fa0b64ee88af283639b415e24@sentry.getanywhere.io/3?verify_ssl=0',
            release=self._plugin_version
            )

        return dict(
            endpoint_prefix='https://app.thespaghettidetective.com'
        )

    ##~~ AssetPlugin mixin

    def get_assets(self):
        # Define your plugin's asset files to automatically include in the
        # core UI here.
        return dict(
            js=["js/TheSpaghettiDetective.js"],
            css=["css/TheSpaghettiDetective.css"],
            less=["less/TheSpaghettiDetective.less"]
        )

    ##~~ Softwareupdate hook

    def get_update_information(self):
        # Define the configuration for your plugin to use with the Software Update
        # Plugin here. See https://github.com/foosel/OctoPrint/wiki/Plugin:-Software-Update
        # for details.
        return dict(
            TheSpaghettiDetectiveBeta=dict(
                displayName="TheSpaghettiDetective Plugin (Beta)",
                displayVersion=self._plugin_version,

                # version check: github repository
                type="github_release",
                user="TheSpaghettiDetective",
                repo="OctoPrint-TheSpaghettiDetective",
                current=self._plugin_version,

                # update method: pip
                pip="https://github.com/TheSpaghettiDetective/OctoPrint-TheSpaghettiDetective/archive/{target_version}.zip"
            )
        )

    ##~~ plugin APIs

    def get_api_commands(self):
        return dict(
            test_auth_token=["auth_token"],
            get_connection_errors=[],
        )

    def is_api_adminonly(self):
        return True

    def on_api_command(self, command, data):
        if command == "test_auth_token":
            auth_token = data["auth_token"]
            succeeded, status_text = self.tsd_api_status(auth_token=auth_token)
            if succeeded:
                self._settings.set(["auth_token"],auth_token, force=True)
                self._settings.save(force=True)

            return flask.jsonify({'succeeded': succeeded, 'text': status_text})
        if command == "get_connection_errors":
            return flask.jsonify(self.error_tracker.as_dict())

    ##~~ Eventhandler mixin

    def on_event(self, event, payload):
        event_payload = self.print_event_tracker.on_event(self, event, payload)
        if event_payload:
            self.post_printer_status(event_payload)

    ##~~Startup Plugin

    def on_after_startup(self):
        main_thread = threading.Thread(target=self.main_loop)
        main_thread.daemon = True
        main_thread.start()


    ## Private methods

    def auth_headers(self, auth_token=None):
        return {"Authorization": "Token " + self.auth_token(auth_token)}

    def octoprint_data(self):
        return self._printer.get_current_data()

    def octoprint_settings(self):
        webcam = dict((k, self._settings.effective['webcam'][k]) for k in ('flipV', 'flipH', 'rotate90'))
        return dict(webcam=webcam)

    def main_loop(self):
        backoff = ExpoBackoff(120)
        while True:
            try:
                if not self.is_configured():
                    time.sleep(1)
                    next

                self.error_tracker.attempt('server')

                if self.last_status < time.time() - POST_STATUS_INTERVAL_SECONDS:
                    payload = self.print_event_tracker.octoprint_data(self)
                    self.post_printer_status(payload, throwing=True)
                    backoff.reset()

                speed_up = 5.0 if self.is_actively_printing() else 1.0
                if self.last_pic < time.time() - POST_PIC_INTERVAL_SECONDS / speed_up:
                    if self.post_jpg():
                        backoff.reset()

                time.sleep(1)

            except Exception as e:
                self.sentry.captureException()
                self.error_tracker.add_connection_error('server')

                backoff.more(e)

    def post_jpg(self):
        if not self.is_configured():
            return True

        endpoint = self.canonical_endpoint_prefix() + '/api/octo/pic/'

        try:
            self.error_tracker.attempt('webcam')
            files = {'pic': capture_jpeg(self._settings.global_get(["webcam"]))}
        except:
            self.error_tracker.add_connection_error('webcam')
            return False

        resp = requests.post( endpoint, files=files, headers=self.auth_headers() )
        resp.raise_for_status()

        self.last_pic = time.time()
        return True


    def post_printer_status(self, data, throwing=False):
        if not self.is_configured():
            return

        if not self.ss:
            _logger.debug("Establishing WS connection...")
            self.connect_ws()
            if throwing:
                time.sleep(2.0)    # Wait for websocket to connect

        if not self.ss or not self.ss.connected():  # Check self.ss again as it could already be set to None now.
            if throwing:
                raise Exception('Failed to connect to websocket server')
            else:
                return

        self.ss.send_text(json.dumps(data))
        self.last_status = time.time()

    def connect_ws(self):
        self.ss = ServerSocket(self.canonical_ws_prefix() + "/ws/dev/", self.auth_token(), on_server_ws_msg=self.process_server_msg, on_server_ws_close=self.on_ws_close)
        wst = threading.Thread(target=self.ss.run)
        wst.daemon = True
        wst.start()

    def on_ws_close(self, ws):
        print("closing")
        self.ss = None

    def process_server_msg(self, ws, msg_json):
        print(msg_json)
        msg = json.loads(msg_json)
        if msg.get('commands'):
            _logger.info('Received: ' + msg_json)
        else:
            _logger.debug('Received: ' + msg_json)

        for command in msg.get('commands', []):
            if command["cmd"] == "pause":
                self.commander.prepare_to_pause(self._printer, **command.get('args'))
                self._printer.pause_print()
            if command["cmd"] == 'cancel':
                self._printer.cancel_print()
            if command["cmd"] == 'resume':
                self._printer.resume_print()

    def canonical_endpoint_prefix(self):
        if not self._settings.get(["endpoint_prefix"]):
            return None

        endpoint_prefix = self._settings.get(["endpoint_prefix"]).strip()
        if endpoint_prefix.endswith('/'):
            endpoint_prefix = endpoint_prefix[:-1]
        return endpoint_prefix

    def canonical_ws_prefix(self):
        return re.sub(r'^http', 'ws', self.canonical_endpoint_prefix())

    def auth_token(self, token=None):
        t = token if token is not None else self._settings.get(["auth_token"])
        return t.strip() if t else ''

    def is_actively_printing(self):
        return self._printer.is_operational() and not self._printer.is_ready()

    def is_configured(self):
        return self._settings.get(["endpoint_prefix"]) and self._settings.get(["auth_token"])

    def tsd_api_status(self, auth_token=None):
        endpoint = self.canonical_endpoint_prefix() + '/api/octo/ping/'
        succeeded = False
        status_text = 'Unknown error.'
        try:
            resp = requests.get( endpoint, headers=self.auth_headers(auth_token=self.auth_token(auth_token)) )
            succeeded = resp.ok
            if resp.status_code == 200:
                status_text = 'Secret token is valid. You are awesome!'
            elif resp.status_code == 401:
                status_text = 'Meh~~~. Invalid secret token.'
        except:
            status_text = 'Connection error. Please check OctoPrint\'s internet connection'

        return succeeded, status_text


# If you want your plugin to be registered within OctoPrint under a different name than what you defined in setup.py
# ("OctoPrint-PluginSkeleton"), you may define that here. Same goes for the other metadata derived from setup.py that
# can be overwritten via __plugin_xyz__ control properties. See the documentation for that.
__plugin_name__ = "The Spaghetti Detective (Beta)"
__plugin_author__ = "The Spaghetti Detective Team"
__plugin_url__ = "https://thespaghettidetective.com"
__plugin_description__ = "AI-based open source project for 3D printing failure detection."
__plugin_license__ = "AGPLv3"

def __plugin_load__():
    global __plugin_implementation__
    __plugin_implementation__ = TheSpaghettiDetectivePlugin()

    global __plugin_hooks__
    __plugin_hooks__ = {
        "octoprint.comm.protocol.gcode.queuing": __plugin_implementation__.commander.track_gcode,
        "octoprint.comm.protocol.scripts": (__plugin_implementation__.commander.script_hook, 100000),
        "octoprint.plugin.softwareupdate.check_config": __plugin_implementation__.get_update_information,
    }

