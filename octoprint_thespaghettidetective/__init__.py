# coding=utf-8
from __future__ import absolute_import
import logging
import threading
import sarge
import flask
import json
import re
import os
import sys
import time
import requests
import backoff

from .ws import WebSocketClient, WebSocketClientException
from .commander import Commander
from .utils import ExpoBackoff, ConnectionErrorTracker, SentryWrapper, pi_version, get_tags, not_using_pi_camera, OctoPrintSettingsUpdater
from .print_event import PrintEventTracker
from .webcam_stream import WebcamStreamer, StreamingStatus
from .remote_status import RemoteStatus
from .webcam_capture import JpegPoster
from .file_download import FileDownloader

import octoprint.plugin

__python_version__ = 3 if sys.version_info >= (3, 0) else 2

_logger = logging.getLogger('octoprint.plugins.thespaghettidetective')

POST_STATUS_INTERVAL_SECONDS = 15.0

DEFAULT_USER_ACCOUNT = {'is_pro': False, 'dh_balance': 0}

_print_event_tracker = PrintEventTracker()


class TheSpaghettiDetectivePlugin(
        octoprint.plugin.SettingsPlugin,
        octoprint.plugin.StartupPlugin,
        octoprint.plugin.ShutdownPlugin,
        octoprint.plugin.EventHandlerPlugin,
        octoprint.plugin.AssetPlugin,
        octoprint.plugin.SimpleApiPlugin,
        octoprint.plugin.WizardPlugin,
        octoprint.plugin.TemplatePlugin,):

    def __init__(self):
        self.ss = None
        self.last_status_update_ts = 0
        self.remote_status = RemoteStatus()
        self.commander = Commander()
        self.error_tracker = ConnectionErrorTracker(self)
        self.octoprint_settings_updater = OctoPrintSettingsUpdater(self)
        self.streaming_status = StreamingStatus(self)
        self.jpeg_poster = JpegPoster(self)
        self.file_downloader = FileDownloader(self, _print_event_tracker)
        self.webcam_streamer = None
        self.user_account = DEFAULT_USER_ACCOUNT
        # ~~ Wizard plugin mix

    def is_wizard_required(self):
        return not self._settings.get(["auth_token"])

    def get_wizard_version(self):
        return 2

    # ~~ SettingsPlugin mixin

    def get_settings_defaults(self):
        # Initialize sentry the first opportunity when `self._plugin_version` is available. Is there a better place for it?
        self.sentry = SentryWrapper(self)

        return dict(
            endpoint_prefix='https://app.thespaghettidetective.com',
            disable_video_streaming=False,
            pi_cam_resolution='medium',
            sentry_opt='out',
            video_streaming_compatible_mode='auto',
        )

    # ~~ AssetPlugin mixin

    def get_assets(self):
        # Define your plugin's asset files to automatically include in the
        # core UI here.
        return dict(
            js=["js/TheSpaghettiDetective.js"],
            css=["css/TheSpaghettiDetective.css"],
            less=["less/TheSpaghettiDetective.less"]
        )

    # ~~ Softwareupdate hook

    def get_update_information(self):
        # Define the configuration for your plugin to use with the Software Update
        # Plugin here. See https://github.com/foosel/OctoPrint/wiki/Plugin:-Software-Update
        # for details.
        return dict(
            TheSpaghettiDetective=dict(
                displayName="The Spaghetti Detective",
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
    # ~~ plugin APIs

    def get_api_commands(self):
        return dict(
            test_auth_token=["auth_token"],
            get_plugin_status=[],
            toggle_sentry_opt=[],
            get_sentry_opt=[],
        )

    def is_api_adminonly(self):
        return True

    def on_api_command(self, command, data):
        if command == "test_auth_token":
            auth_token = data["auth_token"]
            succeeded, status_text, _ = self.tsd_api_status(auth_token=auth_token)
            if succeeded:
                self._settings.set(["auth_token"], auth_token, force=True)
                self._settings.save(force=True)

            return flask.jsonify({'succeeded': succeeded, 'text': status_text})
        if command == "get_plugin_status":
            return flask.jsonify(dict(connection_errors=self.error_tracker.as_dict(), streaming_status=self.streaming_status.as_dict()))
        if command == "get_sentry_opt":
            sentry_opt = self._settings.get(["sentry_opt"])
            if sentry_opt == 'out':
                self._settings.set(["sentry_opt"], 'asked')
                self._settings.save(force=True)
            return flask.jsonify(dict(sentryOpt=sentry_opt))
        if command == "toggle_sentry_opt":
            self._settings.set(["sentry_opt"], 'out' if self._settings.get(["sentry_opt"]) == 'in' else 'in', force=True)
            self._settings.save(force=True)

    # ~~ Eventhandler mixin

    def on_event(self, event, payload):
        global _print_event_tracker

        try:
            if event == 'FirmwareData':
                self.octoprint_settings_updater.update_firmware(payload)
                self.post_printer_status(_print_event_tracker.octoprint_data(self))
            elif event == 'SettingsUpdated':
                self.octoprint_settings_updater.update_settings()
                self.post_printer_status(_print_event_tracker.octoprint_data(self))
            elif event.startswith("Print"):
                event_payload = _print_event_tracker.on_event(self, event, payload)
                if event_payload:
                    self.post_printer_status(event_payload)
        except Exception as e:
            self.sentry.captureException(tags=get_tags())
    # ~~Shutdown Plugin

    def on_shutdown(self):
        if self.webcam_streamer:
            self.webcam_streamer.restore()
        not_using_pi_camera()

    # ~~Startup Plugin

    def on_after_startup(self):
        not_using_pi_camera()
        main_thread = threading.Thread(target=self.main_loop)
        main_thread.daemon = True
        main_thread.start()
    # Private methods

    def auth_headers(self, auth_token=None):
        return {"Authorization": "Token " + self.auth_token(auth_token)}

    def main_loop(self):
        global _print_event_tracker

        get_tags()  # init tags to minimize risk of race condition

        self.user_account = self.wait_for_auth_token().get('user', DEFAULT_USER_ACCOUNT)
        self.sentry.user_context({'id': self.auth_token()})
        _logger.info('User account: {}'.format(self.user_account))
        _logger.debug('Plugin settings: {}'.format(self._settings.get_all_data()))

        self.streaming_status.set_status(eligible=bool(self.user_account.get('is_pro')))
        if self.user_account.get('is_pro') and not self._settings.get(["disable_video_streaming"]):
            _logger.info('Starting webcam streamer')
            self.webcam_streamer = WebcamStreamer(self, self.sentry)
            stream_thread = threading.Thread(target=self.webcam_streamer.video_pipeline)
            stream_thread.daemon = True
            stream_thread.start()

        backoff = ExpoBackoff(120)
        while True:
            try:
                self.error_tracker.attempt('server')

                if self.last_status_update_ts < time.time() - POST_STATUS_INTERVAL_SECONDS:
                    self.post_printer_status(_print_event_tracker.octoprint_data(self), throwing=True)
                    backoff.reset()

                self.jpeg_poster.post_jpeg_if_needed()
                time.sleep(1)

            except WebSocketClientException as e:
                self.error_tracker.add_connection_error('server')
                backoff.more(e)
            except Exception as e:
                self.sentry.captureException(tags=get_tags())
                self.error_tracker.add_connection_error('server')
                backoff.more(e)

    def post_printer_status(self, data, throwing=False):
        if self.send_ws_msg_to_server(data, throwing=throwing):
            self.last_status_update_ts = time.time()

    def send_ws_msg_to_server(self, data, throwing=False):
        """ Returns True if message is sent successfully. Otherwise returns False. """

        if not self.is_configured():
            _logger.warning("Plugin not configured. Not sending message to server...")
            return False

        if not self.ss:
            _logger.debug("Establishing WS connection...")
            self.connect_ws()
            if throwing:
                time.sleep(2.0)    # Wait for websocket to connect

        if not self.ss or not self.ss.connected():  # Check self.ss again as it could already be set to None now.
            if throwing:
                raise WebSocketClientException('Failed to connect to websocket server')
            else:
                return False

        _logger.debug("Sending to server: \n{}".format(data))
        if __python_version__ == 3:
            self.ss.send_text(json.dumps(data, default=str))
        else:
            self.ss.send_text(json.dumps(data, encoding='iso-8859-1', default=str))
        return True

    def connect_ws(self):
        self.ss = WebSocketClient(self.canonical_ws_prefix() + "/ws/dev/", token=self.auth_token(), on_ws_msg=self.process_server_msg, on_ws_close=self.on_ws_close)
        wst = threading.Thread(target=self.ss.run)
        wst.daemon = True
        wst.start()

    def on_ws_close(self, ws):
        _logger.error("Server websocket is closing")
        self.ss = None

    def process_server_msg(self, ws, msg_json):
        global _print_event_tracker

        try:
            msg = json.loads(msg_json)
            if msg.get('commands') or msg.get('passthru'):
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
                if command["cmd"] == 'print':
                    self.start_print(**command.get('args'))

            passsthru = msg.get('passthru')
            if passsthru:
                target = getattr(self, passsthru.get('target'))
                func = getattr(target, passsthru['func'], None)
                if not func:
                    return

                ack_ref = passsthru.get('ref')
                ret = func(*(passsthru.get("args", [])))

                if ack_ref:
                    self.send_ws_msg_to_server({'passthru': {'ref': ack_ref, 'ret': ret}})

                time.sleep(0.2)  # chnages, such as setting temp will take a bit of time to be reflected in the status. wait for it
                self.post_printer_status(_print_event_tracker.octoprint_data(self))

            if msg.get('janus') and self.webcam_streamer:
                self.webcam_streamer.pass_to_janus(msg.get('janus'))

            if msg.get('remote_status'):
                self.remote_status.update(msg.get('remote_status'))
                if self.remote_status['viewing']:
                    self.jpeg_poster.post_jpeg_if_needed(force=True)

        except:
            self.sentry.captureException(tags=get_tags())

    # ~~ helper methods

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

    def is_configured(self):
        return self._settings.get(["endpoint_prefix"]) and self._settings.get(["auth_token"])

    def tsd_api_status(self, auth_token=None):
        endpoint = self.canonical_endpoint_prefix() + '/api/v1/octo/ping/'
        succeeded = False
        status_text = 'Unknown error.'
        resp = None
        try:
            resp = requests.get(endpoint, headers=self.auth_headers(auth_token=self.auth_token(auth_token)))
            succeeded = resp.ok
            if resp.status_code == 200:
                status_text = 'Secret token is valid. You are awesome!'
            elif resp.status_code == 401:
                status_text = 'Meh~~~. Invalid secret token.'
        except:
            status_text = 'Connection error. Please check OctoPrint\'s internet connection'

        return succeeded, status_text, resp

    @backoff.on_predicate(backoff.expo, max_value=1200)
    def wait_for_auth_token(self):
        while not self.is_configured():
            time.sleep(1)

        succeeded, _, resp = self.tsd_api_status()
        if succeeded:
            return resp.json()

        return None


# If you want your plugin to be registered within OctoPrint under a different name than what you defined in setup.py
# ("OctoPrint-PluginSkeleton"), you may define that here. Same goes for the other metadata derived from setup.py that
# can be overwritten via __plugin_xyz__ control properties. See the documentation for that.
__plugin_name__ = "The Spaghetti Detective"
__plugin_author__ = "The Spaghetti Detective Team"
__plugin_url__ = "https://thespaghettidetective.com"
__plugin_description__ = "AI-based open source project for 3D printing failure detection."
__plugin_license__ = "AGPLv3"
__plugin_pythoncompat__ = ">=2.7,<4"


def __plugin_load__():
    global __plugin_implementation__
    __plugin_implementation__ = TheSpaghettiDetectivePlugin()

    global __plugin_hooks__
    __plugin_hooks__ = {
        "octoprint.comm.protocol.gcode.queuing": __plugin_implementation__.commander.track_gcode,
        "octoprint.comm.protocol.scripts": (__plugin_implementation__.commander.script_hook, 100000),
        "octoprint.plugin.softwareupdate.check_config": __plugin_implementation__.get_update_information,
    }
