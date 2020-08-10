# sample2_pools_and_resourcefiles.py Code Sample
#
# Copyright (c) Microsoft Corporation
#
# All rights reserved.
#
# MIT License
#
# Permission is hereby granted, free of charge, to any person obtaining a
# copy of this software and associated documentation files (the "Software"),
# to deal in the Software without restriction, including without limitation
# the rights to use, copy, modify, merge, publish, distribute, sublicense,
# and/or sell copies of the Software, and to permit persons to whom the
# Software is furnished to do so, subject to the following conditions:
#
# The above copyright notice and this permission notice shall be included in
# all copies or substantial portions of the Software.
#
# THE SOFTWARE IS PROVIDED *AS IS*, WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
# IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
# FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
# AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
# LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING
# FROM, OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER
# DEALINGS IN THE SOFTWARE.

from __future__ import print_function
try:
    import configparser
except ImportError:
    import ConfigParser as configparser
import datetime
import os

import azure.storage.blob as azureblob
import azure.batch._batch_service_client as batch
import azure.batch.batch_auth as batchauth
import azure.batch.models as batchmodels
from azure.common.credentials import ServicePrincipalCredentials

import common.helpers


def create_pool(batch_client, block_blob_client, pool_id, vm_size, vm_count):
    """Creates an Azure Batch pool with the specified id.

    :param batch_client: The batch client to use.
    :type batch_client: `batchserviceclient.BatchServiceClient`
    :param block_blob_client: The storage block blob client to use.
    :type block_blob_client: `azure.storage.blob.BlockBlobService`
    :param str pool_id: The id of the pool to create.
    :param str vm_size: vm size (sku)
    :param int vm_count: number of vms to allocate
    """

    #application_packages = [
    #    batchmodels.ApplicationPackageReference(application_id='Regridv3', version='1.0.0'),
    #    batchmodels.ApplicationPackageReference(application_id='us_5km', version='1.0.0'),
    #    batchmodels.ApplicationPackageReference(application_id='us_800m_t6', version='1.0.0'),
    #]
    sku_to_use, image_ref_to_use = \
        common.helpers.select_latest_verified_vm_image_with_node_agent_sku(
            batch_client, 'Canonical', 'UbuntuServer', '18.04')
    #image_ref_to_use = batchmodels.ImageReference(
    #    publisher = 'microsoftwindowsserver', offer = 'windowsserver', sku = '2019-datacenter', version = 'latest'
    #)
    #sku_to_use = "batch.node.windows amd64"

    pool = batchmodels.PoolAddParameter(
        id=pool_id,
        virtual_machine_configuration=batchmodels.VirtualMachineConfiguration(
            image_reference=image_ref_to_use,
            node_agent_sku_id=sku_to_use),
        vm_size=vm_size,
        max_tasks_per_node=_POOL_MAX_TASKS_PER_NODE,
        #application_package_references=application_packages,
        target_dedicated_nodes=vm_count)

    common.helpers.create_pool_if_not_exist(batch_client, pool)


def submit_job_and_add_task(batch_client, block_blob_client, job_id, pool_id, storage_account_name):
    """Submits a job to the Azure Batch service and adds
    a task that runs a python script.

    :param batch_client: The batch client to use.
    :type batch_client: `batchserviceclient.BatchServiceClient`
    :param block_blob_client: The storage block blob client to use.
    :type block_blob_client: `azure.storage.blob.BlockBlobService`
    :param str job_id: The id of the job to create.
    :param str pool_id: The id of the pool to use.
    """
    job = batchmodels.JobAddParameter(
        id=job_id,
        pool_info=batchmodels.PoolInformation(pool_id=pool_id))

    batch_client.job.add(job)

    output_container_sas = common.helpers.create_container_and_create_sas(
        block_blob_client,
        job_id,
        azureblob.BlobPermissions.WRITE,
        expiry=None,
        timeout=120)

    output_container_sas_url = 'https://{}.blob.core.windows.net/{}?{}'.format(
        storage_account_name, job_id, output_container_sas)

    app_file_list= get_resource_file_list_from_container(block_blob_client, _APP_CONTAINER_NAME)

    blob_list = block_blob_client.list_blobs(_RESOURCE_CONTAINER_NAME)
    for blob in blob_list:
        (blob_base_name, blob_extension) = os.path.splitext(blob.name)
        output_file_name = f"{blob_base_name}_out{blob_extension}"
        command_line = f"{_APP_EXE_NAME} {_APP_EXTRA_ARGS} {blob.name} {output_file_name}"
        task_id = f"{_APP_EXE_NAME}_{blob_base_name}_Task"
        resource_sas_url = common.helpers.create_sas_url(block_blob_client, _RESOURCE_CONTAINER_NAME, blob.name,
            azureblob.BlobPermissions.READ, datetime.datetime.utcnow() + datetime.timedelta(hours=1))
        resource_file = batchmodels.ResourceFile(file_path=blob.name, http_url=resource_sas_url)
        print(resource_sas_url)
        print(app_file_list)

        print(f"Creating task ({task_id}): " + command_line)
        output_file = batchmodels.OutputFile(
            file_pattern=output_file_name,
            destination=batchmodels.OutputFileDestination(
                container=batchmodels.OutputFileBlobContainerDestination(
                    container_url=output_container_sas_url)),
            upload_options=batchmodels.OutputFileUploadOptions(
                upload_condition=batchmodels.
                OutputFileUploadCondition.task_completion))

        task = batchmodels.TaskAddParameter(
            id=task_id,
            command_line=command_line,
            resource_files=app_file_list + [resource_file],
            output_files=[output_file])

        batch_client.task.add(job_id=job.id, task=task)

