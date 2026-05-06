#
# Copyright (c) 2026 Wind River Systems, Inc.
#
# SPDX-License-Identifier: Apache-2.0
#
"""Tests for pre_downgrade and _get_active_vmis.

Covers VMI blocking logic before downgrade and
kubectl output parsing for active VMI detection.
"""

from unittest.mock import MagicMock
from unittest.mock import patch

from sysinv.helm.lifecycle_constants import LifecycleConstants

from k8sapp_kubevirt.lifecycle.lifecycle_kubevirt import \
    KubeVirtAppLifecycleOperator
from k8sapp_kubevirt.tests.lifecycle_base import LifecycleTestBase


class TestPreDowngrade(LifecycleTestBase):
    """Tests for pre_downgrade method.

    Validates VMI checks before allowing downgrade.
    """

    @patch.object(
        KubeVirtAppLifecycleOperator,
        '_get_active_vmis',
        return_value=[],
    )
    def test_pre_downgrade_no_active_vmis(
        self, _mock_vmis
    ):
        """Verify pre_downgrade passes with no VMIs."""
        hook_info = MagicMock()
        hook_info.extra = {
            LifecycleConstants.FROM_APP_VERSION: '1.2',
            LifecycleConstants.TO_APP_VERSION: '1.1',
        }
        self.operator.pre_downgrade(hook_info)

    @patch.object(
        KubeVirtAppLifecycleOperator,
        '_get_active_vmis',
        return_value=['default/test-vm'],
    )
    def test_pre_downgrade_active_vmis_raises(
        self, _mock_vmis
    ):
        """Verify pre_downgrade raises with active VMIs."""
        hook_info = MagicMock()
        hook_info.extra = {
            LifecycleConstants.FROM_APP_VERSION: '1.2',
            LifecycleConstants.TO_APP_VERSION: '1.1',
        }
        with self.assertRaises(RuntimeError) as ctx:
            self.operator.pre_downgrade(hook_info)
        self.assertIn(
            'Cannot downgrade', str(ctx.exception)
        )
        self.assertIn(
            'default/test-vm', str(ctx.exception)
        )

    @patch.object(
        KubeVirtAppLifecycleOperator,
        '_get_active_vmis',
        return_value=['ns1/vm1', 'ns2/vm2'],
    )
    def test_pre_downgrade_multiple_active_vmis(
        self, _mock_vmis
    ):
        """Verify all active VMIs listed in error."""
        hook_info = MagicMock()
        hook_info.extra = {
            LifecycleConstants.FROM_APP_VERSION: '2.0',
            LifecycleConstants.TO_APP_VERSION: '1.0',
        }
        with self.assertRaises(RuntimeError) as ctx:
            self.operator.pre_downgrade(hook_info)
        self.assertIn('ns1/vm1', str(ctx.exception))
        self.assertIn('ns2/vm2', str(ctx.exception))


class TestGetActiveVmis(LifecycleTestBase):
    """Tests for _get_active_vmis method.

    Validates parsing of kubectl output to identify
    active VMI instances by phase.
    """

    def test_empty_stdout_returns_empty(self):
        """Verify empty kubectl output returns []."""
        self.mock_cutils.trycmd.return_value = ('', '')
        result = self.operator._get_active_vmis()
        self.assertEqual(result, [])

    def test_none_stdout_returns_empty(self):
        """Verify None stdout returns []."""
        self.mock_cutils.trycmd.return_value = (None, '')
        result = self.operator._get_active_vmis()
        self.assertEqual(result, [])

    def test_whitespace_only_returns_empty(self):
        """Verify whitespace-only stdout returns []."""
        self.mock_cutils.trycmd.return_value = (
            '   \n  ', ''
        )
        result = self.operator._get_active_vmis()
        self.assertEqual(result, [])

    def test_running_vmi_detected(self):
        """Verify Running VMI is detected as active."""
        self.mock_cutils.trycmd.return_value = (
            'default/my-vm=Running\n', ''
        )
        result = self.operator._get_active_vmis()
        self.assertEqual(result, ['default/my-vm'])

    def test_scheduling_vmi_detected(self):
        """Verify Scheduling VMI is detected."""
        self.mock_cutils.trycmd.return_value = (
            'ns1/vm1=Scheduling\n', ''
        )
        result = self.operator._get_active_vmis()
        self.assertEqual(result, ['ns1/vm1'])

    def test_scheduled_vmi_detected(self):
        """Verify Scheduled VMI is detected."""
        self.mock_cutils.trycmd.return_value = (
            'ns1/vm1=Scheduled\n', ''
        )
        result = self.operator._get_active_vmis()
        self.assertEqual(result, ['ns1/vm1'])

    def test_succeeded_vmi_not_active(self):
        """Verify Succeeded VMI is not active."""
        self.mock_cutils.trycmd.return_value = (
            'ns1/vm1=Succeeded\n', ''
        )
        result = self.operator._get_active_vmis()
        self.assertEqual(result, [])

    def test_failed_vmi_not_active(self):
        """Verify Failed VMI is not active."""
        self.mock_cutils.trycmd.return_value = (
            'ns1/vm1=Failed\n', ''
        )
        result = self.operator._get_active_vmis()
        self.assertEqual(result, [])

    def test_multiple_vmis_mixed_phases(self):
        """Verify mixed phases filter correctly."""
        self.mock_cutils.trycmd.return_value = (
            'ns1/vm1=Running\n'
            'ns2/vm2=Failed\n'
            'ns3/vm3=Scheduling\n',
            '',
        )
        result = self.operator._get_active_vmis()
        self.assertEqual(
            result, ['ns1/vm1', 'ns3/vm3']
        )

    def test_blank_lines_skipped(self):
        """Verify blank lines in output are skipped."""
        self.mock_cutils.trycmd.return_value = (
            '\nns1/vm1=Running\n\n\n', ''
        )
        result = self.operator._get_active_vmis()
        self.assertEqual(result, ['ns1/vm1'])

    def test_line_without_equals_skipped(self):
        """Verify lines without = are skipped."""
        self.mock_cutils.trycmd.return_value = (
            'garbage-line\nns1/vm1=Running\n', ''
        )
        result = self.operator._get_active_vmis()
        self.assertEqual(result, ['ns1/vm1'])
