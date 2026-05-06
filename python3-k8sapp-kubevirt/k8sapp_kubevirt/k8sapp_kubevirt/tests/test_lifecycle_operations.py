#
# Copyright (c) 2026 Wind River Systems, Inc.
#
# SPDX-License-Identifier: Apache-2.0
#
"""Tests for lifecycle_kubevirt operations.

Covers post_apply, pre_apply, update_namespace_override,
_get_helm_user_overrides, _delete_pods,
_wait_for_deletion, _delete_and_wait,
and post_remove.
"""

from unittest.mock import MagicMock
from unittest.mock import patch

import yaml

from sysinv.common import exception

from k8sapp_kubevirt.common import constants as app_constants
from k8sapp_kubevirt.lifecycle.lifecycle_kubevirt import \
    KubeVirtAppLifecycleOperator
from k8sapp_kubevirt.tests.lifecycle_base import LifecycleTestBase


class TestPostApply(LifecycleTestBase):
    """Tests for post_apply method.

    Validates stale HelmRelease deletion when
    suspended, and no-op when active.
    """

    def setUp(self):
        """Set up with update_namespace_override patched."""
        super().setUp()
        patch.object(
            KubeVirtAppLifecycleOperator,
            'update_namespace_override',
        ).start()

    def test_post_apply_deletes_suspended_helmrelease(
        self,
    ):
        """Verify suspended HelmRelease gets deleted."""
        self.mock_cutils.trycmd.side_effect = [
            ('true', ''),
            ('', ''),
        ]
        self.operator.post_apply(
            MagicMock(), MagicMock()
        )
        self.assertEqual(
            self.mock_cutils.trycmd.call_count, 2
        )

    def test_post_apply_skips_active_helmrelease(self):
        """Verify active HelmRelease is not deleted."""
        self.mock_cutils.trycmd.return_value = (
            'false', ''
        )
        self.operator.post_apply(
            MagicMock(), MagicMock()
        )
        self.mock_cutils.trycmd.assert_called_once()

    def test_post_apply_skips_empty_response(self):
        """Verify empty response means no deletion."""
        self.mock_cutils.trycmd.return_value = ('', '')
        self.operator.post_apply(
            MagicMock(), MagicMock()
        )
        self.mock_cutils.trycmd.assert_called_once()


class TestPreApply(LifecycleTestBase):
    """Tests for pre_apply method.

    Validates CDI ownership transfer only when
    resources are owned by kubevirt.
    """

    def test_transfers_when_owned_by_kubevirt(self):
        """Verify ownership transfer when kubevirt owns CDI."""
        self.mock_cutils.trycmd.return_value = (
            app_constants.HELM_APP_KUBEVIRT, ''
        )
        self.operator.pre_apply()
        self.assertEqual(
            self.mock_cutils.trycmd.call_count, 4
        )

    def test_skips_when_already_cdi_owned(self):
        """Verify no transfer when already owned by cdi."""
        self.mock_cutils.trycmd.return_value = (
            app_constants.HELM_APP_CDI, ''
        )
        self.operator.pre_apply()
        self.mock_cutils.trycmd.assert_called_once()

    def test_skips_when_not_found(self):
        """Verify no transfer when deploy not found."""
        self.mock_cutils.trycmd.return_value = ('', '')
        self.operator.pre_apply()
        self.mock_cutils.trycmd.assert_called_once()


