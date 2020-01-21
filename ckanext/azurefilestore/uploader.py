import cgi
import datetime
import json
import logging
import mimetypes
import os
import uuid

import ckan.lib.munge as munge
import ckan.model as model
import ckan.plugins.toolkit as toolkit

from azure.core.exceptions import AzureError, ClientAuthenticationError, HttpResponseError, ResourceNotFoundError
from azure.storage.blob import BlobClient, BlobServiceClient, ContainerClient

if toolkit.check_ckan_version(min_version='2.7.0'):
    from werkzeug.datastructures import FileStorage as FlaskFileStorage
    ALLOWED_UPLOAD_TYPES = (cgi.FieldStorage, FlaskFileStorage)
else:
    ALLOWED_UPLOAD_TYPES = (cgi.FieldStorage)

config = toolkit.config
log = logging.getLogger(__name__)

_storage_path = None
_max_resource_size = None
_max_image_size = None


def _get_underlying_file(wrapper):
    if isinstance(wrapper, FlaskFileStorage):
        return wrapper.stream
    return wrapper.file


class AzureFileStoreException(Exception):
    pass


class BaseAzureUploader(object):

    def __init__(self):
        log.debug('initializing BaseAzureUploader')
        self.connect_str = config.get('ckanext.azurefilestore.connect_str')
        self.container_name = config.get('ckanext.azurefilestore.container_name')
        self.container_client = self.get_container_client(self.container_name)

    def get_directory(self, id, storage_path):
        directory = os.path.join(storage_path, id)
        return directory

    def get_blob_service_client(self):
        log.debug('connect_str: ' + self.connect_str)
        return BlobServiceClient.from_connection_string(self.connect_str)

    def get_container_client(self, container_name):
        '''Return an azure container, creating it if it doesn't exist.'''
        log.debug('container_name: ' + container_name)
        container_client = None
        try:
            blob_service_client = self.get_blob_service_client()
            container_client = blob_service_client.get_container_client(container_name)
            # test if container exists
            props = container_client.get_container_properties()
            log.debug('container: ' + str(props))
        except ResourceNotFoundError as e:
            log.warning(
                'Container {0} could not be found,\
                attempting to create it...'.format(container_name))
            try:
                container_client = blob_service_client.create_container(container_name)
                log.info('Container {0} succesfully created'.format(container_name))
            except AzureError as e:
                raise AzureFileStoreException('Could not create container {0}: {1}'.format(
                    container_name, str(e)))
        except ClientAuthenticationError as e:
            raise AzureFileStoreException(
                'Error authenticating with Azure for container {0}: {1}'.format(
                    container_name, str(e)))
        except HttpResponseError as e:
            raise AzureFileStoreException(
                'Error in HTTP response ({0}) while getting container {1}: {2}'.format(
                    e.status_code, container_name, str(e)))
        except AzureError as e:
            raise AzureFileStoreException(
                'Something went wrong for container {0}: {1}'.format(container_name, str(e)))

        return container_client

    def upload_to_key(self, filepath, upload_file, make_public=False):
        '''Uploads the `upload_file` to `filepath` on `self.container_client`.'''
        upload_file.seek(0)
        try:
            blob_service_client = self.get_blob_service_client()
            blob_client = blob_service_client.get_blob_client(
                container=self.container_name, blob=filepath)
            blob_client.upload_blob(upload_file)
            log.info("Succesfully uploaded {0} to Azure!".format(filepath))
        except Exception as e:
            log.error('Something went wrong while uploading: {0}'.format(str(e)))
            raise e

    def clear_key(self, filepath):
        '''Deletes the contents of the key at `filepath` on `self.container_client`.'''
        try:
            blob_service_client = self.get_blob_service_client()
            blob_client = blob_service_client.get_blob_client(
                container=self.container_name, blob=filepath)
            blob_client.delete_blob()
            log.info("Succesfully deleted {0}!".format(filepath))
        except Exception as e:
            log.error('Something went wrong while deleting: {0}'.format(str(e)))
            raise e


