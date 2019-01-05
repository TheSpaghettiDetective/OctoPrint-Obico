# coding=utf-8
from __future__ import absolute_import
import logging
import threading
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

POLL_INTERVAL_SECONDS = 5

class TheSpaghettiDetectivePlugin(octoprint.plugin.SettingsPlugin,
            octoprint.plugin.StartupPlugin,
            octoprint.plugin.EventHandlerPlugin,
            octoprint.plugin.AssetPlugin,
            octoprint.plugin.TemplatePlugin):

    def get_template_configs(self):
        return [
            dict(type="settings", custom_bindings=False)
        ]

    ##~~ SettingsPlugin mixin

    def get_settings_defaults(self):
        return dict(
			endpoint_prefix=''
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

    ##~~Startup Plugin

    def on_after_startup(self):
        main_thread = threading.Thread(target=self.main_loop)
        main_thread.daemon = True
        main_thread.start()


    ## Private methods

    def octoprint_data(self):
        data = self._printer.get_current_data()
        data['temperatures'] = self._printer.get_current_temperatures()
        data['octoprint_port'] = self._octoprint_port
        data['octoprint_ip'] = self._octoprint_ip
        return data

    def main_loop(self):
        last_poll = 0
        while True:
            if not self._settings.get(["endpoint_prefix"]) or not self._settings.get(["auth_token"]):
                next

            if last_poll < time.time() - POLL_INTERVAL_SECONDS:
                last_poll = time.time()
                self.post_jpg_to_endpoint()

            time.sleep(1)

    @backoff.on_exception(backoff.expo, Exception, max_value=240)
    def post_jpg_to_endpoint(self):
        endpoint_prefix = self._settings.get(["endpoint_prefix"]).strip()
        if endpoint_prefix.endswith('/'):
            endpoint_prefix = endpoint_prefix[:-1]

        endpoint = endpoint_prefix + '/dev/predict'

        files = {'image': capture_jpeg(self._settings.global_get(["webcam"]))}

        resp = requests.post( endpoint, files=files)
        resp.raise_for_status()
        for command in resp.json():
            if command["command"] == "print":
                self.download_and_print(command["data"]["file_url"], command["data"]["file_name"])
            if command["command"] == "cancel":
                self._printer.cancel_print()
            if command["command"] == "pause":
                self._printer.pause_print()
            if command["command"] == "resume":
                self._printer.resume_print()


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

