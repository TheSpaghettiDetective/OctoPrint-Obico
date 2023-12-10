# coding=utf-8
from __future__ import absolute_import
import bson
import logging
import threading
import sarge
import json
import re
import os
import sys
import time
import requests
import backoff
from collections import deque

from octoprint_obico.nozzlecam import NozzleCam
try:
    import queue
except ImportError:
    import Queue as queue

from .ws import WebSocketClient, WebSocketConnectionException
from .pause_resume_sequence import PauseResumeGCodeSequence
from .utils import (
    ExpoBackoff, SentryWrapper, pi_version,
    OctoPrintSettingsUpdater, run_in_thread,
    server_request, migrate_tsd_settings, octoprint_webcam_settings)
from .lib.error_stats import error_stats
from .lib import alert_queue
from .print_job_tracker import PrintJobTracker
from .janus import JanusConn
from .remote_status import RemoteStatus
from .webcam_capture import JpegPoster, capture_jpeg
from .file_downloader import FileDownloader
from .tunnel import LocalTunnel
from . import plugin_apis
from .client_conn import ClientConn
import zlib
from .printer_discovery import PrinterDiscovery
from .gcode_hooks import GCodeHooks
from .gcode_preprocessor import GcodePreProcessorWrapper
from .file_operations import FileOperations

import octoprint.plugin

__python_version__ = 3 if sys.version_info >= (3, 0) else 2

_logger = logging.getLogger('octoprint.plugins.obico')

POST_STATUS_INTERVAL_SECONDS = 50.0

DEFAULT_LINKED_PRINTER = {'is_pro': False}

_print_job_tracker = PrintJobTracker()



