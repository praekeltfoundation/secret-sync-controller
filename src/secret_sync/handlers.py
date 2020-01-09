"""
Sync data fields between secrets.

TODO:
 * Sync when destination secret changes.
 * Sync to multiple destinations.
"""

from contextlib import contextmanager

import attr
import kopf
import pykube

ANNOTATION_PREFIX = "secret-sync.praekelt.org"
ANN_SYNC_TO = f"{ANNOTATION_PREFIX}/sync-to"
ANN_WATCH = f"{ANNOTATION_PREFIX}/watch"


_kcfg = None
_kapi = None


def add_annotation(obj, annotation, value):
    annotations = obj.setdefault("metadata", {}).setdefault("annotations", {})
    annotations[annotation] = value


@attr.s(frozen=True)
class SecretRef:
    """
    An immutable reference to a secret that we can perform various operations
    on.
    """

    namespace: str = attr.ib()
    name: str = attr.ib()

    def __str__(self):
        return f"{self.namespace}/{self.name}"

    @classmethod
    def from_meta(cls, meta):
        return cls(namespace=meta["namespace"], name=meta["namespace"])

    @classmethod
    def from_annotation(cls, meta):
        ann = meta["annotations"]
        return cls(namespace=meta["namespace"], name=ann[ANN_SYNC_TO])

    def _meta(self):
        return {"name": self.name, "namespace": self.namespace}

    def _as_pykube(self):
        return pykube.Secret(_kapi, {"metadata": self._meta()})

    @contextmanager
    def _existing_pykube(self, logger):
        try:
            yield self._as_pykube()
        except pykube.exceptions.HTTPError as e:
            if e.code != 404:
                raise
            logger.warning(f"Secret not found: {self}")

    def get(self, logger):
        with self._existing_pykube(logger) as secret:
            secret.reload()
        return secret

    def patch(self, logger, patch_obj):
        add_annotation(patch_obj, ANN_WATCH, "true")
        with self._existing_pykube(logger) as secret:
            secret.patch(patch_obj)
        return secret


@kopf.on.startup()
def auth_pykube(**_kw):
    """
    Create an authenticated pykube API client at startup.
    """
    global _kcfg, _kapi
    _kcfg = pykube.KubeConfig.from_env()
    _kapi = pykube.HTTPClient(_kcfg)


def copy_secret_data(src_secret, logger):
    """
    Copy data from source secret to destination secret.
    """
    dst_ref = SecretRef.from_annotation(src_secret["metadata"])
    to_secret = dst_ref.patch(logger, {"data": {**src_secret["data"]}})
    logger.info(f"CSD after: {to_secret.obj}")


@kopf.on.event("", "v1", "secrets", annotations={ANN_SYNC_TO: None})
def source_secret_event(body, event, logger, **_kw):
    logger.info(
        f"SOURCE EVENT! {event['type']}\n  BODY: {body}\n  EVENT: {event}"
    )
    copy_secret_data(body, logger)


@kopf.on.event("", "v1", "secrets", annotations={ANN_WATCH: None})
def watched_secret_event(meta, event, logger, **_kw):
    logger.debug(
        f"DEST EVENT! {event['type']} {meta['namespace']}/{meta['name']}"
    )
