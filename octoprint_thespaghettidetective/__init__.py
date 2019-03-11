# coding=utf-8
from __future__ import absolute_import
import logging
import threading
import flask
import json
import re
import os, sys, time
import requests
import backoff

from .webcam_capture import capture_jpeg
from .ws import ServerSocket

### (Don't forget to remove me)
# This is a basic skeleton for your plugin's __init__.py. You probably want to adjust the class name of your plugin
# as well as the plugin mixins it's subclassing from. This is really just a basic skeleton to get you started,
# defining your plugin as a template plugin, settings and asset plugin. Feel free to add or remove mixins
# as necessary.
#
# Take a look at the documentation on what other plugin mixins are available.

import octoprint.plugin

_logger = logging.getLogger(__name__)

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
            octoprint.plugin.TemplatePlugin,):

    def __init__(self):
        self.ss = None
        self.saved_temps = {}
        self.last_pic = 0
        self.last_status = 0

    def get_template_configs(self):
        return [
            dict(type="settings", custom_bindings=False)
        ]

    ##~~ SettingsPlugin mixin

    def get_settings_defaults(self):
        return dict(
            endpoint_prefix='https://app.thespaghettidetective.com/'
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
            TheSpaghettiDetective=dict(
                displayName="TheSpaghettiDetective Plugin",
                displayVersion=self._plugin_version,

                # version check: github repository
                type="github_release",
                user="kennethjiang",
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
        )

    def is_api_adminonly(self):
        return True

    def on_api_command(self, command, data):
        if command == "test_auth_token":
            auth_token = data["auth_token"]
            succeeded, status_text = self.tsd_api_status(auth_token=auth_token)
            return flask.jsonify({'succeeded': succeeded, 'text': status_text})

    ##~~ Eventhandler mixin

    def on_event(self, event, payload):
        self.printer_status({
            "octoprint_event": {
                "event_type": event,
                "data": payload
                },
            "octoprint_data": self.octoprint_data()
            })


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

    @backoff.on_exception(backoff.expo, Exception, max_value=240)
    def main_loop(self):
        while True:
            if not self.is_configured():
                time.sleep(1)
                next

            if self.last_status < time.time() - POST_STATUS_INTERVAL_SECONDS:
                self.printer_status({
                    "octoprint_data": self.octoprint_data(),
                    "octoprint_settings": self.octoprint_settings()
                })

            speed_up = 5.0 if self.is_actively_printing() else 1.0
            if self.last_pic < time.time() - POST_PIC_INTERVAL_SECONDS / speed_up:
                self.jpg()

            time.sleep(1)

    def jpg(self):
        if not self.is_configured():
            return

        self.last_pic = time.time()

        endpoint = self.canonical_endpoint_prefix() + '/api/octo/pic/'

        files = {'pic': capture_jpeg(self._settings.global_get(["webcam"]))}
        resp = requests.post( endpoint, files=files, headers=self.auth_headers() )
        resp.raise_for_status()

    def printer_status(self, data):
        if not self.is_configured():
            return

        self.last_status = time.time()
        if not self.ss:
            self.connect_ws()

        if not self.ss.connected():
            return

        self.ss.send_text(json.dumps(data))

    def connect_ws(self):
        self.ss = ServerSocket(self.canonical_ws_prefix() + "/ws/dev/", self.auth_token(), on_message=self.process_server_msg, on_close=self.on_ws_close)
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
                self._printer.pause_print()
            if command["cmd"] == 'cancel':
                self._printer.cancel_print()
            if command["cmd"] == 'resume':
                self._printer.resume_print()
            if command["cmd"] == 'set_temps':
                self.set_temps(**command.get('args'))
            if command["cmd"] == 'restore_temps':
                self.restore_temps()

    def restore_temps(self):
        for heater in self.saved_temps.keys():
            self._printer.set_temperature(heater, self.saved_temps[heater]['target'] + self.saved_temps[heater]['offset'])

        time.sleep(10)
        while True:
            temps = self._printer.get_current_temperatures()
            not_reached = [k for k,v in self._printer.get_current_temperatures().items() if v['target'] - 2.0 > v['actual'] + v['offset'] ]

            if len(not_reached) == 0:
                break

            time.sleep(5)

        self.saved_temps = {}

    def set_temps(self, heater=None, target=None, save=False):
        current_temps = self._printer.get_current_temperatures()

        if heater == 'tools':
            for tool_heater in [h for h in current_temps.keys() if h.startswith('tool')]:
                if save:
                    self.saved_temps[tool_heater] = current_temps[tool_heater]
                self._printer.set_temperature(tool_heater, target)

        elif heater == 'bed' and current_temps.get('bed'):
            if save:
                self.saved_temps['bed'] = current_temps.get('bed')
            self._printer.set_temperature('bed', target)

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
        return token.strip() if token else self._settings.get(["auth_token"]).strip()

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
                status_text = 'Secret token is valid. Do not forget to press the "Save" button.'
            elif resp.status_code == 401:
                status_text = 'Invalid secret token.'
        except:
            status_text = 'Connection error. Please check OctoPrint\'s internet connection'

        return succeeded, status_text

# If you want your plugin to be registered within OctoPrint under a different name than what you defined in setup.py
# ("OctoPrint-PluginSkeleton"), you may define that here. Same goes for the other metadata derived from setup.py that
# can be overwritten via __plugin_xyz__ control properties. See the documentation for that.
__plugin_name__ = "The Spaghetti Detective"

def __plugin_load__():
    global __plugin_implementation__
    __plugin_implementation__ = TheSpaghettiDetectivePlugin()

    global __plugin_hooks__
    __plugin_hooks__ = {
        "octoprint.plugin.softwareupdate.check_config": __plugin_implementation__.get_update_information
    }

