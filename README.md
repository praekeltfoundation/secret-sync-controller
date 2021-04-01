# secret-sync-controller
Kubernetes controller for syncing data fields from one secret to another.

To sync fields, add a `secret-sync.praekelt.org/sync-to` annotation to the
source secret with a comma-separated list of destination secrets as the value.
Destinations in other namespaces may be specified as `<namespace>/<secret>`.

Any fields that exist in the source secret will be copied to the
destination(s), overwriting whatever values may already be present. Any fields
that exist in a destination but not the source are left unmodified. The
controller sets a `secret-sync.praekelt.org/watch: true` annotation on all
destination secrets in order to track them and reconcile when either source or
destination changes.

Example:
```
apiVersion: v1
kind: Secret
metadata:
  name: source
  namespace: sourcens
  annotations:
    secret-sync.praekelt.org/sync-to: dest,destns/otherdest
data:
  foo: aGVsbG8=
```

NOTE: Destination secrets must already exist, the controller won't create them.
