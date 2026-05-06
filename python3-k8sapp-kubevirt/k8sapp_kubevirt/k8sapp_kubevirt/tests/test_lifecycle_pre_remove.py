#
# Copyright (c) 2026 Wind River Systems, Inc.
#
# SPDX-License-Identifier: Apache-2.0
#
"""Tests for pre_remove conditional branches.

Covers VM detection and graceful/force deletion,
webhook filtering, CRD timeout retry, and
APIService cleanup logic.
"""

from unittest.mock import MagicMock
from unittest.mock import patch

from k8sapp_kubevirt.lifecycle.lifecycle_kubevirt import \
    KubeVirtAppLifecycleOperator
from k8sapp_kubevirt.tests.lifecycle_base import LifecycleTestBase


class TestPreRemoveNoVMs(LifecycleTestBase):
    """Tests for pre_remove when no VMs are running."""

    def setUp(self):
        """Set up with helper methods patched."""
        super().setUp()
        self.mock_cutils.trycmd.return_value = (
            'No resources found', ''
        )
        self.mock_dw = patch.object(
            KubeVirtAppLifecycleOperator,
            '_delete_and_wait',
            return_value=True,
        ).start()
        patch.object(
            KubeVirtAppLifecycleOperator,
            '_wait_for_deletion',
            return_value=True,
        ).start()

    def test_no_vms_skips_vm_deletion(self):
        """Verify VM deletion skipped with no VMs."""
        app = MagicMock()
        app.name = 'kubevirt'
        self.operator.pre_remove(app)
        vm_calls = [
            c for c in self.mock_dw.call_args_list
            if c[0][0] == 'vm'
        ]
        self.assertEqual(vm_calls, [])


class TestPreRemoveWithVMs(LifecycleTestBase):
    """Tests for pre_remove when VMs are running."""

    def setUp(self):
        """Set up with wait patched."""
        super().setUp()
        patch.object(
            KubeVirtAppLifecycleOperator,
            '_wait_for_deletion',
            return_value=True,
        ).start()

    def test_vms_found_triggers_graceful_delete(self):
        """Verify VMs found triggers _delete_and_wait."""
        call_count = [0]

        def trycmd_side_effect(*_):
            """Return VM list on first call."""
            call_count[0] += 1
            if call_count[0] == 1:
                return (
                    'NAME   NAMESPACE\nvm1   default',
                    '',
                )
            return ('', '')

        self.mock_cutils.trycmd.side_effect = (
            trycmd_side_effect
        )
        with patch.object(
            self.operator, '_delete_and_wait',
            return_value=True,
        ) as mock_dw:
            app = MagicMock()
            app.name = 'kubevirt'
            self.operator.pre_remove(app)
            vm_calls = [
                c for c in mock_dw.call_args_list
                if c[0][0] == 'vm'
            ]
            self.assertGreater(len(vm_calls), 0)

    def test_graceful_timeout_triggers_force_delete(
        self,
    ):
        """Verify force delete when graceful times out."""
        call_count = [0]

        def trycmd_side_effect(*_):
            """Return VM list on first call."""
            call_count[0] += 1
            if call_count[0] == 1:
                return (
                    'NAME   NAMESPACE\nvm1   default',
                    '',
                )
            return ('', '')

        self.mock_cutils.trycmd.side_effect = (
            trycmd_side_effect
        )
        dw_call_count = [0]

        def dw_side_effect(*args, **_):
            """Return False for first vm call."""
            dw_call_count[0] += 1
            if (
                dw_call_count[0] == 1
                and args[0] == 'vm'
            ):
                return False
            return True

        with patch.object(
            self.operator, '_delete_and_wait',
            side_effect=dw_side_effect,
        ):
            app = MagicMock()
            app.name = 'kubevirt'
            self.operator.pre_remove(app)
            force_calls = [
                c for c in
                self.mock_cutils.trycmd.call_args_list
                if '--force' in c[0]
            ]
            self.assertGreater(len(force_calls), 0)


