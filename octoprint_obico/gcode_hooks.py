import threading
import logging
import re

_logger = logging.getLogger('octoprint.plugins.obico')


class GCodeHooks:

    def __init__(self, plugin):
        self.plugin = plugin

    def queuing_gcode(self, comm_instance, phase, cmd, cmd_type, gcode, subcode=None, tags=None, *args, **kwargs):
        self.plugin.pause_resume_sequence.track_gcode(comm_instance, phase, cmd, cmd_type, gcode, subcode=None, tags=None, *args, **kwargs)

        if gcode and gcode in ('M600', 'M701' or 'M702'):
            self.plugin.post_filament_change_event()

    def received_gcode(self, comm, line, *args, **kwargs):

        # credit: https://github.com/QuinnDamerell/OctoPrint-OctoEverywhere/blob/ef37e6c9ce6798e8af54a5fd81215d430c05bfad/octoprint_octoeverywhere/__init__.py#L272
        lineLower = line.lower()
        if "m600" in lineLower or ("fsensor_update" in lineLower and "m600" in lineLower) \
            or "paused for user" in lineLower or "// action:paused" in lineLower:

            self.plugin.post_filament_change_event()

        return line