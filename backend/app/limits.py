"""How long a prompt may be. 100,000 characters is in practice no limit (the owner asked for none, 7 Nov);
it only stops a runaway paste from filling the database. The models read what fits their own encoders."""
PROMPT_MAX = 100_000
