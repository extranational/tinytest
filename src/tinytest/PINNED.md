# Pinned CyberPulse inputs

`native_adapter.py` imports CyberPulse from the checkout in `CYBERPULSE_PATH`. It
**refuses to run** unless that checkout's `git rev-parse HEAD` starts with
`cyberpulse_head` below (an unfilled TODO also refuses). Values measured on the host
by the orchestrator; to re-measure:

    git -C ~/repos/cyberpulse rev-parse HEAD
    sha256sum ~/repos/cyberpulse/src/cyberpulse/pipeline/enrich.py \
              ~/repos/cyberpulse/src/cyberpulse/pipeline/websearch.py

- cyberpulse_head: 368e2722de832d6e82711b08b5c7c49da9d2c46e
- enrich_py_sha256: fb37cab9b78dacfdf0271221b2dd17f19ec346ea8e7d9058dbd8e63ce50442fb
- websearch_py_sha256: 6ec2cd3b6e888414af67ef644c51d7ac779ac45381976f2626e6f0506303b31f

The two sha256 values are recorded for provenance only (the adapter checks HEAD).
