import sys
import ckan.plugins.toolkit as toolkit
from ckan.plugins.toolkit import config

from azure.storage.blob import BlobServiceClient, BlobClient, ContainerClient


class TestConnection(toolkit.CkanCommand):
    '''CKAN Azure FileStore utilities
    Usage:
        paster azure check-config
            Checks if the configuration entered in the ini file is correct
    '''
    summary = __doc__.split('\n')[0]
    usage = __doc__
    min_args = 1

    def command(self):
        self._load_config()
        if not self.args:
            print self.usage
        elif self.args[0] == 'check-config':
            self.check_config()

    def check_config(self):
        exit = False
        for key in ('ckanext.azurefilestore.connect_str',
                    'ckanext.azurefilestore.container_name',
                    'ckanext.azurefilestore.storage_account',
                    'ckanext.azurefilestore.account_key'):
            if not config.get(key):
                print 'You must set the "{0}" option in your ini file'.format(key)
                exit = True
        if exit:
            sys.exit(1)

        print 'All configuration options defined'
        connect_str = config.get('ckanext.azurefilestore.connect_str')
        container_name = config.get('ckanext.azurefilestore.container_name')

        blob_service_client = BlobServiceClient.from_connection_string(connect_str)

        # Check if container exists
        try:
            container_client = blob_service_client.get_container_client(container_name)
        except Exception as e:
            print 'An error was found while getting or creating the container:'
            print str(e)
            sys.exit(1)
        
        print 'Configuration OK!'
