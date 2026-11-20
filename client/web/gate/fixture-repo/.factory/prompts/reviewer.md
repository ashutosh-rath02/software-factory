You are reviewing {ticket_id}.

The ticket is at {ticket_path}, in the repository at {repo_root}.
It reached GATE in {triggered_by_run}.

Nothing in this file is read by the tool beyond the four placeholders above — it exists so the
gate fixture's `roles` block names a prompt that is really there, and so `E2`'s
`prompt_missing` refusal is proved not to be firing.
