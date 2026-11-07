# CUE schema for validating Kubernetes / IaC manifests in white-label deployments.
# Validate with: cue vet -c manifest.cue input.yaml

package whitelabel

import (
	"strconv"
)

# --- Base manifest shape ---
manifest: {
	apiVersion: string
	kind:       string
	metadata: {
		name:      string
		namespace?: string
		labels?:   [string]: string
		annotations?: [string]: string
	}
	spec?: {...}
}

# --- Pod constraints ---
pod: {
	apiVersion: "v1"
	kind: "Pod"
	metadata: {
		name:      string
		labels?:   [string]: string
	}
	spec: {
		containers: [...{
			name:         string
			image:        string
			imagePullPolicy?: "Always" | "IfNotPresent" | "Never"
			command?:     [...string]
			args?:        [...string]
			env?:         [...{
				name:  string
				value?: string
				valueFrom?: {...}
			}]
			resources: {
				limits: {
					cpu:    string
					memory: string
				}
				requests?: {
					cpu:    string
					memory: string
				}
			}
			securityContext?: {
				runAsNonRoot?: bool
				runAsUser?:    int
				privileged?:   bool
				readOnlyRootFilesystem?: bool
				allowPrivilegeEscalation?: bool
			}
			livenessProbe?: {...}
			readinessProbe?: {...}
			ports?: [...{
				containerPort: int
				protocol?:     "TCP" | "UDP"
				name?:         string
			}]
		}]
		restartPolicy?: "Always" | "OnFailure" | "Never"
	}
}

// --- Constraints ---

# 1. Image must not use :latest in production
pod.spec.containers[i]: image != *":latest" | _: image != "latest"

// 2. Non-root enforcement
pod.spec.containers[i].securityContext?: runAsNonRoot: true

// 3. Resource limits required
pod.spec.containers[i]: resources.limits != _|_

// 4. Liveness probe required for webchat
pod: {
	if metadata.labels.app == "webchat-server" {
		spec.containers[i]: {
			if name == "webchat" {
				livenessProbe:  _|_
				readinessProbe: _|_
			}
		}
	}
}

# --- NetworkPolicy constraints ---
networkPolicy: {
	apiVersion: "v1" | "networking.k8s.io/v1"
	kind: "NetworkPolicy"
	metadata: {
		name:      string
		namespace?: string
	}
	spec: {
		podSelector: {
			matchLabels: [string]: string
		}
		ingress?: [...{
			ports?: [...{
				protocol: "TCP" | "UDP"
				port:     int
			}]
			from?: [...{
				podSelector?: {
					matchLabels: [string]: string
				}
				namespaceSelector?: {
					matchLabels: [string]: string
				}
			}]
		}]
	}
}

# 5. Payment service must have explicit ingress rules
networkPolicy: {
	if spec.podSelector.matchLabels.app == "payment-service" {
		spec.ingress: _|_
	}
}
