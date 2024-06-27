#
# Copyright (c) 2022-2023 Wind River Systems, Inc.
#
# SPDX-License-Identifier: Apache-2.0
#
# All Rights Reserved.
#

""" System inventory App lifecycle operator."""

import os
import yaml

from kubernetes import client
from oslo_log import log as logging
from sysinv.common import constants
from sysinv.common import exception
from sysinv.common import kubernetes
from sysinv.common import utils as cutils
from sysinv.helm import lifecycle_base as base

from k8sapp_kubevirt.common import constants as app_constants

LOG = logging.getLogger(__name__)


class KubeVirtAppLifecycleOperator(base.AppLifecycleOperator):
    """Custom KubeVirt-specific AppLifecycleOperator class.

    This class is derived from the base AppLifecycleOperator and provides
    KubeVirt-specific lifecycle actions for applications.

    :param base.AppLifecycleOperator: The base class to inherit from.
    """
    def app_lifecycle_actions(self, context, conductor_obj, app_op, app, hook_info):
        """Perform lifecycle actions for an operation

        :param context: request context, can be None
        :param conductor_obj: conductor object, can be None
        :param app_op: AppOperator object
        :param app: AppOperator.Application object
        :param hook_info: LifecycleHookInfo object

        """

        # Define a dictionary to map values to lifecycle functions
        action_map = {
            (constants.APP_LIFECYCLE_TYPE_FLUXCD_REQUEST, constants.APP_APPLY_OP,
             constants.APP_LIFECYCLE_TIMING_PRE): self.pre_apply,
            (constants.APP_LIFECYCLE_TYPE_FLUXCD_REQUEST, constants.APP_APPLY_OP,
             constants.APP_LIFECYCLE_TIMING_POST): lambda: self.post_apply(app_op, app),
            (constants.APP_LIFECYCLE_TYPE_OPERATION, constants.APP_REMOVE_OP,
             constants.APP_LIFECYCLE_TIMING_PRE): lambda: self.pre_remove(app),
            (constants.APP_LIFECYCLE_TYPE_OPERATION, constants.APP_REMOVE_OP,
             constants.APP_LIFECYCLE_TIMING_POST): lambda: self.post_remove(app)
        }

        # Get the appropriate lifecylce function from the dictionary based on the values
        action_function = action_map.get((hook_info.lifecycle_type, hook_info.operation,
                                          hook_info.relative_timing))

        if action_function is not None:
            action_function()

        super().app_lifecycle_actions(context, conductor_obj, app_op, app, hook_info)

    def pre_apply(self):
        """Prepare KubeVirt namespaces for Helm management.

        Patches CDI and KubeVirt namespaces with labels and annotations for Helm
        before applying the KubeVirt application.
        """

        LOG.debug(f"Executing pre_apply for {app_constants.HELM_APP_KUBEVIRT} app")

        # Create a Kubernetes client object
        client_v1 = client.CoreV1Api()

        patches = [{"metadata": {"labels": {"app.kubernetes.io/managed-by": "Helm"}}},
                   {"metadata": {"annotations": {"meta.helm.sh/release-name":
                    app_constants.HELM_APP_KUBEVIRT}}},
                   {"metadata": {"annotations": {"meta.helm.sh/release-namespace":
                    app_constants.HELM_NS_KUBEVIRT}}}]

        for patch in patches:
            client_v1.patch_namespace(name=app_constants.HELM_NS_KUBEVIRT, body=patch)
            client_v1.patch_namespace(name=app_constants.HELM_NS_CDI, body=patch)

        LOG.debug(f"Patched namespaces {app_constants.HELM_NS_KUBEVIRT} \
          and {app_constants.HELM_NS_CDI}")

    def post_apply(self, app_op, app):
        """Perform post-apply actions for the KubeVirt application. """

        LOG.debug(f"Executing post_apply for {app_constants.HELM_APP_KUBEVIRT} app")

        self.update_namespace_override(app_op, app, app_constants.HELM_NS_KUBEVIRT)
        self.update_namespace_override(app_op, app, app_constants.HELM_NS_CDI)

    def update_namespace_override(self, app_op, app, namespace):
        """Update the namespace override based on Helm chart user overrides.

        This method updates the namespace label based on user overrides specified
        in the Helm chart. It ensures that the namespace label is either 'platform'
        or 'application' and may restart pods when the label changes.

        :param app_op: The AppOperator instance.
        :param app: The Application object.
        :param namespace: The namespace to update.
        """

        dbapi_instance = app_op._dbapi
        db_app_id = dbapi_instance.kube_app_get(app.name).id

        # chart overrides
        chart_overrides = self._get_helm_user_overrides(
            dbapi_instance,
            db_app_id,
            namespace)

        override_label = {}
        client_core = app_op._kube._get_kubernetesclient_core()

        # Namespaces variables
        read_namespace = client_core.read_namespace(namespace)

        # Old namespace variable
        old_namespace_label = read_namespace.metadata.labels.get(
            app_constants.HELM_CHART_COMPONENT_LABEL, None)

        if app_constants.HELM_CHART_COMPONENT_LABEL in chart_overrides:
            # User Override variables
            dict_chart_overrides = yaml.safe_load(chart_overrides)
            override_label = dict_chart_overrides.get(app_constants.HELM_CHART_COMPONENT_LABEL)

        if override_label == app_constants.HELM_CHART_COMPONENT_APPLICATION:
            read_namespace.metadata.labels.update({app_constants.HELM_CHART_COMPONENT_LABEL:
                                                   app_constants.HELM_CHART_COMPONENT_APPLICATION})
            app_op._kube.kube_patch_namespace(namespace, read_namespace)
        elif override_label == app_constants.HELM_CHART_COMPONENT_PLATFORM:
            read_namespace.metadata.labels.update({app_constants.HELM_CHART_COMPONENT_LABEL:
                                                   app_constants.HELM_CHART_COMPONENT_PLATFORM})
            app_op._kube.kube_patch_namespace(namespace, read_namespace)
        elif not override_label:
            read_namespace.metadata.labels.update({app_constants.HELM_CHART_COMPONENT_LABEL:
                                                   app_constants.HELM_CHART_COMPONENT_PLATFORM})
            app_op._kube.kube_patch_namespace(namespace, read_namespace)
        else:
            LOG.warning(f'WARNING: Namespace label {override_label} not supported')

        namespace_label = read_namespace.metadata.labels.get(
            app_constants.HELM_CHART_COMPONENT_LABEL)
        if old_namespace_label != namespace_label:
            self._delete_pods(app_op, client_core, namespace)

    def _get_helm_user_overrides(self, dbapi_instance, db_app_id, namespace):
        """Retrieve Helm user overrides for the specified namespace.

        This method attempts to retrieve Helm user overrides for the given namespace
        from the database. If no overrides are found, it creates them and returns an
        empty string.

        :param dbapi_instance: The database API instance.
        :param db_app_id: The application ID in the database.
        :param namespace: The namespace for which Helm user overrides are needed.
        :return: Helm user overrides as a string.
        """
        try:
            overrides = dbapi_instance.helm_override_get(
                app_id=db_app_id,
                name=app_constants.HELM_APP_KUBEVIRT,
                namespace=namespace,
            )
        except exception.HelmOverrideNotFound:
            values = {
                "name": app_constants.HELM_APP_KUBEVIRT,
                "namespace": namespace,
                "db_app_id": db_app_id,
            }
            overrides = dbapi_instance.helm_override_create(values=values)
        return overrides.user_overrides or ""

    def _delete_pods(self, app_op, client_core, namespace):
        """Delete pods in the specified namespace to force restart on label change.

        This method lists pods in the given namespace and deletes them with a grace period
        of 0 seconds, effectively forcing a restart when there is a label change on the namespace.

        :param app_op: The AppOperator object.
        :param client_core: The Kubernetes CoreV1Api client.
        :param namespace: The namespace in which pods should be deleted.
        """

        # pod list
        system_pods = client_core.list_namespaced_pod(namespace)

        # On namespace label change delete pods to force restart
        for pod in system_pods.items:
            app_op._kube.kube_delete_pod(
                name=pod.metadata.name,
                namespace=namespace,
                grace_periods_seconds=0
            )

    def pre_remove(self, app):
        """Pre application removal tasks.

        Due to the ordering of deletes, to prevent the namespace finalizer from
        waiting indefinitely, we need to ensure that the kubevirt and cdi custom
        resources are deleted, and the finalizer removed from the
        helmreleases.helm.toolkit.fluxcd.io resource, in the kubevirt namespace.

        :param app: The application object.
        """

        LOG.debug(f"Executing pre_remove for {app_constants.HELM_APP_KUBEVIRT} app")

        cmd = [
            'kubectl', '--kubeconfig', kubernetes.KUBERNETES_ADMIN_CONF,
            'delete', app_constants.HELM_APP_CDI_CR, '-n', app_constants.HELM_NS_CDI
        ]
        stdout, stderr = cutils.trycmd(*cmd)
        LOG.debug(f"{app.name} app: cmd={cmd} stdout={stdout} stderr={stderr}")

        cmd = [
            'kubectl', '--kubeconfig', kubernetes.KUBERNETES_ADMIN_CONF,
            'delete', app_constants.HELM_APP_KUBEVIRT_CR, '-n', app_constants.HELM_NS_KUBEVIRT
        ]
        stdout, stderr = cutils.trycmd(*cmd)
        LOG.debug(f"{app.name} app: cmd={cmd} stdout={stdout} stderr={stderr}")

    def post_remove(self, app):
        """Execute post-remove actions for the applications

        This method is responsible for performing cleanup actions after an
        application has been removed. It includes deleting Custom Resource Definitions
        (CRDs), removing symbolic links and binaries, and cleaning up directories.

        :param app: The application object.
        """

        LOG.debug(f"Executing post_remove for {app_constants.HELM_APP_KUBEVIRT} app")

        # Helm doesn't delete CRDs.  To clean up after application-remove, we need to explicitly
        # delete the CRDs.
        cmd = [
            'kubectl', '--kubeconfig', kubernetes.KUBERNETES_ADMIN_CONF,
            'delete', 'crd', app_constants.HELM_APP_CDI_CRD
        ]
        stdout, stderr = cutils.trycmd(*cmd)

        # CDI and KubeVirt CRDs are independent of each other; the CRD for CDI can be
        # safely deleted even if deleting the KubeVirt CRD fails above.
        cmd = [
            'kubectl', '--kubeconfig', kubernetes.KUBERNETES_ADMIN_CONF,
            'delete', 'crd', app_constants.HELM_APP_KUBEVIRT_CRD
        ]
        stdout, stderr = cutils.trycmd(*cmd)
        LOG.debug(f"{app.name} app: cmd={cmd} stdout={stdout} stderr={stderr}")

        # Remove virtctl sym link
        if os.path.exists(app_constants.HELM_VIRTCTL_LINK_PATH):
            os.remove(app_constants.HELM_VIRTCTL_LINK_PATH)
        else:
            LOG.warning(f"Failed to delete {app_constants.HELM_VIRTCTL_LINK_PATH}")

        # Remove virtctl binary
        if os.path.exists(app_constants.HELM_VIRTCTL_PATH):
            os.remove(app_constants.HELM_VIRTCTL_PATH)
        else:
            LOG.warning(f"Failed to delete {app_constants.HELM_VIRTCTL_PATH}")

        # Remove /var/opt/kubevirt if it is empty
        directory = os.listdir(app_constants.HELM_VIRTCTL_DIR)
        if len(directory) == 0:
            os.rmdir(app_constants.HELM_VIRTCTL_DIR)
            LOG.debug(f"Deleted directory {app_constants.HELM_VIRTCTL_DIR}")
        else:
            LOG.info(f"Directory {app_constants.HELM_VIRTCTL_DIR} is not empty \
              - will not be deleted.")
