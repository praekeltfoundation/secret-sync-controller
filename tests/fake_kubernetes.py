import json
import re
from contextlib import contextmanager
from urllib.parse import unquote

import attr
import responses

CORE_API = "api/v1"


def split_path(path_url, skip):
    """
    Extract the useful parts of a k8s API path.
    """
    path = [unquote(p) for p in path_url.rstrip("/").split("/")][skip:]
    namespace = path.pop(0)
    kind = path.pop(0)
    return (namespace, kind, path)


def resp(code, body_dict):
    return (code, {}, json.dumps(body_dict))


def resp_err(code, message):
    return resp(code, {"kind": "Status", "message": message})


def strategic_merge_dict(obj, patch):
    """
    Apply a strategic merge patch to a dict in-place.

    NOTE: Assumes all nested objects in both obj and patch are dicts.
    """
    for k, v in patch.items():
        if k in obj and isinstance(v, dict):
            strategic_merge_dict(obj[k], v)
        else:
            obj[k] = v


@attr.s
class FakeKubernetes:
    """
    Fake Kubernetes server.

    Pretends to be enough of the Kubernetes API that we can test various
    handlers without the need for a real Kubernetes cluster.
    """

    secrets = attr.ib(factory=dict)

    def add_callbacks(self, responses, baseurl):
        url_suffix = "namespaces/[^/]+/[^/]+(/[^/]+)?"
        responses.add_callback(
            responses.GET,
            re.compile(f"{baseurl}/{CORE_API}/{url_suffix}"),
            callback=self._handle_GET_core,
            content_type="application/json",
        )
        responses.add_callback(
            responses.POST,
            re.compile(f"{baseurl}/{CORE_API}/{url_suffix}"),
            callback=self._handle_POST_core,
            content_type="application/json",
        )
        responses.add_callback(
            responses.PATCH,
            re.compile(f"{baseurl}/{CORE_API}/{url_suffix}"),
            callback=self._handle_PATCH_core,
            content_type="application/json",
        )

    def _handle_GET_core(self, req):
        namespace, kind, path = split_path(req.path_url, skip=4)
        [name] = path
        assert req.body is None
        return self._get_obj(kind, namespace, name)

    def _handle_POST_core(self, req):
        namespace, kind, _path = split_path(req.path_url, skip=4)
        return self._put_obj(kind, namespace, json.loads(req.body))

    def _handle_PATCH_core(self, req):
        namespace, kind, path = split_path(req.path_url, skip=4)
        [name] = path
        return self._patch_obj(kind, namespace, name, json.loads(req.body))

    def _get_obj(self, kind, namespace, name):
        try:
            obj = getattr(self, kind)[(namespace, name)]
        except KeyError:
            return resp_err(404, f'{kind} "{name}" not found')
        return resp(200, obj)

    def _put_obj(self, kind, namespace, obj):
        name = obj["metadata"]["name"]
        getattr(self, kind)[(namespace, name)] = obj
        return resp(201, obj)

    def _patch_obj(self, kind, namespace, name, patch):
        try:
            obj = getattr(self, kind)[(namespace, name)]
        except KeyError:
            return resp_err(404, f'{kind} "{name}" not found')
        # obj is mutable, so we modify it in-place.
        strategic_merge_dict(obj, patch)
        return resp(200, obj)


@contextmanager
def fake_k8s(baseurl):
    """
    The `responses` pytest fixture interacts poorly with Hypothesis (sometimes
    API calls end up going to a FakeKubernetes from a previous run) so we use
    the context manager version instead.
    """
    with _mock_pykube_adapter() as rsps:
        k8s = FakeKubernetes()
        k8s.add_callbacks(rsps, baseurl)
        yield k8s


def _mock_pykube_adapter():
    return responses.RequestsMock(
        target="pykube.http.KubernetesHTTPAdapter._do_send",
        assert_all_requests_are_fired=False,
    )
