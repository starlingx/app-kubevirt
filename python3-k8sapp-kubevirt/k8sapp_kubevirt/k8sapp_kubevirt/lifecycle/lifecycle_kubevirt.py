#
# Copyright (c) 2022-2023, 2026 Wind River Systems, Inc.
#
# SPDX-License-Identifier: Apache-2.0
#
# All Rights Reserved.
#

""" System inventory App lifecycle operator."""

import os
import yaml

from oslo_log import log as logging
from sysinv.common import constants
from sysinv.common import exception
from sysinv.common import kubernetes
from sysinv.common import utils as cutils
from sysinv.helm import lifecycle_base as base
from sysinv.helm.lifecycle_constants import LifecycleConstants

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
            (LifecycleConstants.APP_LIFECYCLE_TYPE_FLUXCD_REQUEST, constants.APP_APPLY_OP,
             LifecycleConstants.APP_LIFECYCLE_TIMING_PRE): (
                lambda: self.pre_apply()),  # pylint: disable=unnecessary-lambda
            (LifecycleConstants.APP_LIFECYCLE_TYPE_FLUXCD_REQUEST, constants.APP_APPLY_OP,
             LifecycleConstants.APP_LIFECYCLE_TIMING_POST): lambda: self.post_apply(app_op, app),
            (LifecycleConstants.APP_LIFECYCLE_TYPE_OPERATION, constants.APP_REMOVE_OP,
             LifecycleConstants.APP_LIFECYCLE_TIMING_PRE): lambda: self.pre_remove(app),
            (LifecycleConstants.APP_LIFECYCLE_TYPE_OPERATION, constants.APP_REMOVE_OP,
             LifecycleConstants.APP_LIFECYCLE_TIMING_POST): (
                lambda: self.post_remove()),  # pylint: disable=unnecessary-lambda
            (LifecycleConstants.APP_LIFECYCLE_TYPE_RESOURCE, constants.APP_DOWNGRADE_OP,
             LifecycleConstants.APP_LIFECYCLE_TIMING_PRE): lambda: self.pre_downgrade(hook_info),
        }

        # Get the appropriate lifecylce function from the dictionary based on the values
        action_function = action_map.get((hook_info.lifecycle_type, hook_info.operation,
                                          hook_info.relative_timing))

        if action_function is not None:
            action_function()

        super().app_lifecycle_actions(context, conductor_obj, app_op, app, hook_info)

    def post_apply(self, app_op, app):
        """Perform post-apply actions for the KubeVirt application. """

        LOG.debug(f"Executing post_apply for {app_constants.HELM_APP_KUBEVIRT} app")

        self.update_namespace_override(app_op, app, app_constants.HELM_NS_KUBEVIRT)
        self.update_namespace_override(app_op, app, app_constants.HELM_NS_CDI)

        # Delete stale cdi HelmRelease only if it was suspended by
        # pre_downgrade. A suspended HelmRelease means a downgrade occurred
        # from the chart-split version. On fresh install or upgrade, the
        # HelmRelease is active (not suspended), so this is a no-op.
        cmd = [
            'kubectl', '--kubeconfig', kubernetes.KUBERNETES_ADMIN_CONF,
            'get', 'helmrelease', app_constants.HELM_APP_CDI,
            '-n', app_constants.HELM_RELEASE_NS,
            '-o', 'jsonpath={.spec.suspend}'
        ]
        stdout, _ = cutils.trycmd(*cmd)
        if stdout.strip() == 'true':
            cmd = [
                'kubectl', '--kubeconfig', kubernetes.KUBERNETES_ADMIN_CONF,
                'delete', 'helmrelease', app_constants.HELM_APP_CDI,
                '-n', app_constants.HELM_RELEASE_NS,
                '--ignore-not-found=true', '--timeout=30s',
                '--request-timeout=30s'
            ]
            stdout, stderr = cutils.trycmd(*cmd)
            LOG.debug(f"{app_constants.HELM_APP_KUBEVIRT} app: deleted stale "
                      f"cdi HelmRelease: stdout={stdout} stderr={stderr}")

    def _transfer_cdi_ownership(self, release_name):
        """Transfer Helm ownership of CDI resources to the given release."""
        LOG.info(f"{app_constants.HELM_APP_KUBEVIRT} app: Transferring CDI "
                 f"resource ownership to {release_name}")

        cmd = [
            'kubectl', '--kubeconfig', kubernetes.KUBERNETES_ADMIN_CONF,
            'annotate', 'all,sa,role,rolebinding,cdi.cdi.kubevirt.io',
            '-n', app_constants.HELM_NS_CDI,
            f'meta.helm.sh/release-name={release_name}',
            'helm.sh/resource-policy=keep',
            '--overwrite', '--ignore-not-found'
        ]
        cutils.trycmd(*cmd)

        cmd = [
            'kubectl', '--kubeconfig', kubernetes.KUBERNETES_ADMIN_CONF,
            'annotate', 'clusterrole,clusterrolebinding',
            '-l', 'operator.cdi.kubevirt.io',
            f'meta.helm.sh/release-name={release_name}',
            'helm.sh/resource-policy=keep',
            '--overwrite', '--ignore-not-found'
        ]
        cutils.trycmd(*cmd)

        cmd = [
            'kubectl', '--kubeconfig', kubernetes.KUBERNETES_ADMIN_CONF,
            'annotate', 'rolebinding', 'cdi-registry-rolebinding',
            '-n', app_constants.HELM_RELEASE_NS,
            f'meta.helm.sh/release-name={release_name}',
            'helm.sh/resource-policy=keep',
            '--overwrite', '--ignore-not-found'
        ]
        cutils.trycmd(*cmd)

    def pre_apply(self):
        """Prepare CDI resources for chart-split upgrade.

        Annotates CDI resources with:
        - helm.sh/resource-policy: keep — prevents kubevirt-app upgrade
          from deleting them when removed from its chart.
        - meta.helm.sh/release-name: cdi — allows the new cdi HelmRelease
          to adopt the existing resources.
        """
        cmd = [
            'kubectl', '--kubeconfig', kubernetes.KUBERNETES_ADMIN_CONF,
            'get', 'deploy', 'cdi-operator',
            '-n', app_constants.HELM_NS_CDI,
            '-o', 'jsonpath={.metadata.annotations.meta\\.helm\\.sh/release-name}',
            '--ignore-not-found'
        ]
        stdout, _ = cutils.trycmd(*cmd)
        if stdout.strip() != app_constants.HELM_APP_KUBEVIRT:
            return

        self._transfer_cdi_ownership(app_constants.HELM_APP_CDI)

    def pre_downgrade(self, hook_info):
        """Block kubevirt-app downgrade if VMs are running.

        Ghost record checkpoint files written by a newer virt-handler may use
        normalized socket paths (filepath.Clean). An older virt-handler that
        lacks this normalization will fail the raw string comparison in
        AddGhostRecord, entering a repeated re-enqueue loop that prevents it
        from managing any VM. See https://github.com/kubevirt/kubevirt/issues/17137

        :param hook_info: LifecycleHookInfo object with extra dict containing
                          FROM_APP_VERSION and TO_APP_VERSION
        :raises exception.ApplicationLifecyclePreActionAbort: if active VMIs exist
        """
        from_version = hook_info.extra.get(
            LifecycleConstants.FROM_APP_VERSION, 'unknown')
        to_version = hook_info.extra.get(
            LifecycleConstants.TO_APP_VERSION, 'unknown')

        LOG.info(f"Pre-downgrade check for {app_constants.HELM_APP_KUBEVIRT}: "
                 f"{from_version} -> {to_version}")

        active_vmis = self._get_active_vmis()
        if active_vmis:
            msg = (f"Cannot downgrade {app_constants.HELM_APP_KUBEVIRT} from "
                   f"{from_version} to {to_version} while VMs are running. "
                   f"Active VMIs: {', '.join(active_vmis)}. "
                   f"Stop all VMs first (virtctl stop <vm> -n <namespace>), "
                   f"then retry the downgrade.")
            LOG.error(msg)
            raise RuntimeError(msg)

        self._transfer_cdi_ownership(app_constants.HELM_APP_KUBEVIRT)

        # Suspend the standalone cdi HelmRelease first to prevent it
        # from recreating resources after we delete them below.
        LOG.info(f"{app_constants.HELM_APP_KUBEVIRT} app: Suspending "
                 f"cdi HelmRelease reconciliation")
        cmd = [
            'kubectl', '--kubeconfig', kubernetes.KUBERNETES_ADMIN_CONF,
            'patch', 'helmrelease', app_constants.HELM_APP_CDI,
            '-n', app_constants.HELM_RELEASE_NS, '--type=merge',
            '-p', '{"spec":{"suspend":true}}'
        ]
        stdout, stderr = cutils.trycmd(*cmd)
        LOG.info(f"{app_constants.HELM_APP_KUBEVIRT} app: suspended cdi "
                 f"HelmRelease: stdout={stdout} stderr={stderr}")

        # Delete cdi-operator first and wait for termination so it cannot
        # recreate deployments after we remove them. Then delete the rest.
        # The 26.10 cdi chart introduces incompatible spec changes (probes,
        # selectors) that cannot be resolved by Helm 3-way merge.
        LOG.info(f"{app_constants.HELM_APP_KUBEVIRT} app: Deleting CDI "
                 f"deployments for clean downgrade")
        cmd = [
            'kubectl', '--kubeconfig', kubernetes.KUBERNETES_ADMIN_CONF,
            'delete', 'deploy', 'cdi-operator',
            '-n', app_constants.HELM_NS_CDI, '--ignore-not-found',
            '--wait=true'
        ]
        cutils.trycmd(*cmd)
        cmd = [
            'kubectl', '--kubeconfig', kubernetes.KUBERNETES_ADMIN_CONF,
            'delete', 'deploy', 'cdi-apiserver',
            'cdi-deployment', 'cdi-uploadproxy',
            '-n', app_constants.HELM_NS_CDI, '--ignore-not-found'
        ]
        cutils.trycmd(*cmd)

        # Clear CDI CR status so the older operator can reconcile.
        # Without this, the operator refuses with "operator downgraded".
        cmd = [
            'kubectl', '--kubeconfig', kubernetes.KUBERNETES_ADMIN_CONF,
            'patch', 'cdis.cdi.kubevirt.io', 'cdi', '--type=json',
            '-p', '[{"op":"replace","path":"/status/observedVersion","value":""},'
                  '{"op":"replace","path":"/status/operatorVersion","value":""}]'
        ]
        cutils.trycmd(*cmd)

        # Delete the cdi HelmRelease since older releases are unaware
        # of it and would leave it orphaned in kube-system.
        cmd = [
            'kubectl', '--kubeconfig', kubernetes.KUBERNETES_ADMIN_CONF,
            'delete', 'helmrelease', app_constants.HELM_APP_CDI,
            '-n', app_constants.HELM_RELEASE_NS, '--ignore-not-found'
        ]
        cutils.trycmd(*cmd)

        LOG.info(f"Pre-downgrade check passed: no active VMIs found, "
                 f"proceeding with downgrade to {to_version}")

    def _get_active_vmis(self):
        """Query Kubernetes for VMIs in active phases.

        :return: list of 'namespace/name' strings for active VMIs
        """
        active_phases = ('Running', 'Scheduling', 'Scheduled')
        cmd = [
            'kubectl', '--kubeconfig', kubernetes.KUBERNETES_ADMIN_CONF,
            'get', 'vmi', '--all-namespaces',
            '-o', 'jsonpath={range .items[*]}{.metadata.namespace}/'
                  '{.metadata.name}={.status.phase}{\"\\n\"}{end}'
        ]
        stdout, _stderr = cutils.trycmd(*cmd)
        if not stdout or not stdout.strip():
            return []

        active = []
        for line in stdout.strip().split('\n'):
            line = line.strip()
            if not line:
                continue
            if '=' in line:
                name, phase = line.rsplit('=', 1)
                if phase in active_phases:
                    active.append(name)
        return active

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
        # Map namespace to the correct chart name
        if namespace == app_constants.HELM_NS_CDI:
            chart_name = app_constants.HELM_APP_CDI
        else:
            chart_name = app_constants.HELM_APP_KUBEVIRT

        try:
            overrides = dbapi_instance.helm_override_get(
                app_id=db_app_id,
                name=chart_name,
                namespace=namespace,
            )
        except exception.HelmOverrideNotFound:
            values = {
                "name": chart_name,
                "namespace": namespace,
                "app_id": db_app_id,
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

    def _wait_for_deletion(self, resource_type, selector="--all", namespace=None, timeout=30):
        """Wait for Kubernetes resources to be deleted."""
        cmd = ['kubectl', '--kubeconfig', kubernetes.KUBERNETES_ADMIN_CONF,
               'wait', '--for=delete', resource_type, selector, f'--timeout={timeout}s']
        if namespace:
            cmd.extend(['-n', namespace])

        _, stderr = cutils.trycmd(*cmd)
        return 'timed out' not in stderr.lower()

    def _delete_and_wait(self, resource_type, selector="--all", namespace=None, timeout=30):
        """Delete resources and wait for actual deletion."""
        # Delete
        cmd = ['kubectl', '--kubeconfig', kubernetes.KUBERNETES_ADMIN_CONF,
               'delete', resource_type, selector, f'--timeout={timeout}s']
        if namespace:
            cmd.extend(['-n', namespace])
        cutils.trycmd(*cmd)

        # Wait for actual deletion
        return self._wait_for_deletion(resource_type, selector, namespace, timeout)

    # pylint: disable=too-many-locals,too-many-branches,too-many-statements
    def pre_remove(self, app):
        """Pre application removal tasks.

        Performs comprehensive cleanup before application removal to ensure
        complete resource cleanup and prevent circular dependencies. This
        method executes the following cleanup sequence:

        1. Stop all Virtual Machines to prevent orphaned resources
        2. Suspend FluxCD reconciliation to prevent resource recreation
        3. Stop all workloads (deployments, daemonsets, statefulsets, jobs)
        4. Force kill any remaining pods
        5. Delete webhook configurations and services to prevent validation
        6. Strip finalizers and force delete custom resources (KubeVirt, CDI)
        7. Force delete all kubevirt/cdi CRDs regardless of management
        8. Clean up all kubevirt/cdi APIServices
        9. Clean namespaces while preserving namespace objects
        10. Delete orphaned virt-launcher pods in all namespaces
        11. Delete virt-operator managed cluster-scoped resources
        12. Delete Helm-managed cluster-scoped resources
        13. Delete RoleBindings in kube-system
        14. Delete orphaned HelmChart in kube-system

        :param app: The application object.
        """

        LOG.debug(f"Executing pre_remove for "
                  f"{app_constants.HELM_APP_KUBEVIRT} app")

        # Step 1: Stop all Virtual Machines before cleanup
        LOG.debug(f"{app.name} app: Stopping all Virtual Machines")
        cmd = [
            'kubectl', '--kubeconfig', kubernetes.KUBERNETES_ADMIN_CONF,
            'get', 'vm,vmi', '--all-namespaces', '-o', 'wide'
        ]
        stdout, stderr = cutils.trycmd(*cmd)
        if stdout and 'No resources found' not in stdout:
            LOG.debug(f"{app.name} app: Running VMs detected - stopping them")

            # Try graceful deletion first
            if not self._delete_and_wait('vm', '--all-namespaces', timeout=60):
                LOG.debug(f"{app.name} app: VM graceful deletion timed out, forcing")

                # Force delete VMs after timeout
                cmd = [
                    'kubectl', '--kubeconfig', kubernetes.KUBERNETES_ADMIN_CONF,
                    'delete', 'vm', '--all', '--all-namespaces',
                    '--force', '--grace-period=0'
                ]
                stdout, stderr = cutils.trycmd(*cmd)
                LOG.debug(f"{app.name} app: VM force deletion: "
                          f"stdout={stdout} stderr={stderr}")

            # Force delete VMIs directly
            self._delete_and_wait('vmi', '--all-namespaces', timeout=30)

        # Step 2: Suspend FluxCD reconciliation to prevent recreation
        LOG.debug(f"{app.name} app: Suspending FluxCD reconciliation")
        for release_name in [app_constants.HELM_APP_KUBEVIRT,
                             app_constants.HELM_APP_CDI]:
            cmd = [
                'kubectl', '--kubeconfig', kubernetes.KUBERNETES_ADMIN_CONF,
                'patch', 'helmrelease', release_name,
                '-n', 'kube-system', '--type=merge',
                '-p', '{"spec":{"suspend":true}}'
            ]
            stdout, stderr = cutils.trycmd(*cmd)
            LOG.debug(f"{app.name} app: suspended FluxCD reconciliation "
                      f"for {release_name}: stdout={stdout} stderr={stderr}")

        # Step 3: Stop all workloads to prevent recreation during cleanup
        LOG.debug(f"{app.name} app: Stopping all workloads")
        for namespace in [app_constants.HELM_NS_KUBEVIRT,
                          app_constants.HELM_NS_CDI]:
            self._delete_and_wait('deploy,daemonset,statefulset,job',
                                  namespace=namespace, timeout=60)

        # Step 4: Force kill any remaining pods
        LOG.debug(f"{app.name} app: Force killing remaining pods")
        for namespace in [app_constants.HELM_NS_KUBEVIRT,
                          app_constants.HELM_NS_CDI]:
            # Use force delete for pods that might be stuck
            cmd = [
                'kubectl', '--kubeconfig', kubernetes.KUBERNETES_ADMIN_CONF,
                'delete', 'pods', '--all', '-n', namespace,
                '--force', '--grace-period=0'
            ]
            stdout, stderr = cutils.trycmd(*cmd)
            # Wait for pods to actually be gone
            self._wait_for_deletion('pods', namespace=namespace, timeout=30)

        # Step 5: Delete webhooks to break circular dependencies
        LOG.debug(f"{app.name} app: Deleting webhooks")
        webhook_types = ['validatingwebhookconfigurations',
                         'mutatingwebhookconfigurations']
        for webhook_type in webhook_types:
            # Get webhook names containing kubevirt or cdi
            cmd = [
                'kubectl', '--kubeconfig', kubernetes.KUBERNETES_ADMIN_CONF,
                'get', webhook_type, '-o', 'name'
            ]
            stdout, stderr = cutils.trycmd(*cmd)
            if stdout:
                webhooks = [w.strip() for w in stdout.split('\n')
                            if w.strip() and ('kubevirt' in w.lower() or
                                              w.lower().startswith('cdi-'))]
                for webhook in webhooks:
                    cmd = [
                        'kubectl', '--kubeconfig',
                        kubernetes.KUBERNETES_ADMIN_CONF,
                        'delete', webhook, '--ignore-not-found=true'
                    ]
                    stdout, stderr = cutils.trycmd(*cmd)
                    LOG.debug(f"{app.name} app: deleted webhook {webhook}: "
                              f"stdout={stdout} stderr={stderr}")

        # Also delete specific webhook configurations that cause validation
        LOG.debug(f"{app.name} app: Deleting specific webhook configurations")
        webhook_configs = ['virt-api-validator', 'virt-operator-validator',
                           'cdi-api-datavolume-validate', 'virt-api-mutator']
        for config in webhook_configs:
            cmd = [
                'kubectl', '--kubeconfig', kubernetes.KUBERNETES_ADMIN_CONF,
                'delete', 'validatingwebhookconfigurations', config,
                '--ignore-not-found=true'
            ]
            stdout, stderr = cutils.trycmd(*cmd)
            LOG.debug(f"{app.name} app: deleted validating webhook {config}: "
                      f"stdout={stdout} stderr={stderr}")

            cmd = [
                'kubectl', '--kubeconfig', kubernetes.KUBERNETES_ADMIN_CONF,
                'delete', 'mutatingwebhookconfigurations', config,
                '--ignore-not-found=true'
            ]
            stdout, stderr = cutils.trycmd(*cmd)
            LOG.debug(f"{app.name} app: deleted mutating webhook {config}: "
                      f"stdout={stdout} stderr={stderr}")

        # Delete webhook services to prevent validation calls
        LOG.debug(f"{app.name} app: Deleting webhook services")
        webhook_services = ['kubevirt-operator-webhook', 'cdi-api']
        for service in webhook_services:
            for namespace in [app_constants.HELM_NS_KUBEVIRT,
                              app_constants.HELM_NS_CDI]:
                cmd = [
                    'kubectl', '--kubeconfig',
                    kubernetes.KUBERNETES_ADMIN_CONF,
                    'delete', 'service', service, '-n', namespace,
                    '--ignore-not-found=true'
                ]
                stdout, stderr = cutils.trycmd(*cmd)
                LOG.debug(f"{app.name} app: deleted service {service} "
                          f"in {namespace}: stdout={stdout} stderr={stderr}")

        # Wait for webhook deletions to be processed
        LOG.debug(f"{app.name} app: Waiting for webhook deletions to complete")
        # Try to wait for specific webhooks to be deleted
        webhook_configs = ['virt-api-validator', 'virt-operator-validator',
                           'cdi-api-datavolume-validate']
        for config in webhook_configs:
            self._wait_for_deletion('validatingwebhookconfigurations', config, timeout=10)

        # Step 6: Strip finalizers from custom resources before deletion
        LOG.debug(f"{app.name} app: Stripping finalizers from custom "
                  "resources")
        custom_resources = [
            ('kubevirt', 'kubevirt', app_constants.HELM_NS_KUBEVIRT),
            ('cdi', 'cdi', app_constants.HELM_NS_CDI)
        ]
        for resource_type, resource_name, namespace in custom_resources:
            # Strip finalizers first - this prevents webhook validation
            cmd = [
                'kubectl', '--kubeconfig', kubernetes.KUBERNETES_ADMIN_CONF,
                'patch', resource_type, resource_name, '-n', namespace,
                '--type=merge', '-p', '{"metadata":{"finalizers":[]}}'
            ]
            stdout, stderr = cutils.trycmd(*cmd)
            LOG.debug(f"{app.name} app: stripped finalizers from "
                      f"{resource_type}/{resource_name}: "
                      f"stdout={stdout} stderr={stderr}")

            # Force delete the custom resource without waiting for validation
            cmd = [
                'kubectl', '--kubeconfig', kubernetes.KUBERNETES_ADMIN_CONF,
                'delete', resource_type, resource_name, '-n', namespace,
                '--ignore-not-found=true', '--wait=false',
                '--force', '--grace-period=0'
            ]
            stdout, stderr = cutils.trycmd(*cmd)
            LOG.debug(f"{app.name} app: force deleted "
                      f"{resource_type}/{resource_name}: "
                      f"stdout={stdout} stderr={stderr}")

        # Step 7: Get ALL kubevirt/cdi CRDs and force delete them
        # Note: These CRDs are managed by operators, not Helm, so they require
        # explicit cleanup during application removal
        LOG.debug(f"{app.name} app: Force deleting ALL kubevirt/cdi CRDs")
        cmd = [
            'kubectl', '--kubeconfig', kubernetes.KUBERNETES_ADMIN_CONF,
            'get', 'crd', '-o', 'name'
        ]
        stdout, stderr = cutils.trycmd(*cmd)
        if stdout:
            # Delete ALL CRDs containing kubevirt or cdi
            all_crds = [c.strip() for c in stdout.split('\n')
                        if c.strip() and ('kubevirt' in c.lower() or
                                          c.lower().startswith('cdi-'))]

            for crd in all_crds:
                if crd:
                    # Try normal deletion first
                    cmd = [
                        'kubectl', '--kubeconfig',
                        kubernetes.KUBERNETES_ADMIN_CONF,
                        'delete', crd, '--ignore-not-found=true',
                        '--timeout=30s'
                    ]
                    stdout, stderr = cutils.trycmd(*cmd)

                    # If deletion fails, force remove finalizers and try again
                    if stderr and 'timeout' in stderr.lower():
                        LOG.debug(f"{app.name} app: Force removing "
                                  f"finalizers from {crd}")
                        cmd = [
                            'kubectl', '--kubeconfig',
                            kubernetes.KUBERNETES_ADMIN_CONF,
                            'patch', crd, '--type=merge',
                            '-p', '{"metadata":{"finalizers":[]}}'
                        ]
                        stdout, stderr = cutils.trycmd(*cmd)

                        cmd = [
                            'kubectl', '--kubeconfig',
                            kubernetes.KUBERNETES_ADMIN_CONF,
                            'delete', crd, '--ignore-not-found=true',
                            '--timeout=10s'
                        ]
                        stdout, stderr = cutils.trycmd(*cmd)

                    LOG.debug(f"{app.name} app: processed CRD {crd}: "
                              f"stdout={stdout} stderr={stderr}")

        # Step 8: Clean up APIServices
        LOG.debug(f"{app.name} app: Cleaning up APIServices")
        cmd = [
            'kubectl', '--kubeconfig', kubernetes.KUBERNETES_ADMIN_CONF,
            'get', 'apiservice', '-o', 'name'
        ]
        stdout, stderr = cutils.trycmd(*cmd)
        if stdout:
            apiservices = [a.strip() for a in stdout.split('\n')
                           if a.strip() and '.kubevirt.io' in a.lower()]
            for apiservice in apiservices:
                cmd = [
                    'kubectl', '--kubeconfig',
                    kubernetes.KUBERNETES_ADMIN_CONF,
                    'delete', apiservice, '--ignore-not-found=true',
                    '--timeout=10s'
                ]
                stdout, stderr = cutils.trycmd(*cmd)
                LOG.debug(f"{app.name} app: deleted APIService {apiservice}: "
                          f"stdout={stdout} stderr={stderr}")

        # Step 9: Clean namespaces but preserve them
        LOG.debug(f"{app.name} app: Cleaning namespaces")
        for namespace in [app_constants.HELM_NS_KUBEVIRT,
                          app_constants.HELM_NS_CDI]:
            # Delete all remaining resources in namespace
            cmd = [
                'kubectl', '--kubeconfig', kubernetes.KUBERNETES_ADMIN_CONF,
                'delete', 'all', '--all', '-n', namespace,
                '--force', '--grace-period=0'
            ]
            cutils.trycmd(*cmd)

            # Remove finalizers from namespace to prevent blocking
            cmd = [
                'kubectl', '--kubeconfig', kubernetes.KUBERNETES_ADMIN_CONF,
                'patch', 'namespace', namespace, '--type=merge',
                '-p', '{"metadata":{"finalizers":[]}}'
            ]
            stdout, stderr = cutils.trycmd(*cmd)
            LOG.debug(f"{app.name} app: cleaned namespace {namespace}: "
                      f"stdout={stdout} stderr={stderr}")

        # Step 10: Delete orphaned virt-launcher pods in all namespaces
        LOG.debug(f"{app.name} app: Deleting virt-launcher pods")
        cmd = [
            'kubectl', '--kubeconfig', kubernetes.KUBERNETES_ADMIN_CONF,
            'delete', 'pod', '--all-namespaces',
            '-l', 'kubevirt.io/domain',
            '--force', '--grace-period=0'
        ]
        stdout, stderr = cutils.trycmd(*cmd)
        LOG.debug(f"{app.name} app: deleted virt-launcher pods: "
                  f"stdout={stdout} stderr={stderr}")

        # Step 11: Delete virt-operator managed cluster-scoped resources
        LOG.debug(f"{app.name} app: Deleting virt-operator managed resources")
        virt_operator_resources = [
            'clusterrole',
            'clusterrolebinding',
            'mutatingwebhookconfiguration',
            'validatingadmissionpolicy',
            'validatingadmissionpolicybinding',
        ]
        cmd = [
            'kubectl', '--kubeconfig', kubernetes.KUBERNETES_ADMIN_CONF,
            'delete', ','.join(virt_operator_resources),
            '-l', 'app.kubernetes.io/managed-by=virt-operator',
            '--ignore-not-found=true'
        ]
        stdout, stderr = cutils.trycmd(*cmd)
        LOG.debug(f"{app.name} app: deleted virt-operator resources: "
                  f"stdout={stdout} stderr={stderr}")

        # Step 12: Delete Helm-managed cluster-scoped resources
        LOG.debug(f"{app.name} app: Deleting Helm-managed cluster-scoped "
                  "resources")
        helm_cluster_resources = [
            'clusterrole',
            'clusterrolebinding',
            'priorityclass',
        ]
        for release_name in [app_constants.HELM_APP_KUBEVIRT,
                             app_constants.HELM_APP_CDI]:
            cmd = [
                'kubectl', '--kubeconfig', kubernetes.KUBERNETES_ADMIN_CONF,
                'delete', ','.join(helm_cluster_resources),
                '-l',
                f'helm.toolkit.fluxcd.io/name={release_name}',
                '--ignore-not-found=true'
            ]
            stdout, stderr = cutils.trycmd(*cmd)
            LOG.debug(f"{app.name} app: deleted Helm cluster resources "
                      f"for {release_name}: stdout={stdout} stderr={stderr}")

        # Step 13: Delete RoleBindings in kube-system
        LOG.debug(f"{app.name} app: Deleting RoleBindings in "
                  f"{app_constants.HELM_RELEASE_NS}")
        for release_name in [app_constants.HELM_APP_KUBEVIRT,
                             app_constants.HELM_APP_CDI]:
            cmd = [
                'kubectl', '--kubeconfig', kubernetes.KUBERNETES_ADMIN_CONF,
                'delete', 'rolebinding',
                '-n', app_constants.HELM_RELEASE_NS,
                '-l',
                f'helm.toolkit.fluxcd.io/name={release_name}',
                '--ignore-not-found=true'
            ]
            stdout, stderr = cutils.trycmd(*cmd)
            LOG.debug(f"{app.name} app: deleted rolebindings for "
                      f"{release_name}: stdout={stdout} stderr={stderr}")

        # Step 14: Delete orphaned HelmCharts in kube-system
        # The HelmChart objects live in kube-system, not in the kubevirt/cdi
        # namespaces cleaned above. Because FluxCD reconciliation was suspended
        # in Step 2, the framework cannot delete them via 'kubectl delete -k',
        # leaving them orphaned. Delete them explicitly.
        LOG.debug(f"{app.name} app: Deleting orphaned HelmCharts in "
                  f"{app_constants.HELM_RELEASE_NS}")
        for chart_name in [app_constants.HELM_APP_KUBEVIRT,
                           app_constants.HELM_APP_CDI]:
            helmchart_name = (f"{app_constants.HELM_RELEASE_NS}-"
                              f"{chart_name}")
            cmd = [
                'kubectl', '--kubeconfig', kubernetes.KUBERNETES_ADMIN_CONF,
                'delete', 'helmchart', helmchart_name,
                '-n', app_constants.HELM_RELEASE_NS,
                '--ignore-not-found=true', '--timeout=30s',
                '--request-timeout=30s'
            ]
            stdout, stderr = cutils.trycmd(*cmd)
            LOG.debug(f"{app.name} app: deleted HelmChart {helmchart_name}: "
                      f"stdout={stdout} stderr={stderr}")

        LOG.debug(f"{app.name} app: pre_remove comprehensive cleanup "
                  "completed")

    def post_remove(self):
        """Execute post-remove actions for the applications

        This method is responsible for performing cleanup actions after an
        application has been removed. It includes deleting Custom Resource Definitions
        (CRDs), removing symbolic links and binaries, and cleaning up directories.
        """

        LOG.debug(f"Executing post_remove for {app_constants.HELM_APP_KUBEVIRT} app")

        # Delete Helm release secrets
        for chart_name in [app_constants.HELM_APP_KUBEVIRT,
                           app_constants.HELM_APP_CDI]:
            cmd = [
                'kubectl', '--kubeconfig', kubernetes.KUBERNETES_ADMIN_CONF,
                'delete', 'secret',
                '-n', app_constants.HELM_RELEASE_NS,
                '-l', f'name={chart_name},owner=helm',
                '--ignore-not-found=true'
            ]
            stdout, stderr = cutils.trycmd(*cmd)
            LOG.debug(f"{app_constants.HELM_APP_KUBEVIRT} app: deleted helm "
                      f"secrets for {chart_name}: stdout={stdout} "
                      f"stderr={stderr}")

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
