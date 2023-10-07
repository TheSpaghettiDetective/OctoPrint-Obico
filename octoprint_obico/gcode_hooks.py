import logging
import re
import sys
import time
import octoprint

from .utils import run_in_thread

_logger = logging.getLogger('octoprint.plugins.obico')

class GCodeHooks:

    def __init__(self, plugin, _print_job_tracker):
        self.plugin = plugin
        self._print_job_tracker = _print_job_tracker
        self.terminal_feed_is_on = False

    def queuing_gcode(self, comm_instance, phase, cmd, cmd_type, gcode, subcode=None, tags=None, *args, **kwargs):
        self.plugin.pause_resume_sequence.track_gcode(comm_instance, phase, cmd, cmd_type, gcode, subcode=None, tags=None, *args, **kwargs)

        if gcode and gcode in ('M600', 'M701' or 'M702'):
            run_in_thread(self.plugin.post_filament_change_event)

        if gcode and 'M117 OBICO_LAYER_INDICATOR' in cmd:
            layer_num = int(cmd.replace("M117 OBICO_LAYER_INDICATOR ", ""))

            # First layer AI-related
            if layer_num == 1:
                if not self.plugin.nozzlecam.on_first_layer:
                    self.plugin.nozzlecam.on_first_layer = True
                    run_in_thread(self.plugin.nozzlecam.start)
            else:
                self.plugin.nozzlecam.on_first_layer = False

            self._print_job_tracker.increment_layer_height(layer_num)
            return [] # remove layer indicator

    def received_gcode(self, comm, line, *args, **kwargs):

        # credit: https://github.com/QuinnDamerell/OctoPrint-OctoEverywhere/blob/ef37e6c9ce6798e8af54a5fd81215d430c05bfad/octoprint_octoeverywhere/__init__.py#L272
        lineLower = line.lower()
        if "m600" in lineLower or ("fsensor_update" in lineLower and "m600" in lineLower) \
            or "paused for user" in lineLower or "// action:paused" in lineLower:
            run_in_thread(self.plugin.post_filament_change_event)

        if line and lineLower not in ['wait']:
            self.passthru_terminal_feed(line)

        return line

    def sent_gcode(self, comm_instance, phase, cmd, cmd_type, gcode, subcode=None, tags=None, *args, **kwargs):
        if cmd:
            self.passthru_terminal_feed(cmd)

    def passthru_terminal_feed(self, msg):
        # No need to run in thread, as send_ws_msg_to_server is non-blocking
        if self.plugin.remote_status['viewing'] and self.terminal_feed_is_on:
            self.plugin.send_ws_msg_to_server({'passthru': {'terminal_feed': {'msg': msg,'_ts': time.time()}}})

    def toggle_terminal_feed(self, msg):
        if msg == 'on':
            self.terminal_feed_is_on = True
        elif msg == 'off':
            self.terminal_feed_is_on = False
        return self.terminal_feed_is_on
