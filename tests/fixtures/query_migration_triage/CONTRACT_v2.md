# Query serialization contract, v2

Effective 2025-07-01 and superseding v1 for current application requests. Preserve query pair order and values. Encode query pairs with `application/x-www-form-urlencoded` semantics: a space is `+`, while a literal plus remains `%2B`. The v1 golden remains a historical record and is not the v2 expected output.
