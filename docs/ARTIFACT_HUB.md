# Artifact Hub and OCI publication

The chart and its default runtime image are public. Each SemVer release tag
`vX.Y.Z` builds a multi-platform image at
`ghcr.io/bifurcate-systems/bifurcate:vX.Y.Z` and publishes a Helm chart at
`oci://ghcr.io/bifurcate-systems/charts/bifurcate` version `X.Y.Z`.

Register **that exact OCI chart URL** as one repository in Artifact Hub. After
registration, use the real repository ID from the Artifact Hub control panel
to publish `artifacthub-repo.yml` as the `artifacthub.io` OCI tag with the
official media types. Do not invent the ID, commit account details, or claim
Verified Publisher before the ownership challenge succeeds.

For RE8CH deployment, pin the released image and chart by immutable digest in
`re8ch/service-backend` and verify their GitHub OIDC signatures. No chart
release, Artifact Hub registration, or GitOps promotion should be performed
until the relevant release and security checks pass.

The initial `0.1.0` chart is experimental. It exposes no Ingress and defaults
to read-only operation. Production multi-tenant promotion requires scoped
identity and durable transactional storage; the chart alone does not provide
those capabilities.
