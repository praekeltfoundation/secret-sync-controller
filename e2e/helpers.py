import logging
import subprocess
import uuid
from contextlib import contextmanager

import pykube
import yaml
from kopf.config import WatchersConfig
from kopf.testing import KopfRunner


def pykube_config():
    return pykube.KubeConfig.from_env()


class KubeHelper:
    def __init__(self):
        self._api = pykube.HTTPClient(pykube_config())
        self._ns = f"secret-sync-e2etest-{uuid.uuid4()}"

    def _get_namespace(self):
        return pykube.Namespace.objects(self._api).get(name=self._ns)

    def _create_namespace(self):
        body = {"metadata": {"name": self._ns}}
        return pykube.Namespace(self._api, body).create()

    def _delete_namespace(self):
        nsobj = self._get_namespace()
        if nsobj:
            return nsobj.delete()

    def list_secrets(self):
        return pykube.Secret.objects(self._api).filter(namespace=self._ns)

    def _prepare_yaml(self, body):
        body["metadata"]["namespace"] = self._ns
        return yaml.dump(body).encode("utf8")

    def _kubectl(self, args, input):
        args = ["kubectl", *args]
        subprocess.run(args, check=True, input=input, capture_output=True)

    def kubectl_apply(self, body):
        self._kubectl(["apply", "-f", "-"], self._prepare_yaml(body))

    def kubectl_delete(self, body):
        self._kubectl(["delete", "-f", "-"], self._prepare_yaml(body))


@contextmanager
def namespaced_kube_helper():
    kube = KubeHelper()
    kube._create_namespace()
    try:
        yield kube
    finally:
        kube._delete_namespace()


@contextmanager
def kopf_runner(kube):
    logger = logging.getLogger()
    old_handlers = logger.handlers[:]
    args = [
        "--verbose",
        "--standalone",
        "--namespace",
        kube._ns,
        "-m",
        "secret_sync.handlers",
    ]
    # Set the kopf watcher stream timeout to something small so we don't have
    # to wait too long at the end of the tests for all the background watcher
    # threads to end.
    old_stream_timeout = WatchersConfig.default_stream_timeout
    WatchersConfig.default_stream_timeout = 2
    try:
        with KopfRunner(["run", *args]) as runner:
            # Remove any extra log handlers that starting kopf may have added.
            # The built-in pytest log capture does what we need already.
            for handler in logger.handlers[:]:
                if handler not in old_handlers:
                    logger.removeHandler(handler)
            yield runner
    finally:
        WatchersConfig.default_stream_timeout = old_stream_timeout
        # The runner captures all output, so print it for pytest to capture.
        print(runner.stdout)
