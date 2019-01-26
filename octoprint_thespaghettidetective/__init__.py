# coding=utf-8
from __future__ import absolute_import
import logging
import threading
import json
import os, sys, time
import requests
import backoff

from .webcam_capture import capture_jpeg

### (Don't forget to remove me)
# This is a basic skeleton for your plugin's __init__.py. You probably want to adjust the class name of your plugin
# as well as the plugin mixins it's subclassing from. This is really just a basic skeleton to get you started,
# defining your plugin as a template plugin, settings and asset plugin. Feel free to add or remove mixins
# as necessary.
#
# Take a look at the documentation on what other plugin mixins are available.

import octoprint.plugin

_logger = logging.getLogger(__name__)

POST_PIC_INTERVAL_SECONDS = 5
POST_STATUS_INTERVAL_SECONDS = 30

class TheSpaghettiDetectivePlugin(
            octoprint.plugin.SettingsPlugin,
            octoprint.plugin.StartupPlugin,
            octoprint.plugin.EventHandlerPlugin,
            octoprint.plugin.AssetPlugin,
            octoprint.plugin.TemplatePlugin,):


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

    ##~~ Eventhandler mixin

    def on_event(self, event, payload):
        #if event.startswith("Print"):
            self.post_printer_status({
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

    def auth_headers(self):
        return {"Authorization": "Token " + self._settings.get(["auth_token"])}

    def octoprint_data(self):
        return self._printer.get_current_data()

    @backoff.on_exception(backoff.expo, Exception, max_value=240)
    def main_loop(self):
        last_post_pic = 0
        last_post_status = 0
        while True:
            if not self.is_configured():
                time.sleep(1)
                next

            if last_post_pic < time.time() - POST_PIC_INTERVAL_SECONDS:
                last_post_pic = time.time()
                self.post_jpg()

            if last_post_status < time.time() - POST_STATUS_INTERVAL_SECONDS:
                last_post_status = time.time()
                self.post_printer_status({
                    "octoprint_data": self.octoprint_data()
                })

            time.sleep(1)

    def post_jpg(self):
        if not self.is_configured():
            return

        endpoint = self.canonical_endpoint_prefix() + '/api/octo/pic'

        files = {'pic': capture_jpeg(self._settings.global_get(["webcam"]))}
        resp = requests.post( endpoint, files=files, headers=self.auth_headers() )
        resp.raise_for_status()
        self.process_response(resp)

    def post_printer_status(self, json_data):
        if not self.is_configured():
            return

        endpoint = self.canonical_endpoint_prefix() + '/api/octo/status'
        _logger.debug(json.dumps(json_data))
        resp = requests.post(
            endpoint,
            json=json_data,
            headers = self.auth_headers(),
            )
        resp.raise_for_status()
        self.process_response(resp)

    def process_response(self, resp):
        print(resp.json())
        for command in resp.json().get('commands', []):
            if command["cmd"] == "pause":
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

    def is_configured(self):
        return self._settings.get(["endpoint_prefix"]) and self._settings.get(["auth_token"])

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