class TestUpdateNamespaceOverride(LifecycleTestBase):
    """Tests for update_namespace_override.

    Validates namespace label patching and pod
    deletion based on helm user overrides.
    """

    def _make_app_operator(self, old_label=None):
        """Create a mock app_operator with namespace metadata.

        old_label -- existing component label value

        Returns tuple of (app_operator, namespace_obj).
        """
        app_operator = MagicMock()
        db_app = MagicMock()
        db_app.id = 42
        app_operator._dbapi.kube_app_get.return_value = (
            db_app
        )

        namespace_obj = MagicMock()
        labels = {}
        if old_label is not None:
            labels[
                app_constants.HELM_CHART_COMPONENT_LABEL
            ] = old_label
        namespace_obj.metadata.labels = labels
        kube_core = (
            app_operator._kube
            ._get_kubernetesclient_core.return_value
        )
        kube_core.read_namespace.return_value = (
            namespace_obj
        )
        return app_operator, namespace_obj

    def test_unsupported_label_no_patch(self):
        """Verify unsupported label does not patch."""
        with patch.object(
            self.operator, '_get_helm_user_overrides',
            return_value=yaml.dump({
                app_constants.HELM_CHART_COMPONENT_LABEL:
                    'unsupported_value',
            }),
        ), patch.object(
            self.operator, '_delete_pods',
        ):
            app_operator, _ns_obj = (
                self._make_app_operator()
            )
            app = MagicMock()
            app.name = 'kubevirt-app'
            self.operator.update_namespace_override(
                app_operator, app,
                app_constants.HELM_NS_KUBEVIRT,
            )
            app_operator._kube \
                .kube_patch_namespace.assert_not_called()

    def test_label_change_triggers_pod_delete(self):
        """Verify label change triggers _delete_pods."""
        with patch.object(
            self.operator, '_get_helm_user_overrides',
            return_value=yaml.dump({
                app_constants.HELM_CHART_COMPONENT_LABEL:
                    app_constants
                    .HELM_CHART_COMPONENT_APPLICATION,
            }),
        ), patch.object(
            self.operator, '_delete_pods',
        ) as mock_del:
            app_operator, _ns_obj = (
                self._make_app_operator(
                    old_label='platform'
                )
            )
            app = MagicMock()
            app.name = 'kubevirt-app'
            self.operator.update_namespace_override(
                app_operator, app,
                app_constants.HELM_NS_KUBEVIRT,
            )
            mock_del.assert_called_once()

    def test_no_label_change_no_pod_delete(self):
        """Verify same label skips _delete_pods."""
        with patch.object(
            self.operator, '_get_helm_user_overrides',
            return_value=yaml.dump({
                app_constants.HELM_CHART_COMPONENT_LABEL:
                    app_constants
                    .HELM_CHART_COMPONENT_PLATFORM,
            }),
        ), patch.object(
            self.operator, '_delete_pods',
        ) as mock_del:
            app_operator, namespace_obj = (
                self._make_app_operator(
                    old_label='platform'
                )
            )
            app = MagicMock()
            app.name = 'kubevirt-app'
            namespace_obj.metadata.labels = {
                app_constants.HELM_CHART_COMPONENT_LABEL:
                    'platform',
            }
            self.operator.update_namespace_override(
                app_operator, app,
                app_constants.HELM_NS_KUBEVIRT,
            )
            mock_del.assert_not_called()


class TestGetHelmUserOverrides(LifecycleTestBase):
    """Tests for _get_helm_user_overrides."""

    def test_returns_user_overrides(self):
        """Verify existing user overrides returned."""
        mock_dbapi = MagicMock()
        override = MagicMock()
        override.user_overrides = 'some: value'
        mock_dbapi.helm_override_get.return_value = (
            override
        )
        result = self.operator._get_helm_user_overrides(
            mock_dbapi, 1, 'kubevirt'
        )
        self.assertEqual(result, 'some: value')

    def test_returns_empty_when_no_overrides(self):
        """Verify empty string when overrides is None."""
        mock_dbapi = MagicMock()
        override = MagicMock()
        override.user_overrides = None
        mock_dbapi.helm_override_get.return_value = (
            override
        )
        result = self.operator._get_helm_user_overrides(
            mock_dbapi, 1, 'kubevirt'
        )
        self.assertEqual(result, '')

    def test_creates_override_on_not_found(self):
        """Verify override created on not found."""
        mock_dbapi = MagicMock()
        mock_dbapi.helm_override_get.side_effect = (
            exception.HelmOverrideNotFound(
                name='kubevirt-app',
                namespace='kubevirt',
            )
        )
        created = MagicMock()
        created.user_overrides = None
        mock_dbapi.helm_override_create.return_value = (
            created
        )
        result = self.operator._get_helm_user_overrides(
            mock_dbapi, 1, 'kubevirt'
        )
        self.assertEqual(result, '')
        mock_dbapi.helm_override_create \
            .assert_called_once()


