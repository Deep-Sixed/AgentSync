Seed skills for tests. storage/skills/approved/ is runtime data and is
git-ignored, so tests read this committed tree instead:

- sailpoint-joiner-provisioning, jamf-ade-enrollment: valid approved skills
- broken-skill: SKILL.md without frontmatter (must be skipped)
- has-non-skill-file: directory without a SKILL.md (must be skipped)

Matcher tests depend on these descriptions (e.g. no word shared with
"water the office plants"), so keep them in step with tests/test_skill_server.py.
