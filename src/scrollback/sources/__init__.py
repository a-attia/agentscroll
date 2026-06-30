"""Source adapters: one per supported AI agent.

Each adapter implements `Source` (see base.py) and is registered in
`registry.py`. Adding support for a new agent means writing one adapter
and adding it to the registry -- nothing else in the program changes.
"""
