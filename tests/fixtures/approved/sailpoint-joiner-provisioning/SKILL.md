---
name: sailpoint-joiner-provisioning
description: Provisions birthright access for a new joiner in SailPoint IdentityIQ. Use when a joiner event needs its birthright entitlements granted.
skill_family: iam-lifecycle
capability_family: identity-governance
---

# Overview
Grants a new joiner their birthright entitlements through a SailPoint joiner workflow.

# When to Use
Use when an HR joiner event arrives and birthright access has not been provisioned yet.

# Process
1. Confirm the joiner identity exists in IdentityIQ.
2. Run the joiner workflow in dry-run mode and review its plan.
3. Run the joiner workflow for real.

# Verification
- dry_run_output reviewed before provisioning
- rollback_step recorded (disable the identity and revoke granted entitlements)
- approval_reference attached to the provisioning request
