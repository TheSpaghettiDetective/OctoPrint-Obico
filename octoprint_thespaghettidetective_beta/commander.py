import threading
import re

class Commander:

    def __init__(self):
        self.mutex = threading.RLock()
        self.last_g9x = 'G90'
        self.last_m8x = 'M82'
        self.job_is_on_hold = False

    def track_gcode(self, comm_instance, phase, cmd, cmd_type, gcode, subcode=None, tags=None, *args, **kwargs):
        if 'TSD' in tags:
            return

        with self.mutex:
            if re.match('G9[01]', cmd, flags=re.IGNORECASE):
                self.last_g9x = cmd
                print('commander setting: {}'.format(self.last_g9x))
            if re.match('M8[23]', cmd, flags=re.IGNORECASE):
                self.last_m8x = cmd
                print('commander setting: {}'.format(self.last_m8x))

    def put_on_hold(self, printer):
        with self.mutex:
            printer.set_job_on_hold(True)
            self.job_is_on_hold = True

        self.commands(printer, [
            'G91',
            'M83',
            'G1 E-5.0',
            'G1 Z5.0',
            ])

    def resume_from_hold(self, printer):
        self.commands(printer, [
            'G91',
            'M83',
            'G1 Z-5.0',
            'G1 E5.0',
            self.last_g9x,
            self.last_m8x,
            ])

        with self.mutex:
            printer.set_job_on_hold(False)
            self.job_is_on_hold = False


    def release_hold_if_needed(self, printer):
        with self.mutex:
            if self.job_is_on_hold:
                self.commands(printer, [
                    self.last_g9x,
                    self.last_m8x,
                    ])
                printer.set_job_on_hold(False)
                self.job_is_on_hold = False


    # private methods

    def commands(self, printer, cmds):
        printer.commands(cmds, tags=set(['TSD']))