class AzureUploader(BaseAzureUploader):
    '''
    An uploader class to replace local file storage with Azure Blob Storage
    for general files.
    '''

    def __init__(self, upload_to, old_filename=None):
        '''Setup the uploader. Additional setup is performed by
        `update_data_dict()`, and actual uploading is performed by `upload()`.
        Create a storage path in the format:
        <ckanext.azurefilestore.storage_path>/storage/uploads/<upload_to>/
        '''
        super(AzureUploader, self).__init__()

        self.storage_path = self.get_storage_path(upload_to)

        self.filename = None
        self.filepath = None

        self.old_filename = old_filename
        if old_filename:
            self.old_filepath = os.path.join(self.storage_path, old_filename)

    @classmethod
    def get_storage_path(cls, upload_to):
        path = config.get('ckanext.azurefilestore.storage_path', '')
        return os.path.join(path, 'storage', 'uploads', upload_to)

    def update_data_dict(self, data_dict, url_field, file_field, clear_field):
        '''Manipulate data from the data_dict. This needs to be called before it
        reaches any validators.
        `url_field` is the name of the field where the upload is going to be.
        `file_field` is name of the key where the FieldStorage is kept (i.e
        the field where the file data actually is).
        `clear_field` is the name of a boolean field which requests the upload
        to be deleted.
        '''
        self.url = data_dict.get(url_field, '')
        self.clear = data_dict.pop(clear_field, None)
        self.file_field = file_field
        self.upload_field_storage = data_dict.pop(file_field, None)

        if not self.storage_path:
            return

        if isinstance(self.upload_field_storage, ALLOWED_UPLOAD_TYPES):
            self.filename = self.upload_field_storage.filename
            self.filename = str(datetime.datetime.utcnow()) + self.filename
            self.filename = munge.munge_filename_legacy(self.filename)
            self.filepath = os.path.join(self.storage_path, self.filename)
            data_dict[url_field] = self.filename
            self.upload_file = _get_underlying_file(self.upload_field_storage)
        elif self.old_filename and not self.old_filename.startswith('http'):
            # keep the file if there has been no change
            if not self.clear:
                data_dict[url_field] = self.old_filename
            if self.clear and self.url == self.old_filename:
                data_dict[url_field] = ''

    def upload(self, max_size=2):
        '''Actually upload the file.
        This should happen just before a commit but after the data has been
        validated and flushed to the db. This is so we do not store anything
        unless the request is actually good.
        max_size is size in MB maximum of the file'''

        # If a filename has been provided (a file is being uploaded), write the
        # file to the appropriate key in the azure container.
        if self.filename:
            self.upload_to_key(self.filepath, self.upload_file,
                               make_public=True)
            self.clear = True

        if (self.clear and self.old_filename
                and not self.old_filename.startswith('http')):
            self.clear_key(self.old_filepath)


class AzureResourceUploader(BaseAzureUploader):
    '''
    An uploader class to replace local file storage with Azure Blob Storage
    for resource files.
    '''

    def __init__(self, resource):
        '''Setup the resource uploader. Actual uploading is performed by
        `upload()`.
        Create a storage path in the format:
        <ckanext.azurefilestore.storage_path>/resources/
        '''
        super(AzureResourceUploader, self).__init__()
        path = config.get('ckanext.azurefilestore.storage_path', '')
        self.storage_path = os.path.join(path, 'resources')
        self.filename = None
        self.old_filename = None

        upload_field_storage = resource.pop('upload', None)
        self.clear = resource.pop('clear_upload', None)

        if isinstance(upload_field_storage, ALLOWED_UPLOAD_TYPES):
            self.filename = upload_field_storage.filename
            self.filename = munge.munge_filename(self.filename)
            resource['url'] = self.filename
            resource['url_type'] = 'upload'
            resource['last_modified'] = datetime.datetime.utcnow()
            self.mimetype = resource.get('mimetype')
            if not self.mimetype:
                try:
                    self.mimetype = resource['mimetype'] = mimetypes.guess_type(self.filename, strict=False)[0]
                except Exception:
                    pass
            self.upload_file = _get_underlying_file(upload_field_storage)
        elif self.clear and resource.get('id'):
            # New, not yet created resources can be marked for deletion if the
            # user cancels an upload and enters a URL instead.
            old_resource = model.Session.query(model.Resource) \
                .get(resource['id'])
            self.old_filename = old_resource.url
            resource['url_type'] = ''

    def get_path(self, id, filename):
        '''Return the key used for this resource in azure.
        Keys are in the form:
        <ckanext.azurefilestore.storage_path>/resources/<resource id>/<filename>
        e.g.:
        my_storage_path/resources/165900ba-3c60-43c5-9e9c-9f8acd0aa93f/data.csv
        '''
        directory = self.get_directory(id, self.storage_path)
        filepath = os.path.join(directory, filename)
        return filepath

    def upload(self, id, max_size=10):
        '''Upload the file to azure.'''

        # If a filename has been provided (a file is being uploaded) write the
        # file to the appropriate key in the azure container.
        if self.filename:
            filepath = self.get_path(id, self.filename)
            self.upload_to_key(filepath, self.upload_file)

        # The resource form only sets `self.clear` (via the input clear_upload)
        # to True when an uploaded file is not replaced by another uploaded
        # file, only if it is replaced by a link. If the uploaded file is
        # replaced by a link, we should remove the previously uploaded file to
        # clean up the file system.
        if self.clear and self.old_filename:
            filepath = self.get_path(id, self.old_filename)
            self.clear_key(filepath)
