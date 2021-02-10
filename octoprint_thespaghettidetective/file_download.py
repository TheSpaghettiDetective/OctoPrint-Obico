import logging
import requests
import os, sys, time
import threading
from .utils import get_tags

_logger = logging.getLogger('octoprint.plugins.thespaghettidetective')
UPLOAD_FOLDER = 'TheSpaghettiDetectiveUpload'

class FileDownloader:

    def __init__(self, plugin):
        self.plugin = plugin

    def download(self, gcode_file):
        try:
            _logger.warn('Received download command for {} '.format(gcode_file))

            if self.plugin.print_event_tracker.get_tsd_gcode_file_id() or self.plugin._printer.get_current_data().get('state', {}).get('text') != 'Operational':
                return {'error': 'Currently downloading or printing!'}

            self.plugin.print_event_tracker.set_tsd_gcode_file_id(gcode_file['id'])

            self.__ensure_storage__()
            target_path = os.path.join(self.g_code_folder, gcode_file['safe_filename'])

            print_thread = threading.Thread(target=self.__download_and_print__, args=(gcode_file,target_path))
            print_thread.daemon = True
            print_thread.start()

            return {'target_path': target_path}

        except Exception as e:
            self.plugin.sentry.captureException(tags=get_tags())

    def __download_and_print__(self, gcode_file, target_path):
        r = requests.get(gcode_file['url'], allow_redirects=True, timeout=60*30)
        r.raise_for_status()
        open(target_path, "wb").write(r.content)

        _logger.warn('Finished downloading to target_path: {}'.format(target_path))
        self.plugin._printer.select_file(target_path, False, printAfterSelect=True)

    def __ensure_storage__(self):
        self.plugin._file_manager.add_folder("local", UPLOAD_FOLDER, ignore_existing=True)
        self.g_code_folder = self.plugin._file_manager.path_on_disk("local", UPLOAD_FOLDER)
