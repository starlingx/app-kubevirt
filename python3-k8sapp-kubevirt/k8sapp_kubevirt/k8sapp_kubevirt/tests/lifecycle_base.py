#
# Copyright (c) 2026 Wind River Systems, Inc.
#
# SPDX-License-Identifier: Apache-2.0
#
"""Shared base class for lifecycle operator tests.

Provides common setUp/tearDown with cutils patched
to prevent real subprocess calls during tests.
"""

import unittest
from unittest.mock import patch

from k8sapp_kubevirt.lifecycle import lifecycle_kubevirt

KubeVirtAppLifecycleOperator = (
    lifecycle_kubevirt.KubeVirtAppLifecycleOperator
)


class LifecycleTestBase(unittest.TestCase):
    """Base class for lifecycle operator tests.

    Patches cutils to prevent real kubectl calls.
    Subclasses can access self.mock_cutils and
    self.operator directly.
    """

    def setUp(self):
        """Set up operator with cutils patched."""
        self.mock_cutils = patch(
            'k8sapp_kubevirt.lifecycle'
            '.lifecycle_kubevirt.cutils'
        ).start()
        self.mock_cutils.trycmd.return_value = ('', '')
        self.operator = KubeVirtAppLifecycleOperator()

    def tearDown(self):
        """Stop all patches."""
        patch.stopall()
