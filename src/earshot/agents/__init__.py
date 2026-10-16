"""Stream 1 executors: Spotter, Caller, Checker, Director (and later Analyst, StatWatcher).

Each executor is a plain class or function over the contract, so the Agent Framework workflow
(Stream 2) can wrap it without the executor knowing about the framework.
"""
