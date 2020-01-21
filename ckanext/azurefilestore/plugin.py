import ckan.plugins as plugins
import ckan.plugins.toolkit as toolkit
from routes.mapper import SubMapper

from ckanext.azurefilestore.uploader import AzureResourceUploader, AzureUploader, BaseAzureUploader


class AzurefilestorePlugin(plugins.SingletonPlugin):
    plugins.implements(plugins.IConfigurer)
    plugins.implements(plugins.IConfigurable)
    plugins.implements(plugins.IRoutes, inherit=True)
    plugins.implements(plugins.IUploader)

    # IConfigurer

    def update_config(self, config_):
        toolkit.add_template_directory(config_, 'templates')
        toolkit.add_public_directory(config_, 'public')
        toolkit.add_resource('fanstatic', 'azurefilestore')

    # IConfigurable

    def configure(self, config):
        # Certain config options must exists for the plugin to work. Raise an
        # exception if they're missing.
        missing_config = '{0} is not configured. Please amend your .ini file.'
        config_options = (
            'ckanext.azurefilestore.connect_str',
            'ckanext.azurefilestore.container_name',
            'ckanext.azurefilestore.storage_account',
            'ckanext.azurefilestore.account_key'
        )
        for option in config_options:
            if not config.get(option, None):
                raise RuntimeError(missing_config.format(option))

        # Check that options actually work, if not exceptions will be raised
        if toolkit.asbool(
                config.get('ckanext.azurefilestore.check_access_on_startup',
                           True)):
            BaseAzureUploader().get_container_client(
                config.get('ckanext.azurefilestore.container_name'))

    # IUploader

    def get_resource_uploader(self, data_dict):
        '''Return an uploader object used to upload resource files.'''
        return AzureResourceUploader(data_dict)

    def get_uploader(self, upload_to, old_filename=None):
        '''Return an uploader object used to upload general files.'''
        return AzureUploader(upload_to, old_filename)

    # IRoutes

    def before_map(self, map):
        with SubMapper(map, controller='ckanext.azurefilestore.controller:AzureController') as m:
            # Override the resource download links
            m.connect('resource_download',
                      '/dataset/{id}/resource/{resource_id}/download',
                      action='resource_download')
            m.connect('resource_download',
                      '/dataset/{id}/resource/{resource_id}/download/{filename}',
                      action='resource_download')

            # fallback controller action to download from the filesystem
            m.connect('filesystem_resource_download',
                      '/dataset/{id}/resource/{resource_id}/fs_download/{filename}',
                      action='filesystem_resource_download')

            # Intercept the uploaded file links (e.g. group images)
            m.connect('uploaded_file', '/uploads/{upload_to}/{filename}',
                      action='uploaded_file_redirect')

        return map