class TestDeletePods(LifecycleTestBase):
    """Tests for _delete_pods."""

    def test_deletes_all_pods_in_namespace(self):
        """Verify all pods in namespace are deleted."""
        app_operator = MagicMock()
        kube_client_core = MagicMock()
        pod_one = MagicMock()
        pod_one.metadata.name = 'pod-1'
        pod_two = MagicMock()
        pod_two.metadata.name = 'pod-2'
        kube_client_core \
            .list_namespaced_pod.return_value.items = (
                [pod_one, pod_two]
            )
        self.operator._delete_pods(
            app_operator, kube_client_core, 'kubevirt'
        )
        self.assertEqual(
            app_operator._kube
            .kube_delete_pod.call_count, 2
        )

    def test_no_pods_no_delete(self):
        """Verify no pods means no delete calls."""
        app_operator = MagicMock()
        kube_client_core = MagicMock()
        kube_client_core \
            .list_namespaced_pod.return_value.items = []
        self.operator._delete_pods(
            app_operator, kube_client_core, 'kubevirt'
        )
        app_operator._kube \
            .kube_delete_pod.assert_not_called()


class TestWaitForDeletion(LifecycleTestBase):
    """Tests for _wait_for_deletion."""

    def test_returns_true_on_success(self):
        """Verify returns True on success."""
        self.mock_cutils.trycmd.return_value = ('', '')
        result = self.operator._wait_for_deletion(
            'pods'
        )
        self.assertTrue(result)

    def test_returns_false_on_timeout(self):
        """Verify returns False when timed out."""
        self.mock_cutils.trycmd.return_value = (
            '', 'error: timed out waiting'
        )
        result = self.operator._wait_for_deletion(
            'pods'
        )
        self.assertFalse(result)


class TestDeleteAndWait(LifecycleTestBase):
    """Tests for _delete_and_wait."""

    def test_delete_and_wait_timeout(self):
        """Verify returns False on timeout."""
        self.mock_cutils.trycmd.side_effect = [
            ('', ''),
            ('', 'error: timed out waiting'),
        ]
        result = self.operator._delete_and_wait('pods')
        self.assertFalse(result)


class TestPostRemove(LifecycleTestBase):
    """Tests for post_remove."""

    @patch('os.path.exists')
    @patch('os.remove')
    @patch('os.listdir')
    @patch('os.rmdir')
    def test_post_remove_all_exist_empty_dir(
        self, mock_rmdir, mock_listdir,
        mock_remove, mock_exists
    ):
        """Verify cleanup when files exist and dir empty."""
        mock_exists.return_value = True
        mock_listdir.return_value = []
        self.operator.post_remove()
        self.assertEqual(mock_remove.call_count, 2)
        mock_rmdir.assert_called_once()

    @patch('os.path.exists')
    @patch('os.remove')
    @patch('os.listdir')
    @patch('os.rmdir')
    def test_post_remove_all_exist_nonempty_dir(
        self, mock_rmdir, mock_listdir,
        mock_remove, mock_exists
    ):
        """Verify dir not removed when not empty."""
        mock_exists.return_value = True
        mock_listdir.return_value = ['some_file']
        self.operator.post_remove()
        self.assertEqual(mock_remove.call_count, 2)
        mock_rmdir.assert_not_called()

    @patch('os.path.exists')
    @patch('os.remove')
    @patch('os.listdir')
    @patch('os.rmdir')
    def test_post_remove_link_missing(
        self, _, mock_listdir,
        mock_remove, mock_exists
    ):
        """Verify handling when symlink missing."""
        mock_exists.side_effect = [False, True]
        mock_listdir.return_value = []
        self.operator.post_remove()
        mock_remove.assert_called_once()

    @patch('os.path.exists')
    @patch('os.remove')
    @patch('os.listdir')
    @patch('os.rmdir')
    def test_post_remove_binary_missing(
        self, _, mock_listdir,
        mock_remove, mock_exists
    ):
        """Verify handling when binary missing."""
        mock_exists.side_effect = [True, False]
        mock_listdir.return_value = []
        self.operator.post_remove()
        mock_remove.assert_called_once()

    @patch('os.path.exists')
    @patch('os.remove')
    @patch('os.listdir')
    @patch('os.rmdir')
    def test_post_remove_both_missing(
        self, _, mock_listdir,
        mock_remove, mock_exists
    ):
        """Verify no removal when both files missing."""
        mock_exists.return_value = False
        mock_listdir.return_value = []
        self.operator.post_remove()
        mock_remove.assert_not_called()
