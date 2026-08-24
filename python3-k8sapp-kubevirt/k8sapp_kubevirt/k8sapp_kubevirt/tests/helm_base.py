#
# Copyright (c) 2026 Wind River Systems, Inc.
#
# SPDX-License-Identifier: Apache-2.0
#
"""Shared base class for helm override tests.

Provides reusable test methods for common helm
override patterns: namespace dispatch, invalid
namespace handling, replica logic, and validation
that override keys match helm values.yaml structure.
"""

import os
import unittest

from sysinv.common import exception
import yaml

from k8sapp_kubevirt.common import constants as app_constants

REPO_ROOT = os.path.dirname(os.path.dirname(
    os.path.dirname(os.path.dirname(
        os.path.dirname(os.path.abspath(__file__))
    ))
))


class HelmOverrideTestBase(unittest.TestCase):
    """Base class for helm override tests.

    Subclasses must define:
      - helm: the helm instance under test
      - CHART_NAMESPACE: the primary chart namespace
      - mock_utils: patched utils module (via setUp)
      - VALUES_YAML_PATH: path to chart values.yaml
        relative to REPO_ROOT
    """

    helm = None
    CHART_NAMESPACE = None
    mock_utils = None
    VALUES_YAML_PATH = None

    def _load_values_yaml_keys(self):
        """Load top-level keys from values.yaml."""
        values_path = os.path.join(
            REPO_ROOT, self.VALUES_YAML_PATH
        )
        with open(values_path, 'r',
                  encoding='utf-8') as values_file:
            return set(yaml.safe_load(values_file).keys())

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

    def _test_no_namespace_has_single_chart_key(self):
        """Verify no-namespace returns single chart key."""
        overrides = self._get_overrides()
        self.assertEqual(
            list(overrides.keys()),
            [self.CHART_NAMESPACE],
        )

    def _test_override_keys_match_values_yaml(self):
        """Verify override keys exist in values.yaml."""
        values_keys = self._load_values_yaml_keys()
        overrides = self._get_overrides(
            namespace=self.CHART_NAMESPACE,
        )
        for key in overrides:
            if key == app_constants.HELM_CHART_COMPONENT_LABEL:
                continue
            self.assertIn(key, values_keys)
