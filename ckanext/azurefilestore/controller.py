from datetime import datetime, timedelta
import mimetypes
import os
import paste.fileapp
import webob

# Legacy SDK
# from azure.storage.blob.baseblobservice import BaseBlobService
# from azure.storage.blob import BlobPermissions

from azure.storage.blob import generate_blob_sas, AccountSasPermissions

import ckan.lib.base as base
import ckan.lib.uploader as uploader
import ckan.logic as logic
import ckan.model as model
import ckan.plugins.toolkit as toolkit

from ckan.common import _, request, c, response
from ckan.plugins.toolkit import config

from ckanext.azurefilestore.uploader import AzureUploader

import logging
log = logging.getLogger(__name__)

NotAuthorized = logic.NotAuthorized
NotFound = logic.NotFound
abort = base.abort
get_action = logic.get_action
redirect = toolkit.redirect_to


class AzureController(base.BaseController):

    def resource_download(self, id, resource_id, filename=None):
        '''
        Provide a download by either redirecting the user to the url stored or
        downloading the uploaded file from azure.
        '''
        context = {'model': model, 'session': model.Session,
                   'user': c.user or c.author, 'auth_user_obj': c.userobj}

        try:
            rsc = get_action('resource_show')(context, {'id': resource_id})
            get_action('package_show')(context, {'id': id})
        except NotFound:
            abort(404, _('Resource not found'))
        except NotAuthorized:
            abort(401, _('Unauthorized to read resource %s') % id)

        if rsc.get('url_type') == 'upload':
            upload = uploader.get_resource_uploader(rsc)
            container_name = config.get('ckanext.azurefilestore.container_name')
            container_client = upload.get_container_client(container_name)

            if filename is None:
                filename = os.path.basename(rsc['url'])

            key_path = upload.get_path(rsc['id'], filename)
            key = filename

            if key is None:
                log.warn('Key \'{0}\' not found in container \'{1}\''
                         .format(key_path, container_name))

            url_with_sas = None
            try:
                # Small workaround to manage downloading of large files
                # We are redirecting to the azure resource's public URL

                storage_account = config.get('ckanext.azurefilestore.storage_account')
                account_key = config.get('ckanext.azurefilestore.account_key')
                url = 'https://{storage_account}.blob.core.windows.net/{container_name}/{key_path}'.format(
                    storage_account=storage_account,
                    container_name=container_name,
                    key_path=key_path
                )

                # Legacy SDK
                # service = BaseBlobService(account_name=storage_account, account_key=account_key)
                # token = service.generate_blob_shared_access_signature(
                #     container_name, blob_name, 
                #     permission=BlobPermissions.READ, 
                #     expiry=datetime.utcnow() + timedelta(hours=1))

                # https://stackoverflow.com/questions/56769671/how-to-download-an-azure-blob-storage-file-via-url-in-python
                token = generate_blob_sas(
                    account_name=storage_account,
                    account_key=account_key,
                    container_name=container_name,
                    blob_name=key_path,
                    permission=AccountSasPermissions(read=True),
                    expiry=datetime.utcnow() + timedelta(hours=1)
                )
                url_with_sas = '{url}?{token}'.format(url=url, token=token)

            except Exception as ex:
                log.error(str(ex))
                # attempt fallback
                if config.get(
                        'ckanext.azurefilestore.filesystem_download_fallback',
                        False):
                    log.info('Attempting filesystem fallback for resource {0}'
                             .format(resource_id))
                    url = toolkit.url_for(
                        controller='ckanext.azurefilestore.controller:AzureController',
                        action='filesystem_resource_download',
                        id=id,
                        resource_id=resource_id,
                        filename=filename)
                    redirect(url)

                abort(404, _('Resource data not found'))

            redirect(url_with_sas)

    def filesystem_resource_download(self, id, resource_id, filename=None):
        """
        A fallback controller action to download resources from the
        filesystem. A copy of the action from
        `ckan.controllers.package:PackageController.resource_download`.
        Provide a direct download by either redirecting the user to the url
        stored or downloading an uploaded file directly.
        """
        context = {'model': model, 'session': model.Session,
                   'user': c.user or c.author, 'auth_user_obj': c.userobj}

        try:
            rsc = get_action('resource_show')(context, {'id': resource_id})
            get_action('package_show')(context, {'id': id})
        except NotFound:
            abort(404, _('Resource not found'))
        except NotAuthorized:
            abort(401, _('Unauthorized to read resource %s') % id)

        if rsc.get('url_type') == 'upload':
            upload = uploader.ResourceUpload(rsc)
            filepath = upload.get_path(rsc['id'])
            fileapp = paste.fileapp.FileApp(filepath)
            try:
                status, headers, app_iter = request.call_application(fileapp)
            except OSError:
                abort(404, _('Resource data not found'))

            response.headers.update(dict(headers))
            content_type, content_enc = mimetypes.guess_type(rsc.get('url', ''))
            if content_type:
                response.headers['Content-Type'] = content_type

            response.status = status
            return app_iter
        elif 'url' not in rsc:
            abort(404, _('No download is available'))

        redirect(str(rsc['url']))

    def uploaded_file_redirect(self, upload_to, filename):
        '''Redirect static file requests to their location on azure.'''
        storage_account = config.get('ckanext.azurefilestore.storage_account')
        # Remove last characted if it's a slash
        # if host_name[-1] == '/':
        #     host_name = host_name[:-1]

        storage_path = AzureUploader.get_storage_path(upload_to)
        filepath = os.path.join(storage_path, filename)
        redirect_url = 'https://{storage_account}.blob.core.windows.net/{container_name}/{filepath}'\
                        .format(
                            container_name=config.get('ckanext.azurefilestore.container_name'),
                            filepath=filepath,
                            storage_account=storage_account)
        redirect(redirect_url)
