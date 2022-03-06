import threading
import logging
import re

_logger = logging.getLogger('octoprint.plugins.thespaghettidetective')


class GCodeHooks:

    def __init__(self, plugin):
        self.plugin = plugin

    def queuing_gcode(self, comm_instance, phase, cmd, cmd_type, gcode, subcode=None, tags=None, *args, **kwargs):
        self.plugin.pause_resume_sequence.track_gcode(comm_instance, phase, cmd, cmd_type, gcode, subcode=None, tags=None, *args, **kwargs)

    def received_gcode(self, comm, line, *args, **kwargs):
        return line