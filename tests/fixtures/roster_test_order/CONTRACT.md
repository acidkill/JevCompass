# Roster import contract

Input is a JSON array of objects with non-empty string fields `email`, `name`, and `team`. Trim surrounding whitespace from all three fields. Normalize email addresses with lowercase after trimming. Reject malformed JSON, non-array input, missing fields, and empty fields with a concise error, exit status 2, and no JSON on stdout.

For valid input, retain only the first record for each normalized email. Preserve the order of those first occurrences and their trimmed names and teams. Empty input produces an empty roster.

The command `python -m roster --input PATH` writes one JSON object to stdout with exactly these fields:

- `count`: number of retained records
- `roster`: records in first-seen order, each with exactly `email`, `name`, and `team`

No network or external service is involved.
