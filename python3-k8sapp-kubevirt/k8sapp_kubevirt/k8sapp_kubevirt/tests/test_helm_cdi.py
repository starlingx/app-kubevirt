#
# Copyright (c) 2026 Wind River Systems, Inc.
#
# SPDX-License-Identifier: Apache-2.0
#
"""Tests for helm/cdi.py module.

Covers CdiHelm get_overrides method behavior
including namespace handling and replicas.
"""

from unittest.mock import MagicMock
from unittest.mock import patch

from k8sapp_kubevirt.common import constants as app_constants
from k8sapp_kubevirt.helm.cdi import CdiHelm
from k8sapp_kubevirt.tests.helm_base import HelmOverrideTestBase


class TestCdiHelmOverrides(HelmOverrideTestBase):
    """Tests for CdiHelm get_overrides method."""

    CHART_NAMESPACE = app_constants.HELM_NS_CDI
    VALUES_YAML_PATH = (
        'helm-charts/custom/cdi-helm/'
        'cdi-helm/cdi/values.yaml'
    )

    def setUp(self):
        """Set up test helm instance with patches."""
        self.mock_utils = patch(
            'k8sapp_kubevirt.helm.cdi.utils'
        ).start()
        patch('k8sapp_kubevirt.helm.cdi.dbapi').start()
        self.helm = CdiHelm(MagicMock())

    def tearDown(self):
        """Stop all patches."""
        patch.stopall()

    def test_get_overrides_no_namespace(self):
        """Verify get_overrides returns all keys."""
        self._test_get_overrides_no_namespace()

    def test_get_overrides_cdi_namespace(self):
        """Verify overrides for cdi namespace."""
        overrides = self._get_overrides(
            namespace=app_constants.HELM_NS_CDI,
        )
        self.assertIn('featureGates', overrides)
        self.assertIn(
            'HonorWaitForFirstConsumer',
            overrides['featureGates'],
        )

    def test_get_overrides_release_ns(self):
        """Verify release namespace returns empty."""
        self._test_get_overrides_release_ns()

    def test_get_overrides_invalid_namespace(self):
        """Verify raises for invalid namespace."""
        self._test_get_overrides_invalid_namespace()

    def test_overrides_single_controller_replicas(self):
        """Verify replicas is 1 for single controller."""
        self._test_replicas_single_controller()

    def test_overrides_multi_controller_replicas(self):
        """Verify replicas is 2 for multi controller."""
        self._test_replicas_multi_controller()

    def test_no_namespace_has_single_chart_key(self):
        """Verify single namespace key in overrides."""
        self._test_no_namespace_has_single_chart_key()

    def test_override_keys_match_values_yaml(self):
        """Verify override keys exist in values.yaml."""
        self._test_override_keys_match_values_yaml()
