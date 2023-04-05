import logging
import requests
import os
import sys
import time
import threading
from octoprint.filemanager.util import AbstractFileWrapper
import io

from .utils import server_request

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

    def download(self, g_code_file):
        try:
            _logger.warning(
                'Received download command for {} '.format(g_code_file))

            if self._print_job_tracker.get_obico_g_code_file_id() or self.plugin._printer.get_current_data().get('state', {}).get('text') != 'Operational':
                return {'error': 'Currently downloading or printing!'}

            # _print_job_tracker.obico_g_code_file_id is used as a latch to prevent double-clicking
            self._print_job_tracker.set_obico_g_code_file_id(g_code_file['id'])

            print_thread = threading.Thread(target=self.__download_and_print__, args=(g_code_file,))
            print_thread.daemon = True
            print_thread.start()

            return {'target_path': g_code_file['safe_filename']}

        except Exception as e:
            self.plugin.sentry.captureException()
            return {'error': str(e)}

    def __download_and_print__(self, g_code_file):
        try:
            self.__ensure_storage__()

            _logger.debug('Start downloading...' + g_code_file['url'])
            display_filename = g_code_file['filename']
            r = requests.get(g_code_file['url'], allow_redirects=True, timeout=60*30)
            _logger.debug('Done downloading...' + g_code_file['url'])
            r.raise_for_status()

            octoprint_storage = 'local'
            file_object = RequestFileWrapper(display_filename, r)


            target_path = os.path.join(UPLOAD_FOLDER, g_code_file['safe_filename'])
            target_path = self.plugin._file_manager.add_file(octoprint_storage, target_path, file_object, links=None, allow_overwrite=True, display=display_filename,)
            _logger.debug('Start getting metadata...')
            md5_hash = self.plugin._file_manager.get_metadata(path=target_path, destination=octoprint_storage)['hash']
            _logger.debug('Done getting metadata...')

            g_code_data = dict(agent_signature='md5:{}'.format(md5_hash), safe_filename=os.path.basename(target_path))
            resp = server_request('PATCH', '/api/v1/octo/g_code_files/{}/'.format(g_code_file['id']), self.plugin, timeout=60, data=g_code_data, headers=self.plugin.auth_headers())

            self.plugin._printer.select_file(target_path, False, printAfterSelect=True)

        except Exception:
            self.plugin.sentry.captureException()

    def __ensure_storage__(self):
        self.plugin._file_manager.add_folder(
            "local", UPLOAD_FOLDER, ignore_existing=True)