class TestPreRemoveWebhooks(LifecycleTestBase):
    """Tests for pre_remove webhook filtering."""

    def setUp(self):
        """Set up with helper methods patched."""
        super().setUp()
        patch.object(
            KubeVirtAppLifecycleOperator,
            '_delete_and_wait',
            return_value=True,
        ).start()
        patch.object(
            KubeVirtAppLifecycleOperator,
            '_wait_for_deletion',
            return_value=True,
        ).start()

    def test_filters_kubevirt_webhooks_only(self):
        """Verify only kubevirt/cdi webhooks deleted."""
        call_count = [0]

        def trycmd_side_effect(*args):
            """Return webhook list for get calls."""
            call_count[0] += 1
            if call_count[0] == 1:
                return ('No resources found', '')
            if (
                'get' in args
                and 'validatingwebhookconfigurations'
                in args
            ):
                return (
                    'virt-api-validator\n'
                    'unrelated-webhook\n'
                    'cdi-api-datavolume-validate\n',
                    '',
                )
            if (
                'get' in args
                and 'mutatingwebhookconfigurations'
                in args
            ):
                return ('', '')
            return ('', '')

        self.mock_cutils.trycmd.side_effect = (
            trycmd_side_effect
        )
        app = MagicMock()
        app.name = 'kubevirt'
        self.operator.pre_remove(app)
        all_args = [
            str(c) for c in
            self.mock_cutils.trycmd.call_args_list
        ]
        deleted = [
            a for a in all_args
            if 'unrelated-webhook' in a
            and 'delete' in a
        ]
        self.assertEqual(deleted, [])


class TestPreRemoveCRDRetry(LifecycleTestBase):
    """Tests for pre_remove CRD timeout retry."""

    def setUp(self):
        """Set up with helper methods patched."""
        super().setUp()
        patch.object(
            KubeVirtAppLifecycleOperator,
            '_delete_and_wait',
            return_value=True,
        ).start()
        patch.object(
            KubeVirtAppLifecycleOperator,
            '_wait_for_deletion',
            return_value=True,
        ).start()

    def test_crd_timeout_triggers_finalizer_strip(
        self,
    ):
        """Verify CRD timeout triggers patch+retry."""
        call_count = [0]

        def trycmd_side_effect(*args):
            """Simulate CRD delete timeout."""
            call_count[0] += 1
            if call_count[0] == 1:
                return ('No resources found', '')
            if 'get' in args and 'crd' in args:
                return (
                    'crd/kubevirts.kubevirt.io\n', ''
                )
            if (
                'delete' in args
                and 'kubevirts.kubevirt.io' in str(args)
                and '--timeout=30s' in args
            ):
                return ('', 'error: timeout waiting')
            return ('', '')

        self.mock_cutils.trycmd.side_effect = (
            trycmd_side_effect
        )
        app = MagicMock()
        app.name = 'kubevirt'
        self.operator.pre_remove(app)
        patch_calls = [
            c for c in
            self.mock_cutils.trycmd.call_args_list
            if 'patch' in c[0]
            and 'finalizers' in str(c[0])
        ]
        self.assertGreater(len(patch_calls), 0)


class TestPreRemoveAPIServices(LifecycleTestBase):
    """Tests for pre_remove APIService cleanup."""

    def setUp(self):
        """Set up with helper methods patched."""
        super().setUp()
        patch.object(
            KubeVirtAppLifecycleOperator,
            '_delete_and_wait',
            return_value=True,
        ).start()
        patch.object(
            KubeVirtAppLifecycleOperator,
            '_wait_for_deletion',
            return_value=True,
        ).start()

    def test_filters_kubevirt_apiservices(self):
        """Verify only kubevirt.io apiservices deleted."""
        call_count = [0]

        def trycmd_side_effect(*args):
            """Return apiservice list."""
            call_count[0] += 1
            if call_count[0] == 1:
                return ('No resources found', '')
            if (
                'get' in args
                and 'apiservice' in args
            ):
                return (
                    'apiservice/v1.kubevirt.io\n'
                    'apiservice/v1.other.io\n',
                    '',
                )
            return ('', '')

        self.mock_cutils.trycmd.side_effect = (
            trycmd_side_effect
        )
        app = MagicMock()
        app.name = 'kubevirt'
        self.operator.pre_remove(app)
        all_args = [
            str(c) for c in
            self.mock_cutils.trycmd.call_args_list
        ]
        other_deleted = [
            a for a in all_args
            if 'v1.other.io' in a and 'delete' in a
        ]
        self.assertEqual(other_deleted, [])
