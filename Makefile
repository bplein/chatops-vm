VERSION := $(shell cat VERSION)
IMAGE   := ghcr.io/bplein/chatops-vm

# Release flow: bump VERSION, `make sync-manifests`, commit, `git tag v<VERSION>`, push.
# Builds are done by GitHub Actions on push — there is no local docker build target.
.PHONY: sync-manifests
sync-manifests:
	sed -i '' -E "s|$(IMAGE):[^\" ]+|$(IMAGE):$(VERSION)|g" deploy/k8s/chatops-vm.yaml app/controller.py
	@echo "pinned at $(VERSION)"
