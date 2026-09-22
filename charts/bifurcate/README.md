# Bifurcate Helm chart (experimental)

Install from the public OCI repository after a release is published:

```sh
helm install bifurcate oci://ghcr.io/bifurcate-systems/charts/bifurcate --version 0.1.0
```

The default install is a read-only, single-replica demonstration. The current
JSON store and global write token are **not tenant-isolated** and must not be
exposed as a shared production service. To enable writes in a private test
namespace, create an existing Secret with a `token` key and set
`auth.writeTokenSecretName`. To retain demonstration state across restarts,
enable `persistence.enabled`; this does not provide multi-writer safety.

No ingress, cluster-scoped RBAC, or CubeSandbox permissions are installed by
this chart. Production promotion requires tenant-scoped identity, durable
transactional storage, Cube adapter integration and the RE8CH GitOps review.
