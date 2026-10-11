# Pinned inputs

`native_adapter.py` imports the pipeline from the checkout selected by
`TINYTEST_CORPUS` (`NATIVE_CHECKOUT` to override). It **refuses to run** unless that
checkout's `git rev-parse HEAD` starts with the matching `<corpus>_head` below (an
unfilled TODO also refuses). Values measured on the host by the orchestrator; to
re-measure:

    git -C ~/repos/cyberpulse rev-parse HEAD
    sha256sum ~/repos/cyberpulse/src/cyberpulse/pipeline/enrich.py \
              ~/repos/cyberpulse/src/cyberpulse/pipeline/websearch.py
    git -C ~/repos/rollout rev-parse HEAD
    sha256sum ~/repos/rollout/src/cyberpulse/pipeline/enrich.py \
              ~/repos/rollout/src/cyberpulse/pipeline/websearch.py

## cyberpulse (default corpus)

- cyberpulse_head: 368e2722de832d6e82711b08b5c7c49da9d2c46e
- enrich_py_sha256: fb37cab9b78dacfdf0271221b2dd17f19ec346ea8e7d9058dbd8e63ce50442fb
- websearch_py_sha256: 6ec2cd3b6e888414af67ef644c51d7ac779ac45381976f2626e6f0506303b31f

## rollout (TINYTEST_CORPUS=rollout)

Rollout is a re-skin of the CyberPulse engine and keeps the `cyberpulse` module name, so
the adapter imports `cyberpulse.pipeline.enrich` from this checkout unchanged.

- rollout_head: b8b31276cf6d4b97a945978cb2eaa2fb789c9ac0
- enrich_py_sha256: 223bc4a0bc857cb92909874e7656f95fa1fbfde8fa25df974f7a30a9359fedaa
- websearch_py_sha256: e2b3feec467d31e20c4ba05064c9aabc8ba8ef6145657b1565876c15a6666383

The sha256 values are recorded for provenance only (the adapter checks HEAD).
