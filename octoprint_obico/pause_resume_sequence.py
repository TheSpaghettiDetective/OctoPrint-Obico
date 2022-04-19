import threading
import logging
import re

_logger = logging.getLogger('octoprint.plugins.obico')


class PauseResumeGCodeSequence:

    def __init__(self):
        self.mutex = threading.RLock()
        self.pause_scripts = []
        self.resume_scripts = []

        self.last_g9x = 'G90'
        self.last_m8x = 'M82'

    def track_gcode(self, comm_instance, phase, cmd, cmd_type, gcode, subcode=None, tags=None, *args, **kwargs):
        with self.mutex:
            if re.match('G9[01]', cmd, flags=re.IGNORECASE):
                self.last_g9x = cmd
            if re.match('M8[23]', cmd, flags=re.IGNORECASE):
                self.last_m8x = cmd

    def script_hook(self, comm, script_type, script_name, *args, **kwargs):
        if script_type == "gcode" and script_name == "afterPrintPaused":
            _logger.debug('afterPrintPaused hook called. Returning scripts %s' % self.pause_scripts)

            pause_scripts = self.pause_scripts
            self.pause_scripts = []
            return None, pause_scripts

        if script_type == "gcode" and script_name == "beforePrintResumed":
            _logger.debug('beforePrintResumed hook called. Returning scripts %s' % self.resume_scripts)

            resume_scripts = self.resume_scripts
            self.resume_scripts = []
            return resume_scripts, None

        return None

    def prepare_to_pause(self, printer, printer_profile, retract=0, lift_z=0, tools_off=False, bed_off=False):
        with self.mutex:
            self.pause_scripts = []
            self.resume_scripts = []

            if retract > 0 or lift_z > 0:

                # Scripts for retract and lift to be returned from afterPrintPaused
                self.pause_scripts.extend([    # Set to relative mode
                    'G91',
                    'M83',
                ])

                if retract > 0:    # Retract before lift on pause
                    self.pause_scripts.extend([
                        'G1 E-%g' % retract,
                    ])

                if lift_z > 0:
                    self.pause_scripts.extend([
                        'G1 Z%g' % lift_z,
                    ])

                self.pause_scripts.extend([     # restore previous mode
                    self.last_g9x,
                    self.last_m8x,
                ])

                # Scripts for retract and lift to be returned from beforePrintResumed
                self.resume_scripts.extend([
                    'G91',
                    'M83',
                ])

                if lift_z > 0:     # Drop before de-reract on resume
                    self.resume_scripts.extend([
                        'G1 Z-%g' % lift_z,
                    ])

                if retract > 0:
                    self.resume_scripts.extend([
                        'G1 E%g' % retract,
                    ])

                self.resume_scripts.extend([     # restore previous mode
                    self.last_g9x,
                    self.last_m8x,
                ])

            current_temps = printer.get_current_temperatures()

            if tools_off:

                extruder = printer_profile.get('extruder')
                extruder_count = extruder.get('count', 1)

                # When printer has multiple extruders, and they are not shared (MMU has shared extruders)
                if extruder_count > 1 and not extruder.get('sharedNozzle', False):
                    for tool_num in range(extruder_count):
                        heater = 'tool%d' % tool_num
                        if heater in current_temps and current_temps[heater]['target'] is not None and current_temps[heater]['offset'] is not None:
                            target_temp = current_temps[heater]['target'] + current_temps[heater]['offset']
                            self.pause_scripts.append('M104 T%d S0' % (tool_num))     # On pause, temp should come after retract and lift
                            self.resume_scripts.insert(0, 'M109 T%d S%d' % (tool_num, target_temp))  # on resume, temp should come before de-retract and drop
                else:
                    heater = 'tool0'
                    if heater in current_temps and current_temps[heater]['target'] is not None and current_temps[heater]['offset'] is not None:
                        target_temp = current_temps[heater]['target'] + current_temps[heater]['offset']
                        self.pause_scripts.append('M104 S0')
                        self.resume_scripts.insert(0, 'M109 S%d' % (target_temp))

            if bed_off:

                heater = 'bed'
                if heater in current_temps and current_temps[heater]['target'] is not None and current_temps[heater]['offset'] is not None:
                    target_temp = current_temps[heater]['target'] + current_temps[heater]['offset']
                    self.pause_scripts.append('M140 S0')
                    self.resume_scripts.insert(0, 'M190 S%d' % (target_temp))

        _logger.debug('prepare_to_pause called.')
        _logger.debug('pause_scripts: {}' % self.pause_scripts)
        _logger.debug('resume_scripts: {}' % self.resume_scripts)
