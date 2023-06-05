class FileOperations:
    def __init__(self, plugin):
        self.plugin = plugin


    def check_filepath_and_agent_signature(self, filepath, server_signature):
        try:
            md5_hash = self.plugin._file_manager.get_metadata(path=filepath, destination='local').get('hash')
            if md5_hash:
                filepath_signature = 'md5:{}'.format(md5_hash)
                return filepath_signature == server_signature # check if signatures match -> Boolean
            else:
                return False
            
        except Exception as e:
            return False
            

    def start_printer_local_print(self, file_to_print):
        ret_value = {}

        filepath = file_to_print['url']
        file_is_not_modified = self.check_filepath_and_agent_signature(filepath, file_to_print['agent_signature'])

        if file_is_not_modified is False:
            ret_value['error'] = 'File has been modified! Did you move, delete, or overwrite this file?'
            return ret_value
        else:
            ret_value = 'Success'
            self.plugin._printer.select_file(filepath, False, printAfterSelect=True)
            return ret_value
