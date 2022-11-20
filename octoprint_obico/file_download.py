import logging
import requests
import os
import sys
import time
import threading
import octoprint.server
from octoprint.filemanager.util import AbstractFileWrapper
import io

_logger = logging.getLogger('octoprint.plugins.obico')
UPLOAD_FOLDER = 'ObicoUpload'


class RequestFileWrapper(AbstractFileWrapper):

    def __init__(self, filename, req):
        AbstractFileWrapper.__init__(self, filename)
        self.req = req

    def save(self, path, permissions=None):
        with open(path, 'wb') as f:
            f.write(self.req.content)

        if permissions is not None:
            os.chmod(path, permissions)

    def stream(self):
        return io.StringIO(self.req.content)


class FileDownloader:

    def __init__(self, plugin, _print_job_tracker):
        self.plugin = plugin
        self._print_job_tracker = _print_job_tracker

    def download(self, gcode_file):
        try:
            _logger.warning(
                'Received download command for {} '.format(gcode_file))

            if self._print_job_tracker.get_obico_gcode_file() or self.plugin._printer.get_current_data().get('state', {}).get('text') != 'Operational':
                return {'error': 'Currently downloading or printing!'}

            self.__ensure_storage__()

            safe_filename = octoprint.server.fileManager.sanitize_name('local', gcode_file['safe_filename'])
            target_path = os.path.join(self.g_code_folder, safe_filename)
            self._print_job_tracker.set_obico_gcode_file({'id': gcode_file['id'], 'filename': safe_filename})

            print_thread = threading.Thread(target=self.__download_and_print__, args=(
                gcode_file, target_path, gcode_file['filename']))
            print_thread.daemon = True
            print_thread.start()

            return {'target_path': target_path}

        except Exception as e:
            self.plugin.sentry.captureException()

    def __download_and_print__(self, gcode_file, target_path, display_filename):
        try:
            _logger.warning(
                'Downloading to target_path: {}'.format(target_path))
            r = requests.get(gcode_file['url'],
                             allow_redirects=True, timeout=60*30)
            r.raise_for_status()
            _logger.warning(
                'Finished downloading to target_path: {}'.format(target_path))

            file_object = RequestFileWrapper(display_filename, r)
            octoprint.server.fileManager.add_file(
                'local',
                target_path,
                file_object,
                links=None,
                allow_overwrite=True,
                display=display_filename,
            )
            self.plugin._printer.select_file(
                target_path, False, printAfterSelect=True)
        except Exception:
            self.plugin.sentry.captureException()

    def __ensure_storage__(self):
        self.plugin._file_manager.add_folder(
            "local", UPLOAD_FOLDER, ignore_existing=True)
        self.g_code_folder = self.plugin._file_manager.path_on_disk(
            "local", UPLOAD_FOLDER)
