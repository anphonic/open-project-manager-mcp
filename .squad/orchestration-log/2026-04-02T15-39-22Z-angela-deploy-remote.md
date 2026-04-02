# Angela: Deploying When Source Is on a Dev Machine

**Timestamp:** 2026-04-02T15-39-22Z

**Agent:** Angela

**Status:** COMPLETED

## Summary

Angela enhanced DEPLOY.md with comprehensive documentation for three methods to deploy when source code resides on a dev machine rather than the deployment server.

## Changes to DEPLOY.md

Added new section "Deploying When Source Is on a Dev Machine" covering:

1. **pip install from git remote (RECOMMENDED)**
   - Uses git+https:// URL with remote reference
   - Method: `pip install git+https://<remote>@<commit>`
   - Advantage: Clean, no local build artifacts transferred

2. **Build wheel locally, SCP to server, pip install**
   - Build .whl file on dev machine
   - Transfer via SCP to server
   - Install with pip on server
   - Advantage: Pre-validated build, can inspect before deployment

3. **rsync source, pip install -e .**
   - Synchronize source directory to server
   - Install in editable mode
   - Advantage: Development-friendly, supports iteration

## Impact

These methods enable flexible deployment workflows for developers working on source machines separate from production/staging servers.

## Related Commits

- Commit: d30f5ee
- Message: "docs: add remote-source deployment methods to DEPLOY.md"
