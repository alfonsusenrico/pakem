- Deploy bundles leave out Python bytecode caches (`__pycache__`, `*.pyc`): publishing
  imports `deploy/af/contracts.py` from the checkout, so CI bundles carried a generated,
  timestamped file.