class ObicoPlugin(
        octoprint.plugin.SettingsPlugin,
        octoprint.plugin.StartupPlugin,
        octoprint.plugin.ShutdownPlugin,
        octoprint.plugin.EventHandlerPlugin,
        octoprint.plugin.AssetPlugin,
        octoprint.plugin.SimpleApiPlugin,
        octoprint.plugin.BlueprintPlugin,
        octoprint.plugin.TemplatePlugin,):

    def __init__(self):
        global _print_job_tracker

        self.shutting_down = False
        self.ss = None
        self.status_posted_to_server_ts = 0
        self.message_queue_to_server = queue.Queue(maxsize=1000)
        self.status_update_booster = 0    # update status at higher frequency when self.status_update_booster > 0
        self.status_update_lock = threading.RLock()
        self.remote_status = RemoteStatus()
        self.pause_resume_sequence = PauseResumeGCodeSequence()
        self.gcode_hooks = GCodeHooks(self, _print_job_tracker)
        self.gcode_preprocessor = GcodePreProcessorWrapper(self)
        self.octoprint_settings_updater = OctoPrintSettingsUpdater(self)
        self.jpeg_poster = JpegPoster(self)
        self.file_downloader = FileDownloader(self, _print_job_tracker)
        self.linked_printer = DEFAULT_LINKED_PRINTER
        self.local_tunnel = None
        self.janus = JanusConn(self)
        self.client_conn = ClientConn(self)
        self.discovery = None
        self.bailed_because_tsd_plugin_running = False
        self.printer_events_posted = dict()
        self.file_operations = FileOperations(self)
        self.nozzlecam = NozzleCam(self)


    # ~~ Custom event registration

    def register_custom_events(*args, **kwargs):
      return ["command"]

    # ~~ SettingsPlugin mixin

    def get_settings_defaults(self):

        return dict(
            endpoint_prefix='https://app.obico.io',
            disable_video_streaming=False,
            pi_cam_resolution='medium',
            sentry_opt='out',
            video_streaming_compatible_mode='auto',
        )

    def on_settings_save(self, data):
        octoprint.plugin.SettingsPlugin.on_settings_save(self, data)
        alert_queue.add_alert({
            'level': 'warning',
            'cause': 'restart_required',
            'text': 'Settings saved! If you are in the setup wizard, restart OctoPrint after the setup is done. Otherwise, restart OctoPrint now for the changes to take effect.',
            'buttons': ['never', 'ok']
        }, self)

    # ~~ AssetPlugin mixin

    def get_assets(self):
        # Define your plugin's asset files to automatically include in the
        # core UI here.
        return dict(
            js=["js/ObicoSettings.js", "js/ObicoWizard.js"],
            css=["css/main.css"],
            less=["less/main.less"]
        )

    # ~~ Softwareupdate hook

    def get_update_information(self):
        # Define the configuration for your plugin to use with the Software Update
        # Plugin here. See https://github.com/foosel/OctoPrint/wiki/Plugin:-Software-Update
        # for details.
        return dict(
            obico=dict(
                displayName="Obico for OctoPrint",
                displayVersion=self._plugin_version,

                # version check: github repository
                type="github_release",
                user="TheSpaghettiDetective",
                repo="OctoPrint-Obico",
                current=self._plugin_version,

                # update method: pip
                pip="https://github.com/TheSpaghettiDetective/OctoPrint-Obico/archive/{target_version}.zip"
            )
        )
    # ~~ plugin APIs

    def get_api_commands(self):
        return plugin_apis.get_api_commands()

    def is_api_adminonly(self):
        return True

    def on_api_command(self, command, data):
        return plugin_apis.on_api_command(self, command, data)

    # ~~ Eventhandler mixin

    def on_event(self, event, payload):
        global _print_job_tracker

        self.boost_status_update()

        try:
            if event == 'FirmwareData':
                self.octoprint_settings_updater.update_firmware(payload)
                self.post_update_to_server()
            elif event == 'SettingsUpdated':
                self.octoprint_settings_updater.update_settings()
                self.post_update_to_server()
            elif event == 'Error':
                event_data = {'event_title': 'OctoPrint Error', 'event_text': payload.get('error', 'Unknown Error'), 'event_class': 'ERROR', 'event_type': 'PRINTER_ERROR'}
                self.passthru_printer_event_to_client(event_data)
                self.post_printer_event_to_server(event_data, attach_snapshot=True, spam_tolerance_seconds=60*30)
            elif event.startswith("Print") or event in (
                'plugin_pi_support_throttle_state',
            ):
                event_payload = _print_job_tracker.on_event(self, event, payload)
                if event_payload:
                    self.post_update_to_server(data=event_payload)
            elif event == 'FilamentChange':
                run_in_thread(self.post_filament_change_event)
        except Exception as e:
            self.sentry.captureException()
    # ~~Shutdown Plugin

    def on_shutdown(self):
        self.shutting_down = True
        if self.ss is not None:
            self.ss.close()
        if self.janus:
            self.janus.shutdown()
        if self.client_conn:
            self.client_conn.close()


    # ~~Startup Plugin

    def on_startup(self, host, port):
        if 'thespaghettidetective' in self._plugin_manager.plugins and self._plugin_manager.plugins.get('thespaghettidetective').enabled:
            self.bailed_because_tsd_plugin_running = True
            alert_queue.add_alert({
                'level': 'error',
                'cause': 'bailed_because_tsd_plugin_running',
                'title': 'Plugin Conflicts',
                'text': 'The Obico plugin failed to start because "Access Anywhere - The Spaghetti Detective" plugin is still installed and enabled. Please remove or disable "Access Anywhere - The Spaghetti Detective" plugin and restart OctoPrint.',
                'info_url': 'https://www.obico.io/docs/user-guides/move-from-tsd-to-obico-in-octoprint',
                'buttons': ['more_info', 'ok']
                }, self, post_to_server=True)

        # TODO: remove once all TSD users have migrated
        migrate_tsd_settings(self)

        self.octoprint_port = port if port else self._settings.getInt(["server", "port"])

    def on_after_startup(self):
        if self.bailed_because_tsd_plugin_running:
            return

        main_thread = threading.Thread(target=self.main_loop)
        main_thread.daemon = True
        main_thread.start()

    # Private methods

    def auth_headers(self, auth_token=None):
        return {"Authorization": "Token " + self.auth_token(auth_token)}

    def main_loop(self):
        global _print_job_tracker
        self.sentry = SentryWrapper(self)

        if not self.is_configured() and self.canonical_endpoint_prefix():
            self.discovery = PrinterDiscovery(plugin=self)
            self.discovery.start_and_block()
            self.discovery = None

        self.linked_printer = self.wait_for_auth_token().get('printer', DEFAULT_LINKED_PRINTER)

        self.sentry.init_context()
        _logger.info('Linked printer: {}'.format(self.linked_printer))
        _logger.debug('Plugin settings: {}'.format(self._settings.get_all_data()))

        # Notify plugin UI about the server connection status change
        self._plugin_manager.send_plugin_message(self._identifier, {'plugin_updated': True})

        # Janus may take a while to start, or fail to start. Put it in thread to make sure it does not block
        janus_thread = threading.Thread(target=self.janus.start)
        janus_thread.daemon = True
        janus_thread.start()

        url = 'http://{}:{}'.format('127.0.0.1', self.octoprint_port)
        self.local_tunnel = LocalTunnel(
            base_url=url,
            on_http_response=self.send_ws_msg_to_server,
            on_ws_message=self.send_ws_msg_to_server,
            data_dir=self.get_plugin_data_folder(),
            sentry=self.sentry)

        jpeg_post_thread = threading.Thread(target=self.jpeg_poster.pic_post_loop)
        jpeg_post_thread.daemon = True
        jpeg_post_thread.start()

        status_update_to_client_thread = threading.Thread(target=self.status_update_to_client_loop)
        status_update_to_client_thread.daemon = True
        status_update_to_client_thread.start()

        message_to_server_thread = threading.Thread(target=self.message_to_server_loop)
        message_to_server_thread.daemon = True
        message_to_server_thread.start()

        self.nozzlecam.create_nozzlecam_config()

        while True:
            try:
                interval_in_seconds = POST_STATUS_INTERVAL_SECONDS
                if self.status_update_booster > 0:
                    interval_in_seconds /= 5

                if self.status_posted_to_server_ts < time.time() - interval_in_seconds:
                    self.post_update_to_server()

            except Exception as e:
                self.sentry.captureException()

            time.sleep(1)

    def message_to_server_loop(self):

        def on_server_ws_close(ws, close_status_code):
            if self.ss and self.ss.ws and self.ss.ws == ws:
                self._plugin_manager.send_plugin_message(self._identifier, {'plugin_updated': True})
                self.local_tunnel.close_all_octoprint_ws()
                self.ss = None

                if close_status_code == 4321:
                    alert_queue.add_alert({
                        'level': 'error',
                        'cause': 'shared_auth_token',
                        'text': 'The same authentication token is being used by another printer. To ensure the security and correct function of your printer, please relink your printer immediately.',
                        'info_url': 'https://obico.io/docs/user-guides/warnings/shared-auth-token-error/',
                        'buttons': ['more_info', 'never', 'ok']
                    }, self)
                    _logger.error('Shared auth_token detected. Shutting down.')
                    self.on_shutdown()

        def on_server_ws_open(ws):
            if self.ss and self.ss.ws and self.ss.ws == ws:
                self._plugin_manager.send_plugin_message(self._identifier, {'plugin_updated': True})
                self.post_update_to_server() # Make sure an update is sent asap so that the server can rely on the availability of essential info such as agent.version

        server_ws_backoff = ExpoBackoff(300)
        while self.shutting_down is False:
            try:
                (data, as_binary) = self.message_queue_to_server.get()

                if not self.is_configured():
                    _logger.warning("Plugin not configured. Not sending message to server...")
                    continue

                if not self.linked_printer.get('id'):  # id is present only when auth_token is validated by the server
                    _logger.warning("auth_token is not validated. Not sending message to server...")
                    continue

                error_stats.attempt('server')

                if not self.ss or not self.ss.connected():
                    self.ss = WebSocketClient(self.canonical_ws_prefix() + "/ws/dev/", token=self.auth_token(), on_ws_msg=self.process_server_msg, on_ws_close=on_server_ws_close, on_ws_open=on_server_ws_open)

                if as_binary:
                    raw = bson.dumps(data)
                    _logger.debug("Sending binary ({} bytes) to server".format(len(raw)))
                else:
                    _logger.debug("Sending to server: \n{}".format(data))
                    if __python_version__ == 3:
                        raw = json.dumps(data, default=str)
                    else:
                        raw = json.dumps(data, encoding='iso-8859-1', default=str)
                self.ss.send(raw, as_binary=as_binary)
                server_ws_backoff.reset()
            except WebSocketConnectionException as e:
                _logger.warning(e)
                error_stats.add_connection_error('server', self)
                if self.ss:
                    self.ss.close()
                server_ws_backoff.more(e)
            except Exception as e:
                self.sentry.captureException()
                error_stats.add_connection_error('server', self)
                if self.ss:
                    self.ss.close()
                server_ws_backoff.more(e)

    def post_update_to_server(self, data=None):
        if not data:
            data = _print_job_tracker.status(self)
        self.send_ws_msg_to_server(data)
        self.status_posted_to_server_ts = time.time()

    def send_ws_msg_to_server(self, data, as_binary=False):
        try:
            self.message_queue_to_server.put_nowait((data, as_binary))
        except queue.Full:
            _logger.warning("Server message queue is full, msg dropped")

    def process_server_msg(self, ws, raw_data):
        global _print_job_tracker
        try:
            # raw_data can be both json or bson
            # no py2 compat way to properly detect type here
            # (w/o patching ws lib)
            try:
                msg = json.loads(raw_data)
                _logger.debug('Received: ' + raw_data)
            except ValueError:
                msg = bson.loads(raw_data)
                _logger.debug(
                    'received binary message ({} bytes)'.format(len(raw_data)))

            need_status_boost = False
            for command in msg.get('commands', []):
                self._event_bus.fire(octoprint.events.Events.PLUGIN_OBICO_COMMAND, command)

                if command["cmd"] == "pause":
                    self.pause_resume_sequence.prepare_to_pause(
                        self._printer,
                        self._printer_profile_manager.get_current_or_default(),
                        **command.get('args'))
                    self._printer.pause_print()

                if command["cmd"] == 'cancel':
                    self._printer.cancel_print()

                if command["cmd"] == 'resume':
                    self._printer.resume_print()

                if command["cmd"] == 'print':
                    self.start_print(**command.get('args'))

            if msg.get('passthru'):
                self.client_conn.on_message_to_plugin(msg.get('passthru'))
                need_status_boost = True

            if msg.get('janus') and self.janus:
                self.janus.pass_to_janus(msg.get('janus'))

            if msg.get('remote_status'):
                self.remote_status.update(msg.get('remote_status'))
                need_status_boost = True
                if self.remote_status['viewing']:
                    self.jpeg_poster.need_viewing_boost.set()

            if msg.get('http.tunnel') and self.local_tunnel:
                kwargs = msg.get('http.tunnel')
                tunnel_thread = threading.Thread(
                    target=self.local_tunnel.send_http_to_local,
                    kwargs=kwargs)
                tunnel_thread.is_daemon = True
                tunnel_thread.start()

            if msg.get('http.tunnelv2') and self.local_tunnel:
                kwargs = msg.get('http.tunnelv2')
                tunnel_thread = threading.Thread(
                    target=self.local_tunnel.send_http_to_local_v2,
                    kwargs=kwargs)
                tunnel_thread.is_daemon = True
                tunnel_thread.start()

            if msg.get('ws.tunnel') and self.local_tunnel:
                kwargs = msg.get('ws.tunnel')
                kwargs['type_'] = kwargs.pop('type')
                self.local_tunnel.send_ws_to_local(**kwargs)

            if need_status_boost:
                self.boost_status_update()
        except:
            self.sentry.captureException()

    def status_update_to_client_loop(self):
        while self.shutting_down is False:
            interval = 0.75 if self.status_update_booster > 0 else 2
            time.sleep(interval)
            self.post_printer_status_to_client()
            with self.status_update_lock:
                self.status_update_booster -= 1

    def post_printer_status_to_client(self):
        status = _print_job_tracker.status(self, status_only=True)
        # Backward compatibility: mobile apps 1.66 or earlier expects {octoprint_data: ...}
        status_data = status.get('status', {})
        status = {'status': status_data, 'octoprint_data': status_data}

        self.client_conn.send_msg_to_client(status)

    def boost_status_update(self):
        self.post_printer_status_to_client()
        with self.status_update_lock:
            self.status_update_booster = 20

    def post_printer_event_to_server(self, event_data, attach_snapshot=False, spam_tolerance_seconds=60*60*24*1000):
        event_title = event_data['event_title']

        last_sent = self.printer_events_posted.get(event_title, 0)
        if time.time() < last_sent + spam_tolerance_seconds:
            return

        self.printer_events_posted[event_title] = time.time()

        files = None
        if attach_snapshot:
            try:
                files = {'snapshot': capture_jpeg(self)}
            except Exception as e:
                _logger.warning('Failed to capture jpeg - ' + str(e))
                pass
        resp = server_request('POST', '/api/v1/octo/printer_events/', self, timeout=60, files=files, data=event_data, headers=self.auth_headers())

    def post_filament_change_event(self):
        event_text = '<div><i>Printer:</i> {}</div><div><i>G-Code:</i> {}</div>'.format(
            self.linked_printer.get('name', 'Unknown printer'),
            self._printer.get_current_data().get('job', {}).get('file', {}).get('name', 'Unknown G-Code or not printing')
        )
        event_data = dict(event_title = 'Filament Change Required', event_text = event_text, event_class = 'WARNING', event_type = 'FILAMENT_CHANGE', notify='true')
        self.post_printer_event_to_server(event_data,attach_snapshot=True, spam_tolerance_seconds=60*10) # Allow to nudge the user for filament change every 10 minutes

    def passthru_printer_event_to_client(self, event_data):
        self.send_ws_msg_to_server({'passthru': {'printer_event': event_data}})

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
        return server_request('GET', '/api/v1/octo/printer/', self, headers=self.auth_headers(auth_token=self.auth_token(auth_token)))

    @backoff.on_predicate(backoff.expo, max_value=1200)
    def wait_for_auth_token(self):
        while not self.is_configured():
            time.sleep(1)

        resp = self.tsd_api_status()
        if resp and resp.ok:
            return resp.json()
        else:
            return None  # Triggers a backoff

    @octoprint.plugin.BlueprintPlugin.route('/grab-discovery-secret', methods=['get', 'options'])
    def grab_discovery_secret(self):
        if self.discovery:
            return self.discovery.id_for_secret()

    def is_blueprint_protected(self):
        # !! HEADSUP bluprint endpoints does not require authentication
        return False

    def is_pro_user(self):
        return self.linked_printer.get('is_pro')

