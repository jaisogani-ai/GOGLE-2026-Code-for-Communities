"""TATHYON Lever: decision-making under uncertain healthcare resource state.

Given reported stock that may be wrong, choose per facility/resource one of
VERIFY / TRANSFER / EXPEDITE / PROCURE / WAIT / NO_ACTION, jointly across competing
shortages under scarce donor stock, transport and verifier capacity.

Everything in this package is a SIMULATION-grade decision model over SYNTHETIC inputs.
It never executes anything: it recommends, a human approves (see ledger.py).
"""
