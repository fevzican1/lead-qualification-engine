package whitelabel.manifest

# =============================================================================
# Open Policy Agent (OPA) / Rego — Policy-as-Code for Kubernetes manifests,
# IaC files, and declarative configurations in white-label deployments.
#
# Compile/validate with: opa eval -i input.json -d manifest.rego "data.whitelabel.manifest"
# =============================================================================

# Deny any manifest that runs as root (security baseline)
deny[msg] {
    input.kind == "Pod"
    input.spec.securityContext.runAsNonRoot == false
    msg := sprintf("Pod %s must run as non-root (runAsNonRoot=true)", [input.metadata.name])
}

deny[msg] {
    input.kind == "Pod"
    container := input.spec.containers[_]
    not container.securityContext.runAsNonRoot
    msg := sprintf("Container %s in Pod %s must define runAsNonRoot=true", [container.name, input.metadata.name])
}

# Deny privileged containers
deny[msg] {
    input.kind == "Pod"
    container := input.spec.containers[_]
    container.securityContext.privileged == true
    msg := sprintf("Container %s in Pod %s must not be privileged", [container.name, input.metadata.name])
}

# Require resource limits on every container (prevent resource exhaustion)
deny[msg] {
    input.kind == "Pod"
    container := input.spec.containers[_]
    not container.resources.limits
    msg := sprintf("Container %s in Pod %s must define resource limits", [container.name, input.metadata.name])
}

# Require liveness and readiness probes on web-facing services
deny[msg] {
    input.kind == "Pod"
    input.metadata.labels.app == "webchat-server"
    container := input.spec.containers[_]
    container.name == "webchat"
    not container.livenessProbe
    msg := sprintf("Webchat container %s must define a livenessProbe", [container.name])
}

deny[msg] {
    input.kind == "Pod"
    input.metadata.labels.app == "webchat-server"
    container := input.spec.containers[_]
    container.name == "webchat"
    not container.readinessProbe
    msg := sprintf("Webchat container %s must define a readinessProbe", [container.name])
}

# Enforce image pinning (no :latest in production)
deny[msg] {
    input.kind == "Pod"
    container := input.spec.containers[_]
    endswith(container.image, ":latest")
    msg := sprintf("Container %s in Pod %s must not use :latest image tag", [container.name, input.metadata.name])
}

# Network policy required for payment-service
deny[msg] {
    input.kind == "NetworkPolicy"
    input.spec.podSelector.matchLabels.app == "payment-service"
    not input.spec.ingress
    msg := sprintf("Payment service NetworkPolicy %s must define ingress rules", [input.metadata.name])
}

# Allow: all checks passed
allow {
    not deny[_]
}