# If you want your plugin to be registered within OctoPrint under a different name than what you defined in setup.py
# ("OctoPrint-PluginSkeleton"), you may define that here. Same goes for the other metadata derived from setup.py that
# can be overwritten via __plugin_xyz__ control properties. See the documentation for that.
__plugin_name__ = "Obico for OctoPrint"
__plugin_author__ = "The Obico team"
__plugin_url__ = "https://www.obico.io/"
__plugin_description__ = "Securely monitor and control your OctoPrint-connected printer from anywhere for free with Obico. Get unlimited live webcam streaming, full OctoPrint remote access, printer status notifications, and a free companion mobile app for iOS and Android. The best part? AI-powered failure detection watches your prints so you don’t have to."
__plugin_license__ = "AGPLv3"
__plugin_pythoncompat__ = ">=2.7,<4"


def __plugin_load__():
    global __plugin_implementation__
    __plugin_implementation__ = ObicoPlugin()

    global __plugin_hooks__
    __plugin_hooks__ = {
        "octoprint.comm.protocol.gcode.queuing": __plugin_implementation__.gcode_hooks.queuing_gcode,
        "octoprint.comm.protocol.gcode.received": __plugin_implementation__.gcode_hooks.received_gcode,
        "octoprint.comm.protocol.gcode.sent": __plugin_implementation__.gcode_hooks.sent_gcode,
        "octoprint.filemanager.preprocessor": __plugin_implementation__.gcode_preprocessor.gcode_preprocessor,
        "octoprint.comm.protocol.scripts": (__plugin_implementation__.pause_resume_sequence.script_hook, 100000),
        "octoprint.plugin.softwareupdate.check_config": __plugin_implementation__.get_update_information,
        "octoprint.events.register_custom_events": __plugin_implementation__.register_custom_events,
    }