def get_resource_file_list_from_container(block_blob_client, container):
    """Creates a Resource File for each blob in the storage container

    :param block_blob_client: The storage block blob client to use.
    :type block_blob_client: `azure.storage.blob.BlockBlobService`
    :param str container: The name of the storage container
    """
    blob_list = block_blob_client.list_blobs(container)
    result = []
    for blob in blob_list:
        print("appending " + blob.name)
        sas_url = common.helpers.create_sas_url(block_blob_client, container, blob.name,
            azureblob.BlobPermissions.READ, datetime.datetime.utcnow() + datetime.timedelta(hours=1))

        result.append(batchmodels.ResourceFile(file_path=blob.name, http_url=sas_url))
    return result

def execute_sample(global_config, sample_config):
    """Executes the sample with the specified configurations.

    :param global_config: The global configuration to use.
    :type global_config: `configparser.ConfigParser`
    :param sample_config: The sample specific configuration to use.
    :type sample_config: `configparser.ConfigParser`
    """

    credentials = batchauth.SharedKeyCredentials(
        batch_account_name,
        batch_account_key)
    #credentials = ServicePrincipalCredentials(
    #    client_id=aad_client_id,
    #    secret=aad_client_secret,
    #    tenant=aad_tenant_id,
    #    resource="https://batch.core.windows.net/"
    #)


    batch_client = batch.BatchServiceClient(
        credentials,
        batch_url=batch_service_url)

    # Retry 5 times -- default is 3
    batch_client.config.retry_policy.retries = 5

    block_blob_client = azureblob.BlockBlobService(
        account_name=storage_account_name,
        account_key=storage_account_key,
        endpoint_suffix=storage_account_suffix)


    job_id = common.helpers.generate_unique_resource_name(
        "poolsandresourcefilesjob")

    try:
        create_pool(
            batch_client,
            block_blob_client,
            pool_id,
            pool_vm_size,
            pool_vm_count)

        submit_job_and_add_task(
            batch_client, block_blob_client,
            job_id, pool_id, storage_account_name)

        common.helpers.wait_for_tasks_to_complete(
            batch_client,
            job_id,
            datetime.timedelta(minutes=25))

        tasks = batch_client.task.list(job_id)
        task_ids = [task.id for task in tasks]

        common.helpers.print_task_output(batch_client, job_id, task_ids)
        print("Completed job_id:" + job_id)
    finally:
        # clean up
        if should_delete_container:
            block_blob_client.delete_container(
                _RESOURCE_CONTAINER_NAME,
                fail_not_exist=False)
        if should_delete_job:
            print("Deleting job: ", job_id)
            batch_client.job.delete(job_id)
        if should_delete_pool:
            print("Deleting pool: ", pool_id)
            batch_client.pool.delete(pool_id)


if __name__ == '__main__':
    global_config = configparser.ConfigParser()
    global_config.read(common.helpers._SAMPLES_CONFIG_FILE_NAME)

    sample_config = configparser.ConfigParser()
    sample_config.read(
        os.path.splitext(os.path.basename(__file__))[0] + '.cfg')

    _APP_EXTRA_ARGS = sample_config.get('DEFAULT', 'app_extra_args')
    _APP_CONTAINER_NAME = sample_config.get('DEFAULT', 'app_container_name')
    _APP_EXE_NAME = sample_config.get('DEFAULT', 'app_exe_name')
    _RESOURCE_CONTAINER_NAME = sample_config.get('DEFAULT', 'resource_container_name')
    _OUTPUT_FILE_PATTERN = sample_config.get('DEFAULT', 'output_file_pattern')
    _POOL_MAX_TASKS_PER_NODE = sample_config.get('DEFAULT', 'pool_max_tasks_per_node')

    # Set up the configuration
    batch_account_key = global_config.get('Batch', 'batchaccountkey')
    batch_account_name = global_config.get('Batch', 'batchaccountname')
    batch_service_url = global_config.get('Batch', 'batchserviceurl')

    storage_account_key = global_config.get('Storage', 'storageaccountkey')
    storage_account_name = global_config.get('Storage', 'storageaccountname')
    storage_account_suffix = global_config.get('Storage', 'storageaccountsuffix')
    
    should_delete_container = sample_config.getboolean('DEFAULT', 'shoulddeletecontainer')
    should_delete_job = sample_config.getboolean('DEFAULT', 'shoulddeletejob')
    should_delete_pool = sample_config.getboolean('DEFAULT', 'shoulddeletepool')
    pool_vm_size = sample_config.get('DEFAULT', 'poolvmsize')
    pool_vm_count = sample_config.getint('DEFAULT', 'poolvmcount')
    pool_id = sample_config.get('DEFAULT', 'pool_id')

    # Print the settings we are running with
    common.helpers.print_configuration(global_config)
    common.helpers.print_configuration(sample_config)
    execute_sample(global_config, sample_config)
