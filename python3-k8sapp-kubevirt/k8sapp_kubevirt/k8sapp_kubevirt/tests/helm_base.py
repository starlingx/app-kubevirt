#
# Copyright (c) 2026 Wind River Systems, Inc.
#
# SPDX-License-Identifier: Apache-2.0
#
"""Shared base class for helm override tests.

Provides reusable test methods for common helm
override patterns: namespace dispatch, invalid
namespace handling, and replica logic.
"""

import unittest

from sysinv.common import exception

from k8sapp_kubevirt.common import constants as app_constants


class HelmOverrideTestBase(unittest.TestCase):
    """Base class for helm override tests.

    Subclasses must define:
      - helm: the helm instance under test
      - CHART_NAMESPACE: the primary chart namespace
      - mock_utils: patched utils module (via setUp)
    """

    helm = None
    CHART_NAMESPACE = None
    mock_utils = None

    def _get_overrides(self, namespace=None,
                       single_controller=False):
        """Helper to call get_overrides with mocked utils.

        namespace -- optional namespace to pass
        single_controller -- whether single controller

        Returns the overrides dict.
        """
        self.mock_utils.is_virtual.return_value = False
        self.mock_utils.is_single_controller.return_value = (
            single_controller
        )
        if namespace:
            return self.helm.get_overrides(
                namespace=namespace
            )
        return self.helm.get_overrides()

    def _test_get_overrides_no_namespace(self):
        """Verify get_overrides returns all keys."""
        overrides = self._get_overrides()
        self.assertIn(
            app_constants.HELM_RELEASE_NS, overrides
        )
        self.assertIn(
            self.CHART_NAMESPACE, overrides
        )

    def _test_get_overrides_release_ns(self):
        """Verify release namespace returns empty."""
        overrides = self._get_overrides(
            namespace=app_constants.HELM_RELEASE_NS,
        )
        self.assertEqual(overrides, {})

    def _test_get_overrides_invalid_namespace(self):
        """Verify invalid namespace raises."""
        self.mock_utils.is_virtual.return_value = False
        self.mock_utils.is_single_controller.return_value = (
            False
        )
        with self.assertRaises(
            exception.InvalidHelmNamespace
        ):
            self.helm.get_overrides(
                namespace='invalid-ns'
            )

    def _test_replicas_single_controller(self):
        """Verify replicas is 1 for single controller."""
        overrides = self._get_overrides(
            namespace=self.CHART_NAMESPACE,
            single_controller=True,
        )
        self.assertEqual(overrides['replicas'], '1')

    def _test_replicas_multi_controller(self):
        """Verify replicas is 2 for multi controller."""
        overrides = self._get_overrides(
            namespace=self.CHART_NAMESPACE,
            single_controller=False,
        )
        self.assertEqual(overrides['replicas'], '2')
