# Copyright (c) 2023 Wind River Systems, Inc.
#
# SPDX-License-Identifier: Apache-2.0
#

"""
This module provides functionality related to KubeVirt Helm charts and deployment.
"""

from oslo_log import log as logging
from sysinv.common import exception
from sysinv.common import utils
from sysinv.db import api as dbapi
from sysinv.helm import base

from k8sapp_kubevirt.common import constants as app_constants

LOG = logging.getLogger(__name__)


class KubeVirtHelm(base.FluxCDBaseHelm):
    """Class to encapsulate helm operations for the kubevirt chart"""

    CHART = app_constants.HELM_CHART_KUBEVIRT
    HELM_RELEASE = app_constants.HELM_RELEASE_KUBEVIRT
    SERVICE_NAME = 'kubevirt'

    SUPPORTED_NAMESPACES = base.BaseHelm.SUPPORTED_NAMESPACES + \
        [app_constants.HELM_NS_KUBEVIRT] + [app_constants.HELM_NS_CDI] + \
        [app_constants.HELM_RELEASE_NS]

    SUPPORTED_APP_NAMESPACES = {
        app_constants.HELM_APP_KUBEVIRT:
            base.BaseHelm.SUPPORTED_NAMESPACES + [app_constants.HELM_RELEASE_NS] +
            [app_constants.HELM_CHART_KUBEVIRT] + [app_constants.HELM_NS_CDI],

    }

    def get_namespaces(self):
        """Returns the supported namespaces for this application."""
        return self.SUPPORTED_NAMESPACES

    def get_overrides(self, namespace=None):
        """Returns application overrides for the given namespace parameter.

        :param namespace: The namespace for which overrides are requested (optional).
        :return: Application overrides.
        """
        overrides = {
            app_constants.HELM_RELEASE_NS: {},
            app_constants.HELM_CHART_KUBEVIRT: {
                'featureGates': ['Snapshot'],
                'useEmulation': utils.is_virtual(),
                'replicas': '1' if utils.is_single_controller(dbapi.get_instance()) else '2',
                app_constants.HELM_CHART_COMPONENT_LABEL:
                    app_constants.HELM_CHART_COMPONENT_PLATFORM,
                'certificateRotate': {
                    'ca': {
                        'duration': app_constants.KUBEVIRT_CERTIFICATE_ROTATE_CA_DURATION,
                        'renewBefore': app_constants.KUBEVIRT_CERTIFICATE_ROTATE_CA_RENEW_BEFORE,
                    },
                    'server': {
                        'duration': app_constants.KUBEVIRT_CERTIFICATE_ROTATE_SERVER_DURATION,
                        'renewBefore':
                        app_constants.KUBEVIRT_CERTIFICATE_ROTATE_SERVER_RENEW_BEFORE,
                    }
                }
            },
            app_constants.HELM_CHART_CDI: {
                'featureGates': ['HonorWaitForFirstConsumer'],
                'replicas': '1' if utils.is_single_controller(dbapi.get_instance()) else '2',
                app_constants.HELM_CHART_COMPONENT_LABEL:
                    app_constants.HELM_CHART_COMPONENT_PLATFORM,
                'certificateRotate': {
                    'ca': {
                        'duration': app_constants.CDI_CERTIFICATE_ROTATE_CA_DURATION,
                        'renewBefore': app_constants.CDI_CERTIFICATE_ROTATE_CA_RENEW_BEFORE,
                    },
                    'server': {
                        'duration': app_constants.CDI_CERTIFICATE_ROTATE_SERVER_DURATION,
                        'renewBefore': app_constants.CDI_CERTIFICATE_ROTATE_SERVER_RENEW_BEFORE,
                    },
                    'client': {
                        'duration': app_constants.CDI_CERTIFICATE_ROTATE_CLIENT_DURATION,
                        'renewBefore': app_constants.CDI_CERTIFICATE_ROTATE_CLIENT_RENEW_BEFORE,
                    }
                }
            }
        }

        if namespace:
            if namespace in self.SUPPORTED_NAMESPACES:
                return overrides[namespace]
            raise exception.InvalidHelmNamespace(chart=self.CHART, namespace=namespace)
        return overrides
