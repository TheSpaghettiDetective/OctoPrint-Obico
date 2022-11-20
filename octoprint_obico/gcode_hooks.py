import threading
import logging
import re

_logger = logging.getLogger('octoprint.plugins.obico')


class GCodeHooks:

    def __init__(self, plugin, _print_job_tracker):
        self.plugin = plugin
        self._print_job_tracker = _print_job_tracker
        self.num_gcode_until_next_filament_change = -1

    def queuing_gcode(self, comm_instance, phase, cmd, cmd_type, gcode, subcode=None, tags=None, *args, **kwargs):
        self.plugin.pause_resume_sequence.track_gcode(comm_instance, phase, cmd, cmd_type, gcode, subcode=None, tags=None, *args, **kwargs)
        self.check_for_filament_change(gcode=gcode)

    def received_gcode(self, comm, line, *args, **kwargs):
        self.check_for_filament_change(line=line)
        return line

    def check_for_filament_change(self, gcode=None, line=None):
        self.num_gcode_until_next_filament_change -= 1
        if self.num_gcode_until_next_filament_change > 0:
            return

        # TODO: This is to be replaced by OctoPrint's FilamentChange event once our PR is accepted
        # https://marlinfw.org/docs/gcode/M600.html

        filament_change_event = False
        if gcode and gcode in ('M600', 'M701' or 'M702'):
            filament_change_event = True
        if line:
            lineLower = line.lower()
            if "m600" in lineLower or "fsensor_update" in lineLower or "paused for user" in lineLower or "action:paused" in lineLower:
                filament_change_event = True

        if filament_change_event:
            self.num_gcode_until_next_filament_change = 50  # 50 gcode (sent+received) before we can send another filament change event
            event_payload = self._print_job_tracker.on_event(self.plugin, 'FilamentChange', None)
            if event_payload:
                self.plugin.post_update_to_server(data=event_payload)
