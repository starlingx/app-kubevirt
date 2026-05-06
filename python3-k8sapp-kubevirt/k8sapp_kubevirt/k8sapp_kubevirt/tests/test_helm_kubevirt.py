#
# Copyright (c) 2026 Wind River Systems, Inc.
#
# SPDX-License-Identifier: Apache-2.0
#
"""Tests for helm/kubevirt.py module.

Covers KubeVirtHelm get_overrides method behavior
including namespace handling, emulation, and replicas.
"""

from unittest.mock import MagicMock
from unittest.mock import patch

from k8sapp_kubevirt.common import constants as app_constants
from k8sapp_kubevirt.helm.kubevirt import KubeVirtHelm
from k8sapp_kubevirt.tests.helm_base import HelmOverrideTestBase


class TestKubeVirtHelmOverrides(HelmOverrideTestBase):
    """Tests for KubeVirtHelm get_overrides method."""

    CHART_NAMESPACE = app_constants.HELM_CHART_KUBEVIRT

    def setUp(self):
        """Set up test helm instance with patches."""
        self.mock_utils = patch(
            'k8sapp_kubevirt.helm.kubevirt.utils'
        ).start()
        patch(
            'k8sapp_kubevirt.helm.kubevirt.dbapi'
        ).start()
        self.helm = KubeVirtHelm(MagicMock())

    def tearDown(self):
        """Stop all patches."""
        patch.stopall()

    def test_get_overrides_no_namespace(self):
        """Verify get_overrides returns all keys."""
        self._test_get_overrides_no_namespace()

    def test_get_overrides_kubevirt_namespace(self):
        """Verify overrides for kubevirt namespace."""
        overrides = self._get_overrides(
            namespace=app_constants.HELM_CHART_KUBEVIRT,
        )
        self.assertIn('featureGates', overrides)
        self.assertIn(
            'Snapshot', overrides['featureGates']
        )

    def test_get_overrides_release_ns(self):
        """Verify release namespace returns empty."""
        self._test_get_overrides_release_ns()

    def test_get_overrides_invalid_namespace(self):
        """Verify raises for invalid namespace."""
        self._test_get_overrides_invalid_namespace()

    def test_overrides_use_emulation_virtual(self):
        """Verify useEmulation True when virtual."""
        self.mock_utils.is_virtual.return_value = True
        self.mock_utils.is_single_controller.return_value = (
            False
        )
        overrides = self.helm.get_overrides()
        kubevirt_overrides = overrides[
            app_constants.HELM_CHART_KUBEVIRT
        ]
        self.assertTrue(
            kubevirt_overrides['useEmulation']
        )

    def test_overrides_use_emulation_physical(self):
        """Verify useEmulation False when physical."""
        self.mock_utils.is_virtual.return_value = False
        self.mock_utils.is_single_controller.return_value = (
            False
        )
        overrides = self.helm.get_overrides()
        kubevirt_overrides = overrides[
            app_constants.HELM_CHART_KUBEVIRT
        ]
        self.assertFalse(
            kubevirt_overrides['useEmulation']
        )

    def test_overrides_single_controller_replicas(self):
        """Verify replicas is 1 for single controller."""
        self._test_replicas_single_controller()

    def test_overrides_multi_controller_replicas(self):
        """Verify replicas is 2 for multi controller."""
        self._test_replicas_multi_controller()
