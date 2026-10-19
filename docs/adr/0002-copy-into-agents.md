# Install by copying into ~/.agents, not by linking to the clone

The Pool goes to `~/.agents/skills` and the engine to `~/.agents/mifal`, as copies. `~/.agents/skills` is the folder most harnesses already read natively, and the validator, doctor and skills resolve their paths from it, so no relocation is needed. Copies mean deleting or moving the clone never breaks an install; the cost is that an edit in the clone takes effect only through `mifal update` or a reinstall.
