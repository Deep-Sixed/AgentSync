---
name: jamf-ade-enrollment
description: Enrolls Apple devices into Jamf Pro through Automated Device Enrollment. Use when devices must be enrolled with a prestage enrollment.
skill_family: apple-device-management
capability_family: endpoint-management
---

# Overview
Enrolls devices assigned in Apple Business Manager into Jamf Pro.

# When to Use
Use when new Apple devices need zero-touch enrollment.

# Process
1. Assign devices to a Jamf Pro prestage enrollment.
2. Scope configuration profiles to a smart group.
3. Enroll a test device first.

# Verification
- test_device_result recorded for one enrolled device
- scope_definition lists targeted smart groups
