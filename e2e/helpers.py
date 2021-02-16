import logging
import subprocess
import uuid
from contextlib import contextmanager

import kopf
import pykube
import yaml
from kopf.testing import KopfRunner


def pykube_config():
    return pykube.KubeConfig.from_env()


class KubeHelper:
    def __init__(self):
        self._api = pykube.HTTPClient(pykube_config())
        self._namespaces = set()
        self._ns = f"secret-sync-e2etest-{uuid.uuid4()}"

    def _get_namespace(self, name):
        return pykube.Namespace.objects(self._api).get(name=name)

    def _create_namespace(self, name):
        self._namespaces.add(name)
        body = {"metadata": {"name": name}}
        return pykube.Namespace(self._api, body).create()

    def _delete_namespaces(self):
        for name in self._namespaces:
            nsobj = self._get_namespace(name)
            nsobj.delete()

    def create_new_namespace(self):
        name = f"secret-sync-e2etest-{uuid.uuid4()}"
        self._create_namespace(name)
        return name

    def list_secrets(self, ns=None):
        if ns is None:
            ns = self._ns
        return pykube.Secret.objects(self._api).filter(namespace=ns)

    def _mk_meta(self, **fields):
        return {"namespace": self._ns, **fields}

    def ns_name(self, name):
        return f"{self._ns}/{name}"

    def patch_secret(self, name, patch):
        sec = pykube.Secret(self._api, {"metadata": self._mk_meta(name=name)})
        return sec.patch(patch)

    def delete_secret(self, name):
        sec = pykube.Secret(self._api, {"metadata": self._mk_meta(name=name)})
        return sec.delete()

    def _prepare_yaml(self, body):
        body["metadata"].setdefault("namespace", self._ns)
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
    kube._create_namespace(kube._ns)
    try:
        yield kube
    finally:
        kube._delete_namespaces()


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
    settings = kopf.OperatorSettings()
    settings.watching.server_timeout = 1
    try:
        with KopfRunner(["run", *args], settings=settings) as runner:
            # Remove any extra log handlers that starting kopf may have added.
            # The built-in pytest log capture does what we need already.
            for handler in logger.handlers[:]:
                if handler not in old_handlers:
                    logger.removeHandler(handler)
            yield runner
    finally:
        # The runner captures all output, so print it for pytest to capture.
        print(runner.stdout)
