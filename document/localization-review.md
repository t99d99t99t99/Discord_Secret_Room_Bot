# Localization contribution and review process

Every member- and administrator-facing change must ship in Korean and English.
Do not treat English as a later translation task or use an internal identifier
as visible fallback copy.

## Before implementation

1. Add paired keys to `messages/en.yml` and `messages/ko.yml` for runtime
   messages. Keep format placeholders identical.
2. Add both-language command metadata to `CommandTranslator._copy` for every
   new application-command description and choice label.
3. Add or update both members of every public documentation pair. Preserve the
   same command references, operational phases, and safety warnings.

## Automated checks

Run `python3 -m unittest discover -s tests -v` with project dependencies.
`test_localization.py` rejects catalog key/placeholder drift and unlocalized
registered command descriptions. `test_documentation.py` verifies paired guides,
matching command references, and migration-guide phases.

## Human review

Before release, the owner (or an explicitly recorded Korean and English copy
reviewer) verifies tone, terminology, message length, and Discord rendering in
both languages. Record the reviewer, date, release version, and any approved
exceptions in the deployment record. The release checklist cannot be completed
without that approval.

## Fallback policy

The configured guild language is used first. Before it is stored, Discord's
locale is used when available; English is the safe fallback. A missing catalog
key is a programming error and must be fixed, not silently shown to members.
